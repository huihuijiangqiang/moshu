"""Platform positioning card API tests."""

from sqlalchemy import select

from db.models_positioning import ProjectPositioning, ProjectPositioningRevision


async def test_positioning_update_keeps_field_boundaries_and_creates_revision(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project()

    initial = await app_client.get("/projects/proj_a/positioning", headers=auth_headers("user_a"))
    assert initial.status_code == 200
    assert initial.json()["revision"] == 0
    assert initial.json()["platform"] == "general"

    updated = await app_client.put(
        "/projects/proj_a/positioning",
        headers=auth_headers("user_a"),
        json={
            "expected_revision": 0,
            "platform": "fanqie",
            "title_candidates": ["穿成农女后我靠水利翻身"],
            "selling_point": "从修一条渠开始改变全村命运。",
            "tags": ["穿越", "种田", "经营"],
            "protagonist_dilemma": "她必须在旱灾前筹到第一笔粮钱。",
            "first_payoff": "第一场雨前，水渠终于通了。",
            "long_term_arc": "从自救小村走向新的秩序。",
            "status": "active",
        },
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert payload["revision"] == 1
    assert payload["platform"] == "fanqie"
    assert payload["synopsis"] == ""
    assert payload["tags"] == ["穿越", "种田", "经营"]

    revisions = await app_client.get(
        "/projects/proj_a/positioning/revisions", headers=auth_headers("user_a")
    )
    assert revisions.status_code == 200
    assert [item["revision"] for item in revisions.json()] == [1]

    current = await async_db_session.scalar(
        select(ProjectPositioning).where(ProjectPositioning.project_id == "proj_a")
    )
    snapshots = (
        await async_db_session.execute(
            select(ProjectPositioningRevision).where(ProjectPositioningRevision.project_id == "proj_a")
        )
    ).scalars().all()
    assert current is not None and current.revision == 1
    assert len(snapshots) == 1 and snapshots[0].selling_point.startswith("从修一条渠")


async def test_positioning_optimistic_lock_and_restore_create_new_revision(
    app_client,
    seed_project,
    auth_headers,
):
    await seed_project()
    headers = auth_headers("user_a")
    first = await app_client.put(
        "/projects/proj_a/positioning",
        headers=headers,
        json={"expected_revision": 0, "platform": "qidian", "long_term_arc": "修仙主线"},
    )
    assert first.status_code == 200

    conflict = await app_client.put(
        "/projects/proj_a/positioning",
        headers=headers,
        json={"expected_revision": 0, "selling_point": "过期修改"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "PROJECT_POSITIONING_REVISION_CONFLICT"

    restored = await app_client.post(
        "/projects/proj_a/positioning/revisions/1/restore",
        headers=headers,
        json={"expected_revision": 1},
    )
    assert restored.status_code == 200
    assert restored.json()["revision"] == 2
    assert restored.json()["platform"] == "qidian"


async def test_positioning_requires_project_access(
    app_client, async_db_session, seed_project, make_user, auth_headers
):
    await seed_project()
    async_db_session.add(make_user("user_b"))
    await async_db_session.flush()
    response = await app_client.put(
        "/projects/proj_a/positioning",
        headers=auth_headers("user_b"),
        json={"expected_revision": 0, "platform": "qimao"},
    )
    assert response.status_code == 403
