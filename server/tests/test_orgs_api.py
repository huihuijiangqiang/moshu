"""Organization membership, role boundaries, and project sharing."""

from sqlalchemy import select

from db.models_core import Project
from db.models_org import Org, OrgMember


async def _seed_org(db, make_user, *, member_role: str = "writer"):
    owner = make_user("org_owner", email="owner@example.test")
    member = make_user("org_member", email="member@example.test")
    outsider = make_user("org_outsider", email="outsider@example.test")
    db.add_all([owner, member, outsider])
    await db.flush()
    db.add(Org(id="org_api", name="Studio", plan="studio", seats=5, seats_used=2))
    await db.flush()
    db.add_all([
        OrgMember(org_id="org_api", user_id=owner.id, role="owner"),
        OrgMember(org_id="org_api", user_id=member.id, role=member_role),
    ])
    await db.commit()


async def test_non_member_cannot_list_org_members(
    app_client, async_db_session, make_user, auth_headers
):
    await _seed_org(async_db_session, make_user)

    response = await app_client.get(
        "/orgs/org_api/members", headers=auth_headers("org_outsider")
    )

    assert response.status_code == 403


async def test_owner_can_add_and_change_member_role(
    app_client, async_db_session, make_user, auth_headers
):
    await _seed_org(async_db_session, make_user)
    invitee = make_user("org_invitee", email="invitee@example.test")
    async_db_session.add(invitee)
    await async_db_session.commit()

    created = await app_client.post(
        "/orgs/org_api/members",
        json={"email": "invitee@example.test", "role": "viewer"},
        headers=auth_headers("org_owner"),
    )
    assert created.status_code == 201
    assert created.json()["role"] == "viewer"

    updated = await app_client.patch(
        "/orgs/org_api/members/org_invitee",
        json={"role": "editor"},
        headers=auth_headers("org_owner"),
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "editor"


async def test_non_owner_roles_cannot_manage_members(
    app_client, async_db_session, make_user, auth_headers
):
    await _seed_org(async_db_session, make_user, member_role="lead")

    response = await app_client.patch(
        "/orgs/org_api/members/org_member",
        json={"role": "viewer"},
        headers=auth_headers("org_member"),
    )

    assert response.status_code == 403


async def test_last_owner_cannot_be_demoted_or_removed(
    app_client, async_db_session, make_user, auth_headers
):
    await _seed_org(async_db_session, make_user)
    headers = auth_headers("org_owner")

    demoted = await app_client.patch(
        "/orgs/org_api/members/org_owner", json={"role": "lead"}, headers=headers
    )
    removed = await app_client.delete(
        "/orgs/org_api/members/org_owner", headers=headers
    )

    assert demoted.status_code == 422
    assert removed.status_code == 422


async def test_only_project_owner_can_attach_project_to_org(
    app_client, async_db_session, make_user, make_project, auth_headers
):
    await _seed_org(async_db_session, make_user)
    project = make_project("shared_project", owner_id="org_member")
    async_db_session.add(project)
    await async_db_session.commit()

    owner_attempt = await app_client.post(
        "/orgs/org_api/projects/shared_project", headers=auth_headers("org_owner")
    )
    member_attempt = await app_client.post(
        "/orgs/org_api/projects/shared_project", headers=auth_headers("org_member")
    )

    assert owner_attempt.status_code == 403
    assert member_attempt.status_code == 403
    stored = await async_db_session.scalar(
        select(Project).where(Project.id == "shared_project")
    )
    assert stored is not None
    assert stored.org_id is None


async def test_project_owner_who_owns_org_can_attach_project(
    app_client, async_db_session, make_user, make_project, auth_headers
):
    await _seed_org(async_db_session, make_user)
    async_db_session.add(make_project("owner_project", owner_id="org_owner"))
    await async_db_session.commit()

    response = await app_client.post(
        "/orgs/org_api/projects/owner_project", headers=auth_headers("org_owner")
    )

    assert response.status_code == 200
    assert response.json() == {"project_id": "owner_project", "org_id": "org_api"}
