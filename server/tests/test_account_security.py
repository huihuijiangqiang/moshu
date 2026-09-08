"""Account security contract tests."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from api.auth import _token_digest, hash_password
from db.models_auth_security import PasswordResetToken


async def _login(client, email: str, password: str) -> dict:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_sessions_are_isolated_and_single_revoke_invalidates_access(
    app_client, async_db_session, make_user
):
    user = make_user("security-a", email="security-a@example.test", password_hash=hash_password("old-pass-123"))
    other = make_user("security-b", email="security-b@example.test", password_hash=hash_password("other-pass-123"))
    async_db_session.add_all([user, other])
    await async_db_session.commit()

    first = await _login(app_client, user.email, "old-pass-123")
    second = await _login(app_client, user.email, "old-pass-123")
    headers = {"Authorization": f"Bearer {first['access_token']}"}
    listed = await app_client.get("/auth/sessions", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 2
    assert sum(row["current"] for row in rows) == 1
    assert all("refresh" not in row and "token" not in row for row in rows)

    foreign = await _login(app_client, other.email, "other-pass-123")
    foreign_headers = {"Authorization": f"Bearer {foreign['access_token']}"}
    assert (await app_client.delete(f"/auth/sessions/{rows[0]['id']}", headers=foreign_headers)).status_code == 404

    target = next(row for row in rows if not row["current"])
    revoked = await app_client.delete(f"/auth/sessions/{target['id']}", headers=headers)
    assert revoked.status_code == 204
    second_check = await app_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"}
    )
    assert second_check.status_code == 401


@pytest.mark.asyncio
async def test_change_password_keeps_current_session_and_revokes_others(
    app_client, async_db_session, make_user
):
    user = make_user("password-a", email="password-a@example.test", password_hash=hash_password("old-pass-123"))
    async_db_session.add(user)
    await async_db_session.commit()
    first = await _login(app_client, user.email, "old-pass-123")
    second = await _login(app_client, user.email, "old-pass-123")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}

    changed = await app_client.post(
        "/auth/password/change",
        headers=first_headers,
        json={"current_password": "old-pass-123", "new_password": "new-pass-456"},
    )
    assert changed.status_code == 204
    assert (await app_client.get("/auth/me", headers=first_headers)).status_code == 200
    assert (
        await app_client.get("/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"})
    ).status_code == 401
    assert (await _login(app_client, user.email, "new-pass-456"))["user"]["id"] == user.id
    assert (await app_client.post(
        "/auth/password/change",
        headers=first_headers,
        json={"current_password": "wrong-pass", "new_password": "another-pass"},
    )).status_code == 400


@pytest.mark.asyncio
async def test_reset_request_is_generic_and_token_is_hashed_and_one_time(
    app_client, async_db_session, make_user, monkeypatch
):
    user = make_user("reset-a", email="reset-a@example.test", password_hash=hash_password("old-pass-123"))
    async_db_session.add(user)
    await async_db_session.commit()
    user_id = user.id
    session = await _login(app_client, user.email, "old-pass-123")
    delivered: dict[str, str] = {}

    async def capture(email: str, token: str, expires_at: datetime):
        delivered["email"] = email
        delivered["token"] = token

    import api.auth as auth_module

    monkeypatch.setattr(auth_module, "_deliver_password_reset_token", capture)
    known = await app_client.post("/auth/password-reset/request", json={"email": user.email})
    unknown = await app_client.post("/auth/password-reset/request", json={"email": "nobody@example.test"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == {"accepted": True}
    token = delivered["token"]
    row = await async_db_session.scalar(select(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
    assert row is not None
    assert row.token_hash == _token_digest(token)
    assert row.token_hash != token
    assert token not in known.text

    confirmed = await app_client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "reset-pass-789"},
    )
    assert confirmed.status_code == 204
    assert (
        await app_client.post(
            "/auth/password-reset/confirm",
            json={"token": token, "new_password": "reset-pass-999"},
        )
    ).status_code == 400
    assert (
        await app_client.get("/auth/me", headers={"Authorization": f"Bearer {session['access_token']}"})
    ).status_code == 401
    assert (await _login(app_client, "reset-a@example.test", "reset-pass-789"))["user"]["id"] == user_id


@pytest.mark.asyncio
async def test_expired_reset_token_is_rejected(app_client, async_db_session, make_user):
    user = make_user("reset-expired", email="reset-expired@example.test", password_hash=hash_password("old-pass-123"))
    async_db_session.add(user)
    await async_db_session.flush()
    async_db_session.add(
        PasswordResetToken(
            id="prt_expired",
            user_id=user.id,
            token_hash=_token_digest("expired-token-value-123456"),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    await async_db_session.commit()
    response = await app_client.post(
        "/auth/password-reset/confirm",
        json={"token": "expired-token-value-123456", "new_password": "new-pass-456"},
    )
    assert response.status_code == 400
