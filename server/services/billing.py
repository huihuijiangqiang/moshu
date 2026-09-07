"""Provider-neutral order and credit entitlement services.

Payment SDKs are intentionally kept outside this module. A verified provider
callback calls ``mark_order_paid`` exactly once; the existing usage ledger then
consumes the purchased balance alongside the monthly allowance.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_core import User
from db.models_usage import BillingOrder, BillingProduct, CreditGrant

SUPPORTED_PROVIDERS = {"wechat", "alipay"}
ORDER_STATUSES = {"pending", "paid", "cancelled", "failed", "refunded", "partially_refunded"}


class BillingError(Exception):
    code = "BILLING_ERROR"


class ProductNotFoundError(BillingError):
    code = "PRODUCT_NOT_FOUND"


class UnsupportedProviderError(BillingError):
    code = "UNSUPPORTED_PROVIDER"


class OrderStateError(BillingError):
    code = "ORDER_STATE_INVALID"


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
        return existing
    product = await db.scalar(
        select(BillingProduct).where(
            BillingProduct.code == product_code.strip(),
            BillingProduct.is_active.is_(True),
        )
    )
    if product is None:
        raise ProductNotFoundError(product_code)
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
    db.add(order)
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
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "failure_code": order.failure_code,
    }


__all__ = [
    "BillingError",
    "ORDER_STATUSES",
    "ProductNotFoundError",
    "SUPPORTED_PROVIDERS",
    "UnsupportedProviderError",
    "OrderStateError",
    "cancel_order",
    "create_order",
    "mark_order_paid",
    "order_payload",
    "product_snapshot",
]
