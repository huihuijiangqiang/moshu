"""Credit products and mainland payment-provider workflows."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, require_admin
from db.models_admin import AdminAuditLog
from db.models_core import User
from db.models_usage import BillingOrder, BillingProduct
from db.session import get_db
from providers.payments import (
    PaymentAdapterRegistry,
    PaymentProviderError,
    PaymentProviderNotConfiguredError,
    PaymentSignatureError,
    get_payment_registry,
)
from services.billing import (
    SUPPORTED_PROVIDERS,
    BillingError,
    OrderNotFoundError,
    ProductNotFoundError,
    RefundNotAvailableError,
    UnsupportedProviderError,
    apply_provider_result,
    attach_checkout,
    begin_refund,
    create_order,
    order_payload,
    process_verified_event,
    restore_failed_refund,
)

router = APIRouter()


class OrderCreate(BaseModel):
    product_code: str = Field(min_length=1, max_length=64)
    provider: Literal["wechat", "alipay"]
    idempotency_key: str = Field(min_length=8, max_length=100)


class RefundCreate(BaseModel):
    reason: str | None = Field(default=None, max_length=256)


class ProductCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    plan: str | None = Field(default=None, max_length=50)
    currency: Literal["CNY"] = "CNY"
    amount_minor: int = Field(ge=0, le=100_000_000)
    credits: int = Field(gt=0, le=100_000_000)
    billing_interval: Literal["one_time", "month", "year"] = "one_time"
    sort_order: int = Field(default=0, ge=0, le=100_000)
    is_active: bool = True


class ProductPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    plan: str | None = Field(default=None, max_length=50)
    amount_minor: int | None = Field(default=None, ge=0, le=100_000_000)
    credits: int | None = Field(default=None, gt=0, le=100_000_000)
    billing_interval: Literal["one_time", "month", "year"] | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None


def _product_payload(product: BillingProduct) -> dict:
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
        "is_active": product.is_active,
        "sort_order": product.sort_order,
    }


def _billing_http_error(exc: BillingError) -> HTTPException:
    status = 404 if isinstance(exc, (OrderNotFoundError, ProductNotFoundError)) else 409
    if isinstance(exc, (UnsupportedProviderError, RefundNotAvailableError)):
        status = 422
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


def _provider_http_error(exc: PaymentProviderError) -> HTTPException:
    status = 503 if isinstance(exc, PaymentProviderNotConfiguredError) else (502 if exc.retryable else 422)
    return HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": str(exc), "retryable": exc.retryable},
    )


async def _owned_order(db: AsyncSession, *, order_id: str, user_id: str) -> BillingOrder:
    order = await db.scalar(select(BillingOrder).where(BillingOrder.id == order_id, BillingOrder.user_id == user_id))
    if order is None:
        raise HTTPException(status_code=404, detail={"code": "ORDER_NOT_FOUND"})
    return order


@router.get("/products")
async def list_products(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(BillingProduct)
                .where(BillingProduct.is_active.is_(True))
                .order_by(BillingProduct.sort_order, BillingProduct.amount_minor, BillingProduct.id)
            )
        )
        .scalars()
        .all()
    )
    return [_product_payload(row) for row in rows]


@router.get("/status")
async def payment_status(
    _user: User = Depends(get_current_user),
    registry: PaymentAdapterRegistry = Depends(get_payment_registry),
) -> dict:
    """Expose readiness and missing variable names, never secret values or paths."""
    return {
        "currency": "CNY",
        "providers": {
            "wechat": registry.readiness("wechat"),
            "alipay": registry.readiness("alipay"),
        },
    }


@router.get("/balance")
async def credit_balance(user: User = Depends(get_current_user)) -> dict:
    return {
        "monthly_remaining": user.quota_remaining,
        "monthly_total": user.quota_total,
        "purchased_remaining": user.purchased_credits_remaining,
        "available": user.quota_remaining + user.purchased_credits_remaining,
        "resets_at": user.quota_resets_at.isoformat() if user.quota_resets_at else None,
    }


@router.post("/orders")
async def create_billing_order(
    request: OrderCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    registry: PaymentAdapterRegistry = Depends(get_payment_registry),
) -> dict:
    try:
        adapter = registry.get(request.provider)
        order = await create_order(
            db,
            user_id=user.id,
            product_code=request.product_code,
            provider=request.provider,
            idempotency_key=request.idempotency_key,
        )
        await db.commit()
    except PaymentProviderError as exc:
        raise _provider_http_error(exc) from exc
    except BillingError as exc:
        await db.rollback()
        raise _billing_http_error(exc) from exc
    if order.provider_checkout_id and order.status == "pending":
        return {
            **order_payload(order),
            "payment": {
                "provider": order.provider,
                "checkout_url": order.provider_checkout_id,
                "state": "checkout_ready",
            },
        }
    try:
        checkout = await adapter.create_checkout(
            order_id=order.id,
            description=(order.product_snapshot or {}).get("name") or "墨枢积分",
            amount_minor=order.amount_minor,
            currency=order.currency,
        )
        order = await attach_checkout(db, order_id=order.id, checkout=checkout)
        await db.commit()
    except PaymentProviderError as exc:
        pending = await db.get(BillingOrder, order.id)
        if pending is not None and pending.status == "pending":
            pending.failure_code = exc.code
            await db.commit()
        raise _provider_http_error(exc) from exc
    return {
        **order_payload(order),
        "payment": {
            "provider": order.provider,
            "checkout_url": checkout.checkout_url,
            "state": "checkout_ready",
        },
    }


@router.get("/orders")
async def list_orders(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(BillingOrder)
                .where(BillingOrder.user_id == user.id)
                .order_by(BillingOrder.created_at.desc(), BillingOrder.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [order_payload(row) for row in rows]


@router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return order_payload(await _owned_order(db, order_id=order_id, user_id=user.id))


@router.post("/orders/{order_id}/sync")
async def sync_order(
    order_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    registry: PaymentAdapterRegistry = Depends(get_payment_registry),
) -> dict:
    order = await _owned_order(db, order_id=order_id, user_id=user.id)
    try:
        adapter = registry.get(order.provider)
        result = (
            await adapter.query_refund(
                order_id=order.id,
                provider_refund_id=order.provider_refund_id,
                amount_minor=order.amount_minor,
            )
            if order.status == "refund_pending"
            else await adapter.query_order(order_id=order.id)
        )
        order = await apply_provider_result(db, order_id=order.id, provider=order.provider, result=result)
        await db.commit()
    except PaymentProviderError as exc:
        raise _provider_http_error(exc) from exc
    except BillingError as exc:
        await db.rollback()
        raise _billing_http_error(exc) from exc
    return order_payload(order)


@router.post("/orders/{order_id}/close")
async def close_billing_order(
    order_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    registry: PaymentAdapterRegistry = Depends(get_payment_registry),
) -> dict:
    order = await _owned_order(db, order_id=order_id, user_id=user.id)
    if order.status == "cancelled":
        return order_payload(order)
    if order.status != "pending":
        raise HTTPException(status_code=409, detail={"code": "ORDER_STATE_INVALID"})
    try:
        adapter = registry.get(order.provider)
        result = await adapter.close_order(order_id=order.id)
        order = await apply_provider_result(db, order_id=order.id, provider=order.provider, result=result)
        await db.commit()
    except PaymentProviderError as exc:
        raise _provider_http_error(exc) from exc
    except BillingError as exc:
        await db.rollback()
        raise _billing_http_error(exc) from exc
    return order_payload(order)


@router.post("/orders/{order_id}/refund")
async def refund_billing_order(
    order_id: str,
    request: RefundCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    registry: PaymentAdapterRegistry = Depends(get_payment_registry),
) -> dict:
    order = await _owned_order(db, order_id=order_id, user_id=user.id)
    try:
        adapter = registry.get(order.provider)
        order = await begin_refund(db, order_id=order.id)
        await db.commit()
        result = await adapter.refund_order(
            order_id=order.id,
            provider_order_id=order.provider_order_id,
            amount_minor=order.amount_minor,
            reason=request.reason,
        )
        order = await apply_provider_result(db, order_id=order.id, provider=order.provider, result=result)
        await db.commit()
    except PaymentProviderError as exc:
        if not exc.retryable:
            await restore_failed_refund(db, order_id=order.id, failure_code=exc.code)
            await db.commit()
        raise _provider_http_error(exc) from exc
    except BillingError as exc:
        await db.rollback()
        raise _billing_http_error(exc) from exc
    return order_payload(order)


@router.get("/admin/products")
async def admin_list_products(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = (
        (await db.execute(select(BillingProduct).order_by(BillingProduct.sort_order, BillingProduct.id)))
        .scalars()
        .all()
    )
    return [_product_payload(row) for row in rows]


@router.post("/admin/products")
async def admin_create_product(
    request: ProductCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    existing = await db.scalar(select(BillingProduct).where(BillingProduct.code == request.code.strip()))
    if existing is not None:
        raise HTTPException(status_code=409, detail={"code": "PRODUCT_CODE_EXISTS"})
    product = BillingProduct(
        id=f"prd_{secrets.token_hex(12)}",
        code=request.code.strip(),
        name=request.name.strip(),
        description=request.description,
        plan=request.plan,
        currency=request.currency,
        amount_minor=request.amount_minor,
        credits=request.credits,
        billing_interval=request.billing_interval,
        provider_prices={},
        sort_order=request.sort_order,
        is_active=request.is_active,
    )
    db.add(product)
    db.add(
        AdminAuditLog(
            actor_id=admin.id,
            action="billing.product.create",
            target_type="billing_product",
            target_id=product.id,
            detail={"code": product.code, "amount_minor": product.amount_minor, "credits": product.credits},
            created_at=datetime.now(UTC),
        )
    )
    await db.commit()
    await db.refresh(product)
    return _product_payload(product)


@router.patch("/admin/products/{product_id}")
async def admin_update_product(
    product_id: str,
    request: ProductPatch,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    product = await db.get(BillingProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail={"code": "PRODUCT_NOT_FOUND"})
    detail = request.model_dump(exclude_none=True)
    for key, value in detail.items():
        setattr(product, key, value)
    db.add(
        AdminAuditLog(
            actor_id=admin.id,
            action="billing.product.update",
            target_type="billing_product",
            target_id=product.id,
            detail=detail,
            created_at=datetime.now(UTC),
        )
    )
    await db.commit()
    await db.refresh(product)
    return _product_payload(product)


@router.post("/webhooks/{provider}")
async def provider_webhook(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    registry: PaymentAdapterRegistry = Depends(get_payment_registry),
) -> Response:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail={"code": "UNSUPPORTED_PROVIDER"})
    body = await request.body()
    try:
        adapter = registry.get(provider)
        event = await adapter.verify_webhook(headers=dict(request.headers), body=body)
        await process_verified_event(
            db,
            provider=provider,
            event=event,
            payload_hash=hashlib.sha256(body).hexdigest(),
        )
        await db.commit()
    except PaymentSignatureError as exc:
        raise HTTPException(status_code=401, detail={"code": exc.code}) from exc
    except PaymentProviderError as exc:
        raise _provider_http_error(exc) from exc
    except BillingError as exc:
        await db.commit()
        raise _billing_http_error(exc) from exc
    if provider == "alipay":
        return Response(content="success", media_type="text/plain")
    return Response(content='{"code":"SUCCESS","message":"成功"}', media_type="application/json")
