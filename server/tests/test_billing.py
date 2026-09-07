from sqlalchemy import select

from db.models_usage import BillingOrder, BillingProduct, CreditGrant
from services.billing import mark_order_paid
from services.usage import reserve_generation, settle_generation


async def test_admin_can_create_and_publish_credit_product(
    app_client, async_db_session, make_user, auth_headers
):
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
    async_db_session.add_all([
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
    ])
    await async_db_session.commit()

    payload = {
        "product_code": "pack-100",
        "provider": "wechat",
        "idempotency_key": "buyer-pack-100-1",
    }
    first = await app_client.post("/billing/orders", json=payload, headers=auth_headers("buyer"))
    second = await app_client.post("/billing/orders", json=payload, headers=auth_headers("buyer"))
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["status"] == "pending"

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
