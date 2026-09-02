"""Administrator access, configuration, and audit behavior."""

from sqlalchemy import func, select

from db.models_admin import AdminAuditLog
from db.models_core import User


async def test_regular_user_cannot_open_admin_api(
    app_client, async_db_session, make_user, auth_headers
):
    async_db_session.add(make_user("regular"))
    await async_db_session.commit()

    response = await app_client.get("/admin/overview", headers=auth_headers("regular"))

    assert response.status_code == 403


async def test_admin_overview_returns_real_counts(
    app_client, async_db_session, make_user, make_project, auth_headers
):
    async_db_session.add_all([
        make_user("overview_admin", system_role="admin"),
        make_user("overview_user"),
    ])
    await async_db_session.flush()
    async_db_session.add(make_project("overview_project", owner_id="overview_user"))
    await async_db_session.commit()

    response = await app_client.get(
        "/admin/overview", headers=auth_headers("overview_admin")
    )

    assert response.status_code == 200
    assert response.json() == {
        "users": 2,
        "projects": 1,
        "active_consistency_runs": 0,
        "open_guard_issues": 0,
        "active_sessions": 0,
    }


async def test_super_admin_can_update_user_and_audit_is_written(
    app_client, async_db_session, make_user, auth_headers
):
    admin = make_user("root_admin", system_role="super_admin")
    target = make_user("target_user")
    async_db_session.add_all([admin, target])
    await async_db_session.commit()

    response = await app_client.patch(
        "/admin/users/target_user",
        json={"plan": "author", "quota_remaining": 2500, "quota_total": 5000},
        headers=auth_headers("root_admin"),
    )

    assert response.status_code == 200
    assert response.json()["plan"] == "author"
    assert response.json()["quota_remaining"] == 2500
    assert await async_db_session.scalar(select(func.count(AdminAuditLog.id))) == 1


async def test_admin_cannot_promote_system_roles(
    app_client, async_db_session, make_user, auth_headers
):
    async_db_session.add_all([
        make_user("plain_admin", system_role="admin"),
        make_user("target_user"),
    ])
    await async_db_session.commit()

    response = await app_client.patch(
        "/admin/users/target_user",
        json={"system_role": "admin"},
        headers=auth_headers("plain_admin"),
    )

    assert response.status_code == 403


async def test_admin_settings_control_new_account_defaults(
    app_client, async_db_session, make_user, auth_headers
):
    async_db_session.add(make_user("settings_admin", system_role="super_admin"))
    await async_db_session.commit()
    headers = auth_headers("settings_admin")

    updated = await app_client.patch(
        "/admin/settings",
        json={"registration_enabled": True, "default_plan": "author", "default_monthly_quota": 1200},
        headers=headers,
    )
    assert updated.status_code == 200

    registered = await app_client.post(
        "/auth/register",
        json={"name": "Default", "email": "default@example.test", "password": "password-123"},
    )
    assert registered.status_code == 201
    created = await async_db_session.scalar(select(User).where(User.email == "default@example.test"))
    assert created is not None
    assert created.plan == "author"
    assert created.quota_remaining == 1200


async def test_admin_can_disable_public_registration(
    app_client, async_db_session, make_user, auth_headers
):
    async_db_session.add(make_user("registration_admin", system_role="super_admin"))
    await async_db_session.commit()

    updated = await app_client.patch(
        "/admin/settings",
        json={"registration_enabled": False},
        headers=auth_headers("registration_admin"),
    )
    assert updated.status_code == 200

    registered = await app_client.post(
        "/auth/register",
        json={"name": "Blocked", "email": "blocked@example.test", "password": "password-123"},
    )
    assert registered.status_code == 403
    assert registered.json()["detail"]["code"] == "REGISTRATION_DISABLED"
