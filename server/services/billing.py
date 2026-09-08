"""Provider-neutral order and credit entitlement services.

Payment SDKs are intentionally kept outside this module. A verified provider
callback calls ``mark_order_paid`` exactly once; the existing usage ledger then
consumes the purchased balance alongside the monthly allowance.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_core import User
from db.models_usage import BillingOrder, BillingProduct, BillingWebhookEvent, CreditGrant
from providers.payments import CheckoutResult, ProviderOrderResult, VerifiedPaymentEvent

SUPPORTED_PROVIDERS = {"wechat", "alipay"}
ORDER_STATUSES = {"pending", "paid", "cancelled", "failed", "refund_pending", "refunded", "partially_refunded"}


class BillingError(Exception):
    code = "BILLING_ERROR"


class ProductNotFoundError(BillingError):
    code = "PRODUCT_NOT_FOUND"


class UnsupportedProviderError(BillingError):
    code = "UNSUPPORTED_PROVIDER"


class OrderStateError(BillingError):
    code = "ORDER_STATE_INVALID"


class OrderNotFoundError(BillingError):
    code = "ORDER_NOT_FOUND"


class ProviderPayloadMismatchError(BillingError):
    code = "PAYMENT_PROVIDER_PAYLOAD_MISMATCH"


class RefundNotAvailableError(BillingError):
    code = "REFUND_NOT_AVAILABLE"


class IdempotencyConflictError(BillingError):
    code = "IDEMPOTENCY_CONFLICT"


def new_billing_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def product_snapshot(product: BillingProduct) -> dict:
    return {
        "id": product.id,
        "code": product.code,
        "name": product.name,
        "description": product.description,
        "plan": product.plan,
        "currency": product.currency,
        "amount_minor": product.amount_minor,
        "credits": product.credits,
        "billing_interval": product.billing_interval,
    }


async def create_order(
    db: AsyncSession,
    *,
    user_id: str,
    product_code: str,
    provider: str,
    idempotency_key: str,
) -> BillingOrder:
    provider = provider.strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise UnsupportedProviderError(provider)
    key = idempotency_key.strip()
    if not key:
        raise BillingError("idempotency key is required")
    existing = await db.scalar(
        select(BillingOrder).where(
            BillingOrder.user_id == user_id,
            BillingOrder.idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.provider != provider or (existing.product_snapshot or {}).get("code") != product_code.strip():
            raise IdempotencyConflictError("idempotency key was already used for a different order")
        return existing
    product = await db.scalar(
        select(BillingProduct).where(
            BillingProduct.code == product_code.strip(),
            BillingProduct.is_active.is_(True),
        )
    )
    if product is None:
        raise ProductNotFoundError(product_code)
    if product.amount_minor <= 0:
        raise BillingError("zero-priced products cannot be sent to a payment provider")
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise BillingError("user not found")
    order = BillingOrder(
        id=new_billing_id("ord"),
        user_id=user.id,
        product_id=product.id,
        provider=provider,
        status="pending",
        amount_minor=product.amount_minor,
        currency=product.currency,
        credits=product.credits,
        product_snapshot=product_snapshot(product),
        idempotency_key=key,
    )
    try:
        async with db.begin_nested():
            db.add(order)
            await db.flush()
        return order
    except IntegrityError:
        existing = await db.scalar(
            select(BillingOrder).where(
                BillingOrder.user_id == user_id,
                BillingOrder.idempotency_key == key,
            )
        )
        if existing is None:
            raise
        if existing.provider != provider or (existing.product_snapshot or {}).get("code") != product_code.strip():
            raise IdempotencyConflictError("idempotency key was already used for a different order")
        return existing


async def attach_checkout(db: AsyncSession, *, order_id: str, checkout: CheckoutResult) -> BillingOrder:
    order = await db.scalar(select(BillingOrder).where(BillingOrder.id == order_id).with_for_update())
    if order is None:
        raise OrderNotFoundError(order_id)
    if order.status != "pending":
        raise OrderStateError(f"cannot attach checkout in state {order.status}")
    order.provider_checkout_id = checkout.provider_checkout_id
    order.expires_at = checkout.expires_at
    order.failure_code = None
    await db.flush()
    return order


async def mark_order_paid(
    db: AsyncSession,
    *,
    order_id: str,
    provider_order_id: str | None = None,
) -> BillingOrder:
    order = await db.scalar(select(BillingOrder).where(BillingOrder.id == order_id).with_for_update())
    if order is None:
        raise BillingError("order not found")
    if order.status == "paid":
        if provider_order_id and order.provider_order_id and provider_order_id != order.provider_order_id:
            raise ProviderPayloadMismatchError("paid order is already bound to another provider order")
        return order
    if order.status != "pending":
        raise OrderStateError(f"cannot pay order in state {order.status}")
    if provider_order_id:
        reused = await db.scalar(
            select(BillingOrder).where(
                BillingOrder.provider == order.provider,
                BillingOrder.provider_order_id == provider_order_id,
                BillingOrder.id != order.id,
            )
        )
        if reused is not None:
            raise OrderStateError("provider order id is already bound")
    user = await db.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if user is None:
        raise BillingError("order user not found")
    now = datetime.now(UTC)
    user.purchased_credits_remaining += order.credits
    db.add(
        CreditGrant(
            id=new_billing_id("grant"),
            user_id=user.id,
            order_id=order.id,
            kind="purchase",
            credits=order.credits,
            remaining_credits=order.credits,
            metadata_json={"provider": order.provider, "provider_order_id": provider_order_id},
            created_at=now,
        )
    )
    order.status = "paid"
    order.provider_order_id = provider_order_id
    order.paid_at = now
    await db.flush()
    return order


def _validate_provider_result(order: BillingOrder, result: ProviderOrderResult | VerifiedPaymentEvent) -> None:
    if result.currency != order.currency:
        raise ProviderPayloadMismatchError("provider currency does not match order")
    if result.amount_minor is not None and result.amount_minor != order.amount_minor:
        raise ProviderPayloadMismatchError("provider amount does not match order")


async def begin_refund(db: AsyncSession, *, order_id: str) -> BillingOrder:
    """Reserve all unused purchased credits before requesting a full refund."""
    order = await db.scalar(select(BillingOrder).where(BillingOrder.id == order_id).with_for_update())
    if order is None:
        raise OrderNotFoundError(order_id)
    if order.status == "refund_pending":
        return order
    if order.status != "paid":
        raise OrderStateError(f"cannot refund order in state {order.status}")
    grant = await db.scalar(
        select(CreditGrant).where(CreditGrant.order_id == order.id, CreditGrant.kind == "purchase").with_for_update()
    )
    if grant is None or grant.remaining_credits != order.credits:
        raise RefundNotAvailableError("refund requires all purchased credits from this order to remain unused")
    user = await db.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if user is None or user.purchased_credits_remaining < order.credits:
        raise RefundNotAvailableError("purchased credit balance is no longer refundable")
    grant.remaining_credits = 0
    user.purchased_credits_remaining -= order.credits
    order.status = "refund_pending"
    order.failure_code = None
    await db.flush()
    return order


async def restore_failed_refund(db: AsyncSession, *, order_id: str, failure_code: str) -> BillingOrder:
    order = await db.scalar(select(BillingOrder).where(BillingOrder.id == order_id).with_for_update())
    if order is None:
        raise OrderNotFoundError(order_id)
    if order.status != "refund_pending":
        return order
    grant = await db.scalar(
        select(CreditGrant).where(CreditGrant.order_id == order.id, CreditGrant.kind == "purchase").with_for_update()
    )
    user = await db.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if grant is None or user is None:
        raise BillingError("refund reservation cannot be restored")
    grant.remaining_credits = order.credits
    user.purchased_credits_remaining += order.credits
    order.status = "paid"
    order.failure_code = failure_code[:100]
    await db.flush()
    return order


async def _apply_refund_result(
    db: AsyncSession, *, order: BillingOrder, result: ProviderOrderResult | VerifiedPaymentEvent
) -> BillingOrder:
    refunded = max(0, min(order.amount_minor, result.refunded_amount_minor))
    if result.state == "refunded" and refunded == 0:
        refunded = order.amount_minor
    if refunded < order.refunded_amount_minor:
        raise ProviderPayloadMismatchError("provider refunded amount moved backwards")
    target_credits = (
        order.credits
        if order.amount_minor == 0
        else min(order.credits, (order.credits * refunded + order.amount_minor - 1) // order.amount_minor)
    )
    grant = await db.scalar(
        select(CreditGrant).where(CreditGrant.order_id == order.id, CreditGrant.kind == "purchase").with_for_update()
    )
    user = await db.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if grant is None or user is None:
        raise BillingError("refund entitlement cannot be reconciled")
    settled_credits = int(
        await db.scalar(
            select(func.coalesce(func.sum(CreditGrant.credits), 0)).where(
                CreditGrant.order_id == order.id,
                CreditGrant.kind == "reversal",
            )
        )
        or 0
    )
    additional = max(0, target_credits - settled_credits)
    if order.status == "refund_pending":
        # begin_refund froze the whole grant. Release the portion the provider
        # did not refund, while keeping the confirmed part permanently spent.
        desired_remaining = order.credits - target_credits
        balance_delta = desired_remaining - grant.remaining_credits
        if balance_delta < 0 and user.purchased_credits_remaining < -balance_delta:
            raise RefundNotAvailableError("refunded credits are no longer available")
        grant.remaining_credits = desired_remaining
        user.purchased_credits_remaining += balance_delta
    elif additional:
        if grant.remaining_credits < additional or user.purchased_credits_remaining < additional:
            raise RefundNotAvailableError("refunded credits are no longer available")
        grant.remaining_credits -= additional
        user.purchased_credits_remaining -= additional
    if additional:
        db.add(
            CreditGrant(
                id=new_billing_id("grant"),
                user_id=user.id,
                order_id=order.id,
                kind="reversal",
                credits=additional,
                remaining_credits=0,
                metadata_json={
                    "provider": order.provider,
                    "provider_refund_id": result.provider_refund_id,
                    "amount_minor": refunded,
                },
                created_at=datetime.now(UTC),
            )
        )
    order.refunded_amount_minor = refunded
    order.provider_refund_id = result.provider_refund_id or order.provider_refund_id
    order.status = result.state
    if result.state == "refunded":
        order.refunded_at = datetime.now(UTC)
    order.failure_code = result.failure_code
    return order


async def apply_provider_result(
    db: AsyncSession,
    *,
    order_id: str,
    provider: str,
    result: ProviderOrderResult | VerifiedPaymentEvent,
) -> BillingOrder:
    order = await db.scalar(select(BillingOrder).where(BillingOrder.id == order_id).with_for_update())
    if order is None:
        raise OrderNotFoundError(order_id)
    if order.provider != provider:
        raise ProviderPayloadMismatchError("provider does not match order")
    _validate_provider_result(order, result)
    order.provider_synced_at = datetime.now(UTC)
    if result.provider_order_id and order.provider_order_id and result.provider_order_id != order.provider_order_id:
        raise ProviderPayloadMismatchError("order is already bound to another provider order")
    order.provider_order_id = result.provider_order_id or order.provider_order_id
    if result.state == "paid":
        return await mark_order_paid(db, order_id=order.id, provider_order_id=result.provider_order_id)
    if result.state in {"refunded", "partially_refunded", "refund_pending"}:
        if order.status not in {"paid", "refund_pending", "partially_refunded", "refunded"}:
            raise OrderStateError(f"cannot apply refund to order in state {order.status}")
        return await _apply_refund_result(db, order=order, result=result)
    if result.state == "cancelled" and order.status == "pending":
        return await cancel_order(db, order_id=order.id, reason=result.failure_code)
    if result.state == "failed" and order.status == "pending":
        order.status = "failed"
        order.failure_code = result.failure_code
    await db.flush()
    return order


async def record_webhook_event(
    db: AsyncSession, *, provider: str, event: VerifiedPaymentEvent, payload_hash: str
) -> tuple[BillingWebhookEvent, bool]:
    existing = await db.scalar(
        select(BillingWebhookEvent)
        .where(
            BillingWebhookEvent.provider == provider,
            BillingWebhookEvent.provider_event_id == event.event_id,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise ProviderPayloadMismatchError("provider reused event id with different payload")
        return existing, False
    row = BillingWebhookEvent(
        provider=provider,
        provider_event_id=event.event_id,
        payload_hash=payload_hash,
        status="received",
        payload={**event.safe_payload, "order_id": event.order_id, "state": event.state},
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
        return row, True
    except IntegrityError:
        existing = await db.scalar(
            select(BillingWebhookEvent)
            .where(
                BillingWebhookEvent.provider == provider,
                BillingWebhookEvent.provider_event_id == event.event_id,
            )
            .with_for_update()
        )
        if existing is None:
            raise
        if existing.payload_hash != payload_hash:
            raise ProviderPayloadMismatchError("provider reused event id with different payload")
        return existing, False


async def process_verified_event(
    db: AsyncSession, *, provider: str, event: VerifiedPaymentEvent, payload_hash: str
) -> tuple[BillingOrder, bool]:
    inbox, created = await record_webhook_event(db, provider=provider, event=event, payload_hash=payload_hash)
    if inbox.status == "processed":
        order = await db.get(BillingOrder, event.order_id)
        if order is None:
            raise OrderNotFoundError(event.order_id)
        return order, False
    try:
        order = await apply_provider_result(db, order_id=event.order_id, provider=provider, result=event)
    except BillingError as exc:
        inbox.status = "failed"
        inbox.error_message = f"{exc.code}: {exc}"[:2000]
        inbox.processed_at = datetime.now(UTC)
        await db.flush()
        raise
    inbox.status = "processed"
    inbox.error_message = None
    inbox.processed_at = datetime.now(UTC)
    await db.flush()
    return order, created


async def cancel_order(db: AsyncSession, *, order_id: str, reason: str | None = None) -> BillingOrder:
    order = await db.scalar(select(BillingOrder).where(BillingOrder.id == order_id).with_for_update())
    if order is None:
        raise BillingError("order not found")
    if order.status == "cancelled":
        return order
    if order.status != "pending":
        raise OrderStateError(f"cannot cancel order in state {order.status}")
    order.status = "cancelled"
    order.cancelled_at = datetime.now(UTC)
    order.failure_code = (reason or "cancelled")[:100]
    await db.flush()
    return order


def order_payload(order: BillingOrder) -> dict:
    return {
        "id": order.id,
        "product_code": (order.product_snapshot or {}).get("code"),
        "product_name": (order.product_snapshot or {}).get("name"),
        "provider": order.provider,
        "status": order.status,
        "amount_minor": order.amount_minor,
        "currency": order.currency,
        "credits": order.credits,
        "provider_order_id": order.provider_order_id,
        "provider_refund_id": order.provider_refund_id,
        "refunded_amount_minor": order.refunded_amount_minor,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "refunded_at": order.refunded_at.isoformat() if order.refunded_at else None,
        "provider_synced_at": order.provider_synced_at.isoformat() if order.provider_synced_at else None,
        "expires_at": order.expires_at.isoformat() if order.expires_at else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "failure_code": order.failure_code,
    }


__all__ = [
    "BillingError",
    "IdempotencyConflictError",
    "ORDER_STATUSES",
    "OrderNotFoundError",
    "OrderStateError",
    "ProductNotFoundError",
    "ProviderPayloadMismatchError",
    "RefundNotAvailableError",
    "SUPPORTED_PROVIDERS",
    "UnsupportedProviderError",
    "apply_provider_result",
    "attach_checkout",
    "begin_refund",
    "cancel_order",
    "create_order",
    "mark_order_paid",
    "order_payload",
    "process_verified_event",
    "product_snapshot",
    "restore_failed_refund",
]
