"""作品书架 API 的鉴权、范围与聚合测试。"""

from db.models_codex import CodexEntry


async def test_project_list_requires_authentication(app_client):
    response = await app_client.get("/projects")

    assert response.status_code in (401, 403)


async def test_project_list_only_returns_accessible_projects_with_real_counts(
    app_client,
    async_db_session,
    seed_project,
    make_project,
    make_user,
    auth_headers,
):
    chapters = await seed_project(chapter_ids=("ch_1", "ch_2"), title="春山有账")
    chapters[0].title = "第一章"
    chapters[0].words = 3200
    chapters[1].title = "第二章"
    chapters[1].words = 3400
    async_db_session.add(make_user("user_b"))
    await async_db_session.flush()
    async_db_session.add(make_project("proj_private", owner_id="user_b"))
    async_db_session.add(
        CodexEntry(
            id="cx_1",
            project_id="proj_a",
            kind="character",
            name="许知微",
            description="女主",
            attrs={},
            resident=True,
            status="confirmed",
            ref_chapters=["ch_1", "ch_2"],
            conflicts=[],
        )
    )
    await async_db_session.commit()

    response = await app_client.get("/projects", headers=auth_headers("user_a"))

    assert response.status_code == 200
    assert [project["id"] for project in response.json()] == ["proj_a"]
    project = response.json()[0]
    assert project["title"] == "春山有账"
    assert project["words"] == 6600
    assert project["chapters"] == 2
    assert project["codex_count"] == 1
    assert project["guard_open"] == 0
    assert project["last_chapter_title"] in {"第一章", "第二章"}
