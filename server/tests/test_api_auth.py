"""
认证与项目访问控制的行为测试。

直接调用 verify_project_access（覆盖 owner / org 成员 / 拒绝 / 不存在），
并通过真实 ASGI 客户端验证端点级鉴权与租户隔离。
"""
import pytest
from fastapi import HTTPException

from api.auth import ProjectPermission, get_current_user, verify_project_access, verify_project_permission
from db.models_org import Org, OrgMember


async def test_owner_is_granted_access(async_db_session, make_user, make_project):
    """项目 owner 直接放行，并返回该项目对象。"""
    owner = make_user("user_owner")
    async_db_session.add(owner)
    async_db_session.add(make_project("proj_owned", owner_id="user_owner", title="Owned"))
    await async_db_session.commit()

    project = await verify_project_access("proj_owned", owner, async_db_session)

    assert project.id == "proj_owned"
    assert project.owner_id == "user_owner"


async def test_org_member_is_granted_access_to_other_owners_project(
    async_db_session, make_user, make_project
):
    """org 成员可访问同 org 下他人拥有的项目。

    owner 必须是另一个用户，否则会走 owner 分支，OrgMember 授权路径根本不会执行。
    """
    owner = make_user("user_owner")
    member = make_user("user_member")
    async_db_session.add_all([owner, member])
    # Org 只有 id/name/plan/seats/seats_used，没有 slug
    async_db_session.add(Org(id="org_test", name="Test Org", plan="studio", seats=5))
    await async_db_session.flush()

    async_db_session.add(OrgMember(org_id="org_test", user_id="user_member", role="writer"))
    async_db_session.add(
        make_project("proj_org", owner_id="user_owner", org_id="org_test", title="Org Project")
    )
    await async_db_session.commit()

    project = await verify_project_access("proj_org", member, async_db_session)

    # 放行原因必须是 org 成员身份，而不是 owner 身份
    assert project.id == "proj_org"
    assert project.owner_id != member.id


async def test_non_member_of_org_project_is_denied(async_db_session, make_user, make_project):
    """同一 org 之外的用户访问 org 项目 → 403。"""
    owner = make_user("user_owner")
    outsider = make_user("user_outsider")
    async_db_session.add_all([owner, outsider])
    async_db_session.add(Org(id="org_test", name="Test Org", plan="studio", seats=5))
    await async_db_session.flush()

    async_db_session.add(OrgMember(org_id="org_test", user_id="user_owner", role="owner"))
    async_db_session.add(
        make_project("proj_org", owner_id="user_owner", org_id="org_test", title="Org Project")
    )
    await async_db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await verify_project_access("proj_org", outsider, async_db_session)

    assert exc_info.value.status_code == 403


async def test_unrelated_user_denied_on_personal_project(async_db_session, make_user, make_project):
    """无 org 的私有项目，非 owner → 403。"""
    owner = make_user("user_owner")
    other = make_user("user_other")
    async_db_session.add_all([owner, other])
    async_db_session.add(make_project("proj_private", owner_id="user_owner", title="Private"))
    await async_db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await verify_project_access("proj_private", other, async_db_session)

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    ("role", "permission", "allowed"),
    [
        ("viewer", ProjectPermission.VIEW, True),
        ("viewer", ProjectPermission.EDIT_BODY, False),
        ("writer", ProjectPermission.EDIT_BODY, True),
        ("writer", ProjectPermission.MANAGE_CODEX, False),
        ("editor", ProjectPermission.MANAGE_CODEX, True),
        ("editor", ProjectPermission.RUN_GUARD, False),
        ("lead", ProjectPermission.MANAGE_MEMBERS, False),
    ],
)
async def test_org_roles_enforce_action_permissions(
    async_db_session, make_user, make_project, role, permission, allowed
):
    owner = make_user("role_owner")
    member = make_user(f"role_{role}")
    async_db_session.add_all([owner, member, Org(id="org_roles", name="Roles", plan="studio", seats=8)])
    await async_db_session.flush()
    async_db_session.add(OrgMember(org_id="org_roles", user_id=member.id, role=role))
    async_db_session.add(make_project("proj_roles", owner_id=owner.id, org_id="org_roles"))
    await async_db_session.commit()

    if allowed:
        project = await verify_project_permission("proj_roles", permission, member, async_db_session)
        assert project.id == "proj_roles"
    else:
        with pytest.raises(HTTPException) as exc_info:
            await verify_project_permission("proj_roles", permission, member, async_db_session)
        assert exc_info.value.status_code == 403


async def test_refresh_rotation_rejects_replayed_token(app_client):
    registered = await app_client.post(
        "/auth/register",
        json={"name": "Rotation", "email": "rotation@example.test", "password": "password-123"},
    )
    assert registered.status_code == 201
    first = registered.json()

    rotated = await app_client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != first["refresh_token"]

    replay = await app_client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401


async def test_logout_revokes_refresh_session(app_client):
    registered = await app_client.post(
        "/auth/register",
        json={"name": "Logout", "email": "logout@example.test", "password": "password-123"},
    )
    session = registered.json()
    response = await app_client.post(
        "/auth/logout",
        json={"refresh_token": session["refresh_token"]},
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert response.status_code == 204
    assert (await app_client.post(
        "/auth/refresh", json={"refresh_token": session["refresh_token"]}
    )).status_code == 401
    assert (await app_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {session['access_token']}"}
    )).status_code == 401


async def test_missing_project_returns_404(async_db_session, make_user):
    """项目不存在 → 404（区别于 403）。"""
    user = make_user("user_test")
    async_db_session.add(user)
    await async_db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await verify_project_access("proj_missing", user, async_db_session)

    assert exc_info.value.status_code == 404


async def test_get_current_user_rejects_invalid_token(async_db_session):
    """伪造/无法解析的 token → 401。"""
    from fastapi.security import HTTPAuthorizationCredentials

    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials, async_db_session)

    assert exc_info.value.status_code == 401


async def test_get_current_user_rejects_valid_token_for_deleted_user(async_db_session):
    """签名合法但用户已不存在 → 401。"""
    from fastapi.security import HTTPAuthorizationCredentials

    from tests.conftest import make_access_token

    token = make_access_token("user_ghost")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials, async_db_session)

    assert exc_info.value.status_code == 401


async def test_get_current_user_accepts_valid_token(async_db_session, make_user):
    """合法 token 且用户存在 → 返回该用户。"""
    from fastapi.security import HTTPAuthorizationCredentials

    from tests.conftest import make_access_token

    async_db_session.add(make_user("user_real"))
    await async_db_session.commit()

    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=make_access_token("user_real")
    )
    user = await get_current_user(credentials, async_db_session)

    assert user.id == "user_real"


# ---------------------------------------------------------------------------
# 端点级鉴权（真实 ASGI 请求）
# ---------------------------------------------------------------------------


async def test_consistency_endpoint_requires_authentication(app_client):
    """无 Authorization 头 → 401/403，且不会执行业务逻辑。"""
    response = await app_client.get("/consistency/issues/proj_test")

    assert response.status_code in (401, 403)


async def test_issue_listing_enforces_tenant_isolation(
    app_client, async_db_session, make_user, make_project, auth_headers
):
    """跨租户读取他人项目的 issue → 403，不泄漏数据。"""
    async_db_session.add_all([make_user("user_a"), make_user("user_b")])
    async_db_session.add(make_project("proj_a", owner_id="user_a"))
    await async_db_session.commit()

    response = await app_client.get(
        "/consistency/issues/proj_a", headers=auth_headers("user_b")
    )

    assert response.status_code == 403


async def test_owner_can_list_own_issues(
    app_client, async_db_session, make_user, make_project, auth_headers
):
    """owner 读取自己项目的 issue 列表 → 200。"""
    async_db_session.add(make_user("user_a"))
    async_db_session.add(make_project("proj_a", owner_id="user_a"))
    await async_db_session.commit()

    response = await app_client.get(
        "/consistency/issues/proj_a", headers=auth_headers("user_a")
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_body_save_requires_idempotency_key_header(
    app_client, async_db_session, make_user, make_project, make_chapter, auth_headers
):
    """缺少 Idempotency-Key → 422（FastAPI 头校验），不会写入正文。"""
    async_db_session.add(make_user("user_a"))
    async_db_session.add(make_project("proj_a", owner_id="user_a"))
    await async_db_session.flush()
    async_db_session.add(make_chapter("ch_a", project_id="proj_a"))
    await async_db_session.commit()

    response = await app_client.put(
        "/chapters/ch_a/body",
        json={
            "content_html": "<p>x</p>",
            "content_json": {"type": "doc", "content": []},
            "base_rev": 0,
        },
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 422
