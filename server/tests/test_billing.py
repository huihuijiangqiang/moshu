import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from db.models_core import User
from db.models_usage import BillingOrder, BillingProduct, BillingWebhookEvent, CreditGrant
from main import app
from providers.payments import (
    CheckoutResult,
    PaymentProviderError,
    ProviderOrderResult,
    VerifiedPaymentEvent,
    get_payment_registry,
)
from services.billing import mark_order_paid
from services.usage import reserve_generation, settle_generation


class FakePaymentAdapter:
    name = "wechat"

    def __init__(self):
        self.create_calls = 0
        self.states: dict[str, ProviderOrderResult] = {}
        self.refund_error: PaymentProviderError | None = None

    async def create_checkout(self, *, order_id, description, amount_minor, currency):
        self.create_calls += 1
        self.states[order_id] = ProviderOrderResult(state="pending", amount_minor=amount_minor, currency=currency)
        return CheckoutResult(
            checkout_url=f"weixin://wxpay/test/{order_id}",
            provider_checkout_id=f"weixin://wxpay/test/{order_id}",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )

    async def query_order(self, *, order_id):
        return self.states[order_id]

    async def close_order(self, *, order_id):
        result = ProviderOrderResult(state="cancelled")
        self.states[order_id] = result
        return result

    async def refund_order(self, *, order_id, provider_order_id, amount_minor, reason):
        if self.refund_error:
            raise self.refund_error
        result = ProviderOrderResult(
            state="refunded",
            provider_order_id=provider_order_id,
            provider_refund_id=f"wx-refund-{order_id}",
            amount_minor=amount_minor,
            refunded_amount_minor=amount_minor,
        )
        self.states[order_id] = result
        return result

    async def query_refund(self, *, order_id, provider_refund_id, amount_minor):
        return self.states[order_id]

    async def verify_webhook(self, *, headers, body):
        payload = json.loads(body)
        return VerifiedPaymentEvent(
            event_id=payload["event_id"],
            order_id=payload["order_id"],
            state=payload["state"],
            provider_order_id=payload.get("provider_order_id"),
            provider_refund_id=payload.get("provider_refund_id"),
            amount_minor=payload.get("amount_minor"),
            refunded_amount_minor=payload.get("refunded_amount_minor", 0),
            event_type=payload["state"],
        )


class FakePaymentRegistry:
    def __init__(self, adapter=None):
        self.adapter = adapter or FakePaymentAdapter()

    def readiness(self, provider):
        return {"configured": True, "state": "ready", "missing": []}

    def get(self, provider):
        return self.adapter


def install_fake_payment_registry(registry):
    app.dependency_overrides[get_payment_registry] = lambda: registry


def remove_fake_payment_registry():
    app.dependency_overrides.pop(get_payment_registry, None)


async def test_admin_can_create_and_publish_credit_product(app_client, async_db_session, make_user, auth_headers):
    async_db_session.add(make_user("billing_product_admin", system_role="super_admin"))
    await async_db_session.commit()

    response = await app_client.post(
        "/billing/admin/products",
        json={
            "code": "author-100k",
            "name": "作者积分包",
            "description": "一次性充值",
            "amount_minor": 990,
            "credits": 100_000,
        },
        headers=auth_headers("billing_product_admin"),
    )
    assert response.status_code == 200
    assert response.json()["currency"] == "CNY"
    assert response.json()["credits"] == 100_000

    products = await app_client.get("/billing/products")
    assert products.status_code == 200
    assert products.json()[0]["code"] == "author-100k"

    duplicate = await app_client.post(
        "/billing/admin/products",
        json={"code": "author-100k", "name": "重复", "amount_minor": 1, "credits": 1},
        headers=auth_headers("billing_product_admin"),
    )
    assert duplicate.status_code == 409


async def test_order_creation_is_idempotent_and_paid_order_grants_purchased_credits(
    app_client, async_db_session, make_user, auth_headers
):
    async_db_session.add_all(
        [
            make_user("buyer", quota_remaining=0, quota_total=0),
            BillingProduct(
                id="prd_pack",
                code="pack-100",
                name="100积分",
                currency="CNY",
                amount_minor=100,
                credits=100,
                provider_prices={},
            ),
        ]
    )
    await async_db_session.commit()

    payload = {
        "product_code": "pack-100",
        "provider": "wechat",
        "idempotency_key": "buyer-pack-100-1",
    }
    registry = FakePaymentRegistry()
    install_fake_payment_registry(registry)
    first = await app_client.post("/billing/orders", json=payload, headers=auth_headers("buyer"))
    second = await app_client.post("/billing/orders", json=payload, headers=auth_headers("buyer"))
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["status"] == "pending"
    assert first.json()["payment"]["state"] == "checkout_ready"
    assert registry.adapter.create_calls == 1

    conflict = await app_client.post(
        "/billing/orders",
        json={**payload, "provider": "alipay"},
        headers=auth_headers("buyer"),
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    remove_fake_payment_registry()

    order = await async_db_session.scalar(select(BillingOrder).where(BillingOrder.id == first.json()["id"]))
    assert order is not None
    await mark_order_paid(async_db_session, order_id=order.id, provider_order_id="wx-1")
    await async_db_session.commit()

    balance = await app_client.get("/billing/balance", headers=auth_headers("buyer"))
    assert balance.json()["purchased_remaining"] == 100
    grant = await async_db_session.scalar(select(CreditGrant).where(CreditGrant.order_id == order.id))
    assert grant is not None
    assert grant.remaining_credits == 100

    reservation = await reserve_generation(
        async_db_session,
        user_id="buyer",
        project_id=None,
        feature="generate_inline",
        model="test-model",
        model_tier="basic",
        prompt_tokens=0,
        target_words=100,
    )
    await settle_generation(
        async_db_session,
        reservation,
        run_id=None,
        prompt_tokens=0,
        cached_tokens=0,
        completion_tokens=1,
    )
    await async_db_session.commit()
    balance_after = await app_client.get("/billing/balance", headers=auth_headers("buyer"))
    assert balance_after.json()["purchased_remaining"] == 99
    await async_db_session.refresh(grant)
    assert grant.remaining_credits == 99


async def test_raw_provider_webhooks_are_closed_until_signature_adapter_is_configured(app_client):
    response = await app_client.post("/billing/webhooks/wechat", json={"event": "paid"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PAYMENT_PROVIDER_NOT_CONFIGURED"


async def test_verified_webhook_is_idempotent_and_order_can_be_refunded(
    app_client, async_db_session, make_user, auth_headers
):
    async_db_session.add_all(
        [
            make_user("webhook_buyer", quota_remaining=0, quota_total=0),
            BillingProduct(
                id="prd_webhook",
                code="webhook-100",
                name="100积分",
                currency="CNY",
                amount_minor=100,
                credits=100,
                provider_prices={},
            ),
        ]
    )
    await async_db_session.commit()
    registry = FakePaymentRegistry()
    install_fake_payment_registry(registry)
    try:
        created = await app_client.post(
            "/billing/orders",
            json={"product_code": "webhook-100", "provider": "wechat", "idempotency_key": "webhook-order-1"},
            headers=auth_headers("webhook_buyer"),
        )
        assert created.status_code == 200
        order_id = created.json()["id"]
        event = {
            "event_id": "wechat-event-1",
            "order_id": order_id,
            "state": "paid",
            "provider_order_id": "wx-transaction-1",
            "amount_minor": 100,
        }
        first = await app_client.post("/billing/webhooks/wechat", json=event)
        second = await app_client.post("/billing/webhooks/wechat", json=event)
        assert first.status_code == second.status_code == 200
        assert first.json()["code"] == "SUCCESS"

        order = await app_client.get(f"/billing/orders/{order_id}", headers=auth_headers("webhook_buyer"))
        assert order.json()["status"] == "paid"
        assert order.json()["provider_order_id"] == "wx-transaction-1"
        balance = await app_client.get("/billing/balance", headers=auth_headers("webhook_buyer"))
        assert balance.json()["purchased_remaining"] == 100
        assert await async_db_session.scalar(select(func.count(CreditGrant.id))) == 1
        assert await async_db_session.scalar(select(func.count(BillingWebhookEvent.id))) == 1

        refunded = await app_client.post(
            f"/billing/orders/{order_id}/refund",
            json={"reason": "未使用"},
            headers=auth_headers("webhook_buyer"),
        )
        assert refunded.status_code == 200
        assert refunded.json()["status"] == "refunded"
        assert refunded.json()["refunded_amount_minor"] == 100
        balance = await app_client.get("/billing/balance", headers=auth_headers("webhook_buyer"))
        assert balance.json()["purchased_remaining"] == 0
        reversals = (
            (
                await async_db_session.execute(
                    select(CreditGrant).where(CreditGrant.order_id == order_id, CreditGrant.kind == "reversal")
                )
            )
            .scalars()
            .all()
        )
        assert len(reversals) == 1
        assert reversals[0].credits == 100
    finally:
        remove_fake_payment_registry()


async def test_order_close_and_sync_use_provider_state(app_client, async_db_session, make_user, auth_headers):
    async_db_session.add_all(
        [
            make_user("sync_buyer"),
            BillingProduct(
                id="prd_sync",
                code="sync-10",
                name="10积分",
                currency="CNY",
                amount_minor=10,
                credits=10,
                provider_prices={},
            ),
        ]
    )
    await async_db_session.commit()
    registry = FakePaymentRegistry()
    install_fake_payment_registry(registry)
    try:
        created = await app_client.post(
            "/billing/orders",
            json={"product_code": "sync-10", "provider": "wechat", "idempotency_key": "sync-order-key"},
            headers=auth_headers("sync_buyer"),
        )
        order_id = created.json()["id"]
        registry.adapter.states[order_id] = ProviderOrderResult(
            state="paid", provider_order_id="wx-sync-paid", amount_minor=10
        )
        synced = await app_client.post(f"/billing/orders/{order_id}/sync", headers=auth_headers("sync_buyer"))
        assert synced.status_code == 200
        assert synced.json()["status"] == "paid"

        other = await app_client.post(
            "/billing/orders",
            json={"product_code": "sync-10", "provider": "wechat", "idempotency_key": "close-order-key"},
            headers=auth_headers("sync_buyer"),
        )
        closed = await app_client.post(
            f"/billing/orders/{other.json()['id']}/close", headers=auth_headers("sync_buyer")
        )
        assert closed.status_code == 200
        assert closed.json()["status"] == "cancelled"
    finally:
        remove_fake_payment_registry()


async def test_refund_rejects_consumed_credits_and_ambiguous_failure_stays_pending(
    app_client, async_db_session, make_user, auth_headers
):
    async_db_session.add_all(
        [
            make_user("refund_buyer", quota_remaining=0, quota_total=0),
            BillingProduct(
                id="prd_refund",
                code="refund-10",
                name="10积分",
                currency="CNY",
                amount_minor=10,
                credits=10,
                provider_prices={},
            ),
        ]
    )
    await async_db_session.commit()
    registry = FakePaymentRegistry()
    install_fake_payment_registry(registry)
    try:
        created = await app_client.post(
            "/billing/orders",
            json={"product_code": "refund-10", "provider": "wechat", "idempotency_key": "refund-order-1"},
            headers=auth_headers("refund_buyer"),
        )
        order_id = created.json()["id"]
        await mark_order_paid(async_db_session, order_id=order_id, provider_order_id="wx-refund-source")
        await async_db_session.commit()
        grant = await async_db_session.scalar(select(CreditGrant).where(CreditGrant.order_id == order_id))
        grant.remaining_credits = 9
        user = await async_db_session.get(User, "refund_buyer")
        user.purchased_credits_remaining = 9
        await async_db_session.commit()
        rejected = await app_client.post(
            f"/billing/orders/{order_id}/refund", json={}, headers=auth_headers("refund_buyer")
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "REFUND_NOT_AVAILABLE"

        grant.remaining_credits = 10
        user.purchased_credits_remaining = 10
        await async_db_session.commit()
        registry.adapter.refund_error = PaymentProviderError("timeout", retryable=True)
        ambiguous = await app_client.post(
            f"/billing/orders/{order_id}/refund", json={}, headers=auth_headers("refund_buyer")
        )
        assert ambiguous.status_code == 502
        await async_db_session.refresh(grant)
        await async_db_session.refresh(user)
        order = await async_db_session.get(BillingOrder, order_id)
        assert order.status == "refund_pending"
        assert grant.remaining_credits == 0
        assert user.purchased_credits_remaining == 0
    finally:
        remove_fake_payment_registry()
