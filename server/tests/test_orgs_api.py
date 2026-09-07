"""Organization membership, role boundaries, and project sharing."""

from sqlalchemy import select

from db.models_core import Chapter, Project
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


async def _seed_production_project(db, make_project, make_chapter) -> None:
    db.add(make_project("studio_project", owner_id="org_owner", org_id="org_api"))
    await db.flush()
    first = make_chapter("studio_ch_1", project_id="studio_project", idx=1024, words=1800)
    first.title = "雨夜来客"
    second = make_chapter("studio_ch_2", project_id="studio_project", idx=2048, words=2400)
    second.title = "开仓放粮"
    db.add_all([first, second])
    await db.commit()


async def test_studio_assignment_claim_completion_and_output_board(
    app_client, async_db_session, make_user, make_project, make_chapter, auth_headers
):
    await _seed_org(async_db_session, make_user)
    await _seed_production_project(async_db_session, make_project, make_chapter)

    assigned = await app_client.post(
        "/orgs/org_api/projects/studio_project/assignments",
        json={"chapter_id": "studio_ch_1", "assigned_to": "org_member", "notes": "先完成冲突场景"},
        headers=auth_headers("org_owner"),
    )
    assert assigned.status_code == 201
    assignment_id = assigned.json()["id"]
    assert assigned.json()["chapter_title"] == "雨夜来客"
    assert assigned.json()["assignee_name"] == "org_member"
    assert assigned.json()["status"] == "assigned"
    assert assigned.json()["words"] == 1800

    claimed = await app_client.patch(
        f"/orgs/org_api/projects/studio_project/assignments/{assignment_id}",
        json={"status": "claimed"},
        headers=auth_headers("org_member"),
    )
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "claimed"

    completed = await app_client.patch(
        f"/orgs/org_api/projects/studio_project/assignments/{assignment_id}",
        json={"status": "completed"},
        headers=auth_headers("org_member"),
    )
    assert completed.status_code == 200

    board = await app_client.get(
        "/orgs/org_api/projects/studio_project/production",
        headers=auth_headers("org_owner"),
    )
    assert board.status_code == 200
    member = next(item for item in board.json()["members"] if item["user_id"] == "org_member")
    assert member["completed_count"] == 1
    assert member["completed_words"] == 1800
    assert board.json()["can_manage"] is True


async def test_assignment_rejects_viewers_and_cross_assignee_updates(
    app_client, async_db_session, make_user, make_project, make_chapter, auth_headers
):
    await _seed_org(async_db_session, make_user)
    viewer = make_user("org_viewer")
    async_db_session.add(viewer)
    await async_db_session.flush()
    async_db_session.add(OrgMember(org_id="org_api", user_id=viewer.id, role="viewer"))
    await async_db_session.commit()
    await _seed_production_project(async_db_session, make_project, make_chapter)

    rejected = await app_client.post(
        "/orgs/org_api/projects/studio_project/assignments",
        json={"chapter_id": "studio_ch_1", "assigned_to": "org_viewer"},
        headers=auth_headers("org_owner"),
    )
    assert rejected.status_code == 422

    assigned = await app_client.post(
        "/orgs/org_api/projects/studio_project/assignments",
        json={"chapter_id": "studio_ch_1", "assigned_to": "org_member"},
        headers=auth_headers("org_owner"),
    )
    forbidden = await app_client.patch(
        f"/orgs/org_api/projects/studio_project/assignments/{assigned.json()['id']}",
        json={"status": "claimed"},
        headers=auth_headers("org_viewer"),
    )
    assert forbidden.status_code == 403


async def test_only_owner_or_lead_can_assign_and_project_must_belong_to_org(
    app_client, async_db_session, make_user, make_project, make_chapter, auth_headers
):
    await _seed_org(async_db_session, make_user)
    await _seed_production_project(async_db_session, make_project, make_chapter)

    writer_attempt = await app_client.post(
        "/orgs/org_api/projects/studio_project/assignments",
        json={"chapter_id": "studio_ch_1", "assigned_to": "org_member"},
        headers=auth_headers("org_member"),
    )
    assert writer_attempt.status_code == 403

    detached = make_project("detached_project", owner_id="org_owner")
    async_db_session.add(detached)
    await async_db_session.flush()
    async_db_session.add(Chapter(id="detached_ch", project_id=detached.id, title="未共享章节", idx=1024))
    await async_db_session.commit()
    board = await app_client.get(
        "/orgs/org_api/projects/detached_project/production",
        headers=auth_headers("org_owner"),
    )
    assert board.status_code == 409
