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
