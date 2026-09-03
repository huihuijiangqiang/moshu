"""作品书架 API 的鉴权、范围与聚合测试。"""

from datetime import UTC, datetime

from sqlalchemy import select

from db.models_codex import CodexEntry
from db.models_core import Chapter, Project, Volume


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


async def test_project_detail_and_chapter_list_require_project_access(
    app_client,
    async_db_session,
    seed_project,
    make_user,
    auth_headers,
):
    await seed_project()
    async_db_session.add(make_user("user_b"))
    await async_db_session.flush()

    assert (await app_client.get("/projects/proj_a")).status_code == 401
    assert (await app_client.get("/projects/proj_a/chapters")).status_code == 401
    assert (await app_client.get("/projects/proj_a", headers=auth_headers("user_b"))).status_code == 403
    assert (await app_client.get("/projects/proj_a/chapters", headers=auth_headers("user_b"))).status_code == 403
    assert (await app_client.get("/projects/proj_a", headers=auth_headers("user_a"))).status_code == 200
    assert (await app_client.get("/projects/proj_a/chapters", headers=auth_headers("user_a"))).status_code == 200


async def test_create_project_persists_plan_first_chapter_and_initial_codex(
    app_client,
    async_db_session,
    make_user,
    auth_headers,
):
    async_db_session.add(make_user("user_a"))
    await async_db_session.flush()

    response = await app_client.post(
        "/projects",
        headers=auth_headers("user_a"),
        json={
            "title": "新书",
            "genre": "女频 · 种田",
            "inspiration": "她带着一本账册穿越到荒年。",
            "synopsis": "从修水渠开始重建村庄。",
            "protagonist": "许知微 · 二十四岁\n擅长记账。",
            "core_hook": "功德账 · 每次选择都留下代价",
            "audience": "女频",
            "template": "群像经营",
            "tags": ["穿越", "经营"],
            "volumes": [
                {"title": "第一卷 · 落脚", "summary": "先活下来。"},
                {"title": "第二卷 · 开渠", "summary": "建立新秩序。"},
            ],
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["title"] == "新书"
    assert payload["inspiration"] == "她带着一本账册穿越到荒年。"
    assert payload["story_settings"]["template"] == "群像经营"
    assert [volume["title"] for volume in payload["volumes"]] == ["第一卷 · 落脚", "第二卷 · 开渠"]

    project = (
        await async_db_session.execute(select(Project).where(Project.id == payload["id"]))
    ).scalar_one()
    volumes = (
        await async_db_session.execute(select(Volume).where(Volume.project_id == project.id).order_by(Volume.idx))
    ).scalars().all()
    chapters = (
        await async_db_session.execute(select(Chapter).where(Chapter.project_id == project.id))
    ).scalars().all()
    entries = (
        await async_db_session.execute(select(CodexEntry).where(CodexEntry.project_id == project.id))
    ).scalars().all()
    assert project.owner_id == "user_a"
    assert len(volumes) == 2
    assert len(chapters) == 1
    assert chapters[0].outline == ["她带着一本账册穿越到荒年。", "从修水渠开始重建村庄。"]
    assert {(entry.kind, entry.name) for entry in entries} == {("character", "许知微"), ("rule", "功德账")}


async def test_insert_chapter_reindexes_the_whole_book(
    app_client,
    async_db_session,
    seed_project,
    make_chapter,
    auth_headers,
):
    chapters = await seed_project(chapter_ids=("ch_1", "ch_3"))
    volume_a = Volume(id="vol_a", project_id="proj_a", title="第一卷", idx=1024)
    volume_b = Volume(id="vol_b", project_id="proj_a", title="第二卷", idx=2048)
    async_db_session.add_all([volume_a, volume_b])
    await async_db_session.flush()
    chapters[0].idx = 1
    chapters[0].volume_id = "vol_a"
    chapters[1].idx = 3
    chapters[1].volume_id = "vol_b"
    async_db_session.add(make_chapter("ch_2", project_id="proj_a", idx=2, volume_id="vol_a"))
    await async_db_session.flush()

    response = await app_client.post(
        "/projects/proj_a/chapters",
        headers=auth_headers("user_a"),
        json={"volume_id": "vol_a", "after_index": 1},
    )

    assert response.status_code == 201
    rows = (
        await async_db_session.execute(select(Chapter).where(Chapter.project_id == "proj_a").order_by(Chapter.idx))
    ).scalars().all()
    assert [chapter.idx for chapter in rows] == [1, 2, 3, 4]
    assert rows[1].id == response.json()["id"]
    assert rows[-1].volume_id == "vol_b"


async def test_update_project_metadata(app_client, async_db_session, seed_project, auth_headers):
    await seed_project(title="旧书名")

    response = await app_client.patch(
        "/projects/proj_a",
        headers=auth_headers("user_a"),
        json={"title": "新书名", "genre": "悬疑", "status": "archived", "target_words_daily": 1800},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "新书名"
    assert response.json()["status"] == "archived"
    project = await async_db_session.get(Project, "proj_a")
    assert (project.genre, project.target_words_daily) == ("悬疑", 1800)


async def test_volume_reorder_trash_restore_and_permanent_delete(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    chapters = await seed_project(chapter_ids=("ch_a", "ch_b"))
    volume_a = Volume(id="vol_a", project_id="proj_a", title="第一卷", idx=1024)
    volume_b = Volume(id="vol_b", project_id="proj_a", title="第二卷", idx=2048)
    async_db_session.add_all([volume_a, volume_b])
    await async_db_session.flush()
    chapters[0].volume_id = "vol_a"
    chapters[1].volume_id = "vol_b"
    await async_db_session.commit()

    reordered = await app_client.put(
        "/projects/proj_a/volumes/order",
        headers=auth_headers("user_a"),
        json={"volume_ids": ["vol_b", "vol_a"]},
    )
    assert reordered.status_code == 200
    assert [item["id"] for item in reordered.json()] == ["vol_b", "vol_a"]
    chapter_rows = (
        await async_db_session.execute(select(Chapter).where(Chapter.project_id == "proj_a").order_by(Chapter.idx))
    ).scalars().all()
    assert [chapter.id for chapter in chapter_rows] == ["ch_b", "ch_a"]

    missing_target = await app_client.delete(
        "/projects/proj_a/volumes/vol_a", headers=auth_headers("user_a")
    )
    assert missing_target.status_code == 409
    assert missing_target.json()["detail"]["code"] == "TARGET_VOLUME_REQUIRED"

    trashed = await app_client.delete(
        "/projects/proj_a/volumes/vol_a?target_volume_id=vol_b",
        headers=auth_headers("user_a"),
    )
    assert trashed.status_code == 204
    assert chapters[0].volume_id == "vol_b"
    trash = await app_client.get("/projects/proj_a/trash", headers=auth_headers("user_a"))
    assert [item["id"] for item in trash.json()["volumes"]] == ["vol_a"]

    restored = await app_client.post(
        "/projects/proj_a/trash/volumes/vol_a/restore", headers=auth_headers("user_a")
    )
    assert restored.status_code == 200
    assert restored.json()["idx"] == 2048
    await app_client.delete("/projects/proj_a/volumes/vol_a", headers=auth_headers("user_a"))
    removed = await app_client.delete(
        "/projects/proj_a/trash/volumes/vol_a", headers=auth_headers("user_a")
    )
    assert removed.status_code == 204
    assert await async_db_session.get(Volume, "vol_a") is None


async def test_permanent_volume_delete_rejects_soft_deleted_chapters(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    chapter = (await seed_project())[0]
    volume = Volume(id="vol_trash", project_id="proj_a", title="废弃卷", idx=1024)
    async_db_session.add(volume)
    await async_db_session.flush()
    chapter.volume_id = volume.id
    chapter.deleted_at = datetime.now(UTC)
    volume.deleted_at = datetime.now(UTC)
    await async_db_session.commit()

    response = await app_client.delete(
        "/projects/proj_a/trash/volumes/vol_trash",
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TRASH_VOLUME_NOT_EMPTY"
    assert await async_db_session.get(Chapter, chapter.id) is not None


async def test_deleting_a_volume_relation_does_not_delete_its_chapter(
    async_db_session,
    seed_project,
):
    chapter = (await seed_project())[0]
    volume = Volume(id="vol_detached", project_id="proj_a", title="待移除卷", idx=1024)
    async_db_session.add(volume)
    await async_db_session.flush()
    chapter.volume_id = volume.id
    await async_db_session.commit()

    await async_db_session.delete(volume)
    await async_db_session.commit()

    preserved = await async_db_session.get(Chapter, chapter.id)
    assert preserved is not None
    assert preserved.volume_id is None


async def test_chapter_move_trash_restore_and_permanent_delete(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    chapters = await seed_project(chapter_ids=("ch_a", "ch_b", "ch_c"))
    volume_a = Volume(id="vol_a", project_id="proj_a", title="第一卷", idx=1024)
    volume_b = Volume(id="vol_b", project_id="proj_a", title="第二卷", idx=2048)
    async_db_session.add_all([volume_a, volume_b])
    await async_db_session.flush()
    chapters[0].volume_id = "vol_a"
    chapters[1].volume_id = "vol_a"
    chapters[2].volume_id = "vol_b"
    await async_db_session.commit()

    moved = await app_client.put(
        "/projects/proj_a/chapters/ch_b/position",
        headers=auth_headers("user_a"),
        json={"volume_id": "vol_b", "placement": "last"},
    )
    assert moved.status_code == 200
    assert moved.json()["volume_id"] == "vol_b"
    rows = (
        await async_db_session.execute(select(Chapter).where(Chapter.project_id == "proj_a").order_by(Chapter.idx))
    ).scalars().all()
    assert [chapter.id for chapter in rows] == ["ch_a", "ch_c", "ch_b"]

    trashed = await app_client.delete(
        "/projects/proj_a/chapters/ch_b", headers=auth_headers("user_a")
    )
    assert trashed.status_code == 204
    assert (await app_client.get("/chapters/ch_b", headers=auth_headers("user_a"))).status_code == 404
    listed = await app_client.get("/projects/proj_a/chapters", headers=auth_headers("user_a"))
    assert [item["id"] for item in listed.json()] == ["ch_a", "ch_c"]

    restored = await app_client.post(
        "/projects/proj_a/trash/chapters/ch_b/restore",
        headers=auth_headers("user_a"),
        json={"volume_id": "vol_a"},
    )
    assert restored.status_code == 200
    assert restored.json()["volume_id"] == "vol_a"
    assert restored.json()["idx"] == 2

    await app_client.delete("/projects/proj_a/chapters/ch_b", headers=auth_headers("user_a"))
    removed = await app_client.delete(
        "/projects/proj_a/trash/chapters/ch_b", headers=auth_headers("user_a")
    )
    assert removed.status_code == 204
    assert await async_db_session.get(Chapter, "ch_b") is None


async def test_structure_guards_last_items_and_cross_project_targets(
    app_client,
    async_db_session,
    seed_project,
    make_project,
    make_user,
    auth_headers,
):
    chapter = (await seed_project())[0]
    volume = Volume(id="vol_a", project_id="proj_a", title="唯一卷", idx=1024)
    async_db_session.add(volume)
    await async_db_session.flush()
    chapter.volume_id = volume.id
    async_db_session.add(make_user("user_b"))
    await async_db_session.flush()
    async_db_session.add(make_project("proj_b", owner_id="user_b"))
    await async_db_session.flush()
    async_db_session.add(Volume(id="vol_foreign", project_id="proj_b", title="外部卷", idx=1024))
    await async_db_session.commit()

    last_chapter = await app_client.delete(
        "/projects/proj_a/chapters/ch_a", headers=auth_headers("user_a")
    )
    last_volume = await app_client.delete(
        "/projects/proj_a/volumes/vol_a", headers=auth_headers("user_a")
    )
    cross_project = await app_client.put(
        "/projects/proj_a/chapters/ch_a/position",
        headers=auth_headers("user_a"),
        json={"volume_id": "vol_foreign", "placement": "last"},
    )

    assert last_chapter.status_code == 409
    assert last_chapter.json()["detail"]["code"] == "LAST_CHAPTER"
    assert last_volume.status_code == 409
    assert last_volume.json()["detail"]["code"] == "LAST_VOLUME"
    assert cross_project.status_code == 422
    assert cross_project.json()["detail"]["code"] == "VOLUME_NOT_IN_PROJECT"
