"""Chapter outline API authorization and persistence tests."""


async def test_outline_update_requires_owner_access(
    app_client,
    async_db_session,
    seed_project,
    make_user,
    auth_headers,
):
    await seed_project(chapter_ids=("ch_1",))
    async_db_session.add(make_user("user_b"))
    await async_db_session.flush()
    body = {
        "title": "新的章名",
        "nodes": ["节点一", "节点二"],
        "note": "人物选择要留有余地",
        "base_outline_revision": 0,
        "body_policy": "plan_only",
    }

    assert (await app_client.put("/chapters/ch_1/outline", json=body)).status_code == 401
    assert (
        await app_client.put("/chapters/ch_1/outline", headers=auth_headers("user_b"), json=body)
    ).status_code == 403

    response = await app_client.put("/chapters/ch_1/outline", headers=auth_headers("user_a"), json=body)

    assert response.status_code == 200
    assert response.json()["outline_revision"] == 1
    assert response.json()["nodes"] == ["节点一", "节点二"]

    listing = await app_client.get("/projects/proj_a/chapters", headers=auth_headers("user_a"))
    assert listing.status_code == 200
    assert listing.json()[0]["outline_note"] == "人物选择要留有余地"
    assert listing.json()[0]["outline_revision"] == 1


async def test_outline_revision_history_requires_access(
    app_client,
    async_db_session,
    seed_project,
    make_user,
    auth_headers,
):
    await seed_project(chapter_ids=("ch_1",))
    async_db_session.add(make_user("user_b"))
    await async_db_session.flush()

    assert (await app_client.get("/chapters/ch_1/outline/revisions")).status_code == 401
    assert (
        await app_client.get("/chapters/ch_1/outline/revisions", headers=auth_headers("user_b"))
    ).status_code == 403


async def test_outline_update_persists_temporal_anchor(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(chapter_ids=("ch_temporal",))
    response = await app_client.put(
        "/chapters/ch_temporal/outline",
        headers=auth_headers("user_a"),
        json={
            "title": "时间锚点",
            "nodes": ["确认日期"],
            "base_outline_revision": 0,
            "body_policy": "plan_only",
            "temporal_anchor": {
                "start": "2024-03-12",
                "end": "2024-03-20",
                "precision": "day",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["temporal_anchor"]["start"] == "2024-03-12"

    chapter = await app_client.get("/chapters/ch_temporal", headers=auth_headers("user_a"))
    assert chapter.json()["temporal_anchor"]["end"] == "2024-03-20"


async def test_outline_can_be_updated_repeatedly_without_async_timestamp_reload(
    app_client, seed_project, auth_headers
):
    await seed_project(chapter_ids=("ch_repeat",))
    first = await app_client.put(
        "/chapters/ch_repeat/outline",
        headers=auth_headers("user_a"),
        json={
            "title": "第一次调整",
            "nodes": ["对手先落子"],
            "note": "初稿",
            "base_outline_revision": 0,
            "body_policy": "plan_only",
        },
    )
    assert first.status_code == 200

    second = await app_client.put(
        "/chapters/ch_repeat/outline",
        headers=auth_headers("user_a"),
        json={
            "title": "第二次调整",
            "nodes": ["主角必须付出代价"],
            "note": "定稿",
            "base_outline_revision": 1,
            "body_policy": "plan_only",
        },
    )

    assert second.status_code == 200
    assert second.json()["outline_revision"] == 2
    assert second.json()["title"] == "第二次调整"
    assert second.json()["outline_updated_at"]
