"""Authentication endpoint tests."""

from sqlalchemy import select

from api.auth import hash_password, verify_password
from config import settings
from db.models_admin import AdminAuditLog
from db.models_core import User


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first != second
    assert verify_password("correct horse battery staple", first) is True
    assert verify_password("wrong password", first) is False


async def test_register_login_refresh_and_me(app_client):
    register = await app_client.post(
        "/auth/register",
        json={"name": "新作者", "email": "Author@Example.Test", "password": "long-enough-password"},
    )

    assert register.status_code == 201
    registered = register.json()
    assert registered["user"]["email"] == "author@example.test"
    assert registered["access_token"] != registered["refresh_token"]

    duplicate = await app_client.post(
        "/auth/register",
        json={"name": "另一个名字", "email": "author@example.test", "password": "long-enough-password"},
    )
    assert duplicate.status_code == 409

    invalid = await app_client.post(
        "/auth/login",
        json={"email": "author@example.test", "password": "not-the-password"},
    )
    assert invalid.status_code == 401

    login = await app_client.post(
        "/auth/login",
        json={"email": "author@example.test", "password": "long-enough-password"},
    )
    assert login.status_code == 200
    session = login.json()

    me = await app_client.get("/auth/me", headers={"Authorization": f"Bearer {session['access_token']}"})
    assert me.status_code == 200
    assert me.json()["name"] == "新作者"

    refresh = await app_client.post("/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert refresh.status_code == 200
    assert refresh.json()["access_token"] != session["access_token"]


async def test_refresh_token_cannot_call_access_endpoint(app_client):
    register = await app_client.post(
        "/auth/register",
        json={"name": "作者", "email": "refresh@example.test", "password": "long-enough-password"},
    )
    token = register.json()["refresh_token"]

    response = await app_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_bootstrap_creates_first_super_admin_and_audit(app_client, async_db_session, monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_token", "local-bootstrap-token")

    response = await app_client.post(
        "/auth/bootstrap",
        json={
            "name": "首席作者",
            "email": "Owner@Example.Test",
            "password": "long-enough-password",
            "bootstrap_token": "local-bootstrap-token",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["user"]["email"] == "owner@example.test"
    assert payload["user"]["system_role"] == "super_admin"
    assert payload["user"]["plan"] == "studio"

    user = await async_db_session.scalar(select(User).where(User.email == "owner@example.test"))
    assert user is not None
    audit = await async_db_session.scalar(
        select(AdminAuditLog).where(AdminAuditLog.action == "auth.bootstrap")
    )
    assert audit is not None
    assert audit.actor_id is None
    assert audit.target_id == user.id
    assert audit.detail["method"] == "environment_token"
    assert "bootstrap_token" not in audit.detail
    assert "local-bootstrap-token" not in str(audit.detail)

    repeated = await app_client.post(
        "/auth/bootstrap",
        json={
            "name": "另一个管理员",
            "email": "second@example.test",
            "password": "long-enough-password",
            "bootstrap_token": "local-bootstrap-token",
        },
    )
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "BOOTSTRAP_ALREADY_COMPLETED"


async def test_bootstrap_rejects_wrong_or_unconfigured_token(app_client, monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_token", "local-bootstrap-token")
    wrong = await app_client.post(
        "/auth/bootstrap",
        json={
            "name": "攻击者",
            "email": "attacker@example.test",
            "password": "long-enough-password",
            "bootstrap_token": "wrong-bootstrap-token",
        },
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "INVALID_BOOTSTRAP_TOKEN"

    monkeypatch.setattr(settings, "bootstrap_token", None)
    unavailable = await app_client.post(
        "/auth/bootstrap",
        json={
            "name": "作者",
            "email": "author@example.test",
            "password": "long-enough-password",
            "bootstrap_token": "local-bootstrap-token",
        },
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "BOOTSTRAP_NOT_CONFIGURED"


async def test_registration_requires_bootstrap_when_token_is_configured(
    app_client, monkeypatch
):
    monkeypatch.setattr(settings, "bootstrap_token", "local-bootstrap-token")

    blocked = await app_client.post(
        "/auth/register",
        json={
            "name": "普通作者",
            "email": "author@example.test",
            "password": "long-enough-password",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "BOOTSTRAP_REQUIRED"

    first = await app_client.post(
        "/auth/bootstrap",
        json={
            "name": "系统管理员",
            "email": "admin@example.test",
            "password": "long-enough-password",
            "bootstrap_token": "local-bootstrap-token",
        },
    )
    assert first.status_code == 201

    allowed = await app_client.post(
        "/auth/register",
        json={
            "name": "普通作者",
            "email": "author@example.test",
            "password": "long-enough-password",
        },
    )
    assert allowed.status_code == 201
    assert allowed.json()["user"]["system_role"] == "user"
