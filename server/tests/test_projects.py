"""作品书架 API 的鉴权、范围与聚合测试。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from db.models_codex import CodexEntry
from db.models_core import Chapter, Project, ProjectNote, Volume
from db.models_writing import ProjectDailyWriting


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


async def test_daily_writing_counts_net_positive_saved_words_in_project_list_and_detail(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(chapter_ids=("ch_daily",))

    def document(text: str) -> dict:
        return {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "attrs": {"pid": "daily-p1"},
                    "content": [{"type": "text", "text": text}],
                }
            ],
        }

    first = await app_client.put(
        "/chapters/ch_daily/body",
        headers=auth_headers("user_a", **{"Idempotency-Key": "daily-save-1"}),
        json={"content_html": "<p>春风起</p>", "content_json": document("春风起"), "base_rev": 0},
    )
    assert first.status_code == 200

    second = await app_client.put(
        "/chapters/ch_daily/body",
        headers=auth_headers("user_a", **{"Idempotency-Key": "daily-save-2"}),
        json={"content_html": "<p>春风起，田埂暖</p>", "content_json": document("春风起，田埂暖"), "base_rev": 1},
    )
    assert second.status_code == 200

    deletion = await app_client.put(
        "/chapters/ch_daily/body",
        headers=auth_headers("user_a", **{"Idempotency-Key": "daily-save-3"}),
        json={"content_html": "<p>春风</p>", "content_json": document("春风"), "base_rev": 2},
    )
    assert deletion.status_code == 200

    listed = await app_client.get("/projects", headers=auth_headers("user_a"))
    assert listed.status_code == 200
    assert listed.json()[0]["today_words"] == len("春风起，田埂暖")

    detail = await app_client.get("/projects/proj_a", headers=auth_headers("user_a"))
    assert detail.status_code == 200
    assert detail.json()["today_words"] == len("春风起，田埂暖")

    activity = await async_db_session.get(
        ProjectDailyWriting,
        {"project_id": "proj_a", "chapter_id": "ch_daily", "day": datetime.now(UTC).date()},
    )
    assert activity is not None
    assert activity.words_added == len("春风起，田埂暖")
    assert activity.saves == 2


async def test_writing_progress_returns_continuous_history_and_target_status(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(chapter_ids=("ch_history",))
    today = datetime.now(UTC).date()
    async_db_session.add_all(
        [
            ProjectDailyWriting(
                project_id="proj_a",
                chapter_id="ch_history",
                day=today - timedelta(days=2),
                words_added=3000,
                saves=2,
            ),
            ProjectDailyWriting(
                project_id="proj_a",
                chapter_id="ch_history",
                day=today,
                words_added=1200,
                saves=1,
            ),
        ]
    )
    await async_db_session.commit()

    response = await app_client.get(
        "/projects/proj_a/writing-progress?days=3",
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "date": (today - timedelta(days=2)).isoformat(),
            "words_added": 3000,
            "saves": 2,
            "target_words_daily": 3000,
            "target_met": True,
        },
        {
            "date": (today - timedelta(days=1)).isoformat(),
            "words_added": 0,
            "saves": 0,
            "target_words_daily": 3000,
            "target_met": False,
        },
        {
            "date": today.isoformat(),
            "words_added": 1200,
            "saves": 1,
            "target_words_daily": 3000,
            "target_met": False,
        },
    ]


async def test_writing_progress_respects_project_access_and_day_limit(
    app_client,
    seed_project,
    make_user,
    auth_headers,
    async_db_session,
):
    await seed_project()
    async_db_session.add(make_user("user_b"))
    await async_db_session.commit()

    assert (
        await app_client.get(
            "/projects/proj_a/writing-progress?days=0",
            headers=auth_headers("user_a"),
        )
    ).status_code == 422
    assert (
        await app_client.get(
            "/projects/proj_a/writing-progress",
            headers=auth_headers("user_b"),
        )
    ).status_code == 403


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


async def test_private_project_notes_support_mobile_capture_and_chapter_anchors(
    app_client,
    async_db_session,
    seed_project,
    make_project,
    make_chapter,
    make_user,
    auth_headers,
):
    await seed_project(chapter_ids=("ch_a",))
    async_db_session.add(make_user("user_b"))
    async_db_session.add(make_project("proj_other", owner_id="user_a"))
    await async_db_session.flush()
    async_db_session.add(make_chapter("ch_other", project_id="proj_other"))
    async_db_session.add_all(
        [
            ProjectNote(
            id="pn_private_other_user",
            project_id="proj_a",
            user_id="user_b",
            chapter_id="ch_a",
            content="另一位用户的私有速记",
            ),
            ProjectNote(
                id="pn_dirty_cross_project",
                project_id="proj_a",
                user_id="user_a",
                chapter_id="ch_other",
                content="历史脏记录不得泄露其他作品章节标题",
            ),
        ]
    )
    await async_db_session.commit()

    created = await app_client.post(
        "/projects/proj_a/notes",
        json={"content": "  下一章让旧井在雨后塌陷。  ", "chapter_id": "ch_a"},
        headers=auth_headers("user_a"),
    )
    assert created.status_code == 201
    note_id = created.json()["id"]
    assert created.json()["content"] == "下一章让旧井在雨后塌陷。"
    assert created.json()["chapter_id"] == "ch_a"
    assert created.json()["chapter_index"] == 1024

    listed = await app_client.get(
        "/projects/proj_a/notes", headers=auth_headers("user_a")
    )
    listed_by_id = {item["id"]: item for item in listed.json()}
    assert set(listed_by_id) == {note_id, "pn_dirty_cross_project"}
    assert listed_by_id["pn_dirty_cross_project"]["chapter_index"] is None
    assert listed_by_id["pn_dirty_cross_project"]["chapter_title"] is None

    invalid_chapter = await app_client.post(
        "/projects/proj_a/notes",
        json={"content": "不能跨作品绑定", "chapter_id": "ch_other"},
        headers=auth_headers("user_a"),
    )
    assert invalid_chapter.status_code == 422
    assert invalid_chapter.json()["detail"]["code"] == "PROJECT_NOTE_CHAPTER_INVALID"

    cannot_delete_other_user = await app_client.delete(
        "/projects/proj_a/notes/pn_private_other_user",
        headers=auth_headers("user_a"),
    )
    assert cannot_delete_other_user.status_code == 404
    deleted = await app_client.delete(
        f"/projects/proj_a/notes/{note_id}", headers=auth_headers("user_a")
    )
    assert deleted.status_code == 204
    deleted_dirty = await app_client.delete(
        "/projects/proj_a/notes/pn_dirty_cross_project",
        headers=auth_headers("user_a"),
    )
    assert deleted_dirty.status_code == 204
    assert (
        await app_client.get("/projects/proj_a/notes", headers=auth_headers("user_a"))
    ).json() == []


async def test_project_notes_require_authentication_and_project_access(
    app_client, async_db_session, seed_project, make_user, auth_headers
):
    await seed_project()
    async_db_session.add(make_user("user_b"))
    await async_db_session.commit()

    assert (await app_client.get("/projects/proj_a/notes")).status_code == 401
    denied = await app_client.post(
        "/projects/proj_a/notes",
        json={"content": "不应写入"},
        headers=auth_headers("user_b"),
    )
    assert denied.status_code == 403


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
    assert chapters[0].volume_id == volumes[0].id
    assert chapters[0].outline == ["她带着一本账册穿越到荒年。", "从修水渠开始重建村庄。"]
    assert {(entry.kind, entry.name) for entry in entries} == {("character", "许知微"), ("rule", "功德账")}


async def test_create_project_assigns_initial_chapters_to_requested_volumes(
    app_client,
    async_db_session,
    make_user,
    auth_headers,
):
    """Initial chapter plans may explicitly target a zero-based volume index."""
    async_db_session.add(make_user("user_a"))
    await async_db_session.flush()

    response = await app_client.post(
        "/projects",
        headers=auth_headers("user_a"),
        json={
            "title": "多卷映射测试",
            "volumes": [
                {"title": "第一卷", "summary": "起步"},
                {"title": "第二卷", "summary": "成长"},
                {"title": "第三卷", "summary": "决战"},
            ],
            "chapters": [
                {"title": "第一章", "outline": ["开场", "冲突"], "volumeIndex": 0},
                {"title": "第二章", "outline": ["修行", "突破"], "volumeIndex": 1},
                {"title": "第三章", "outline": ["赴约", "交锋"], "volumeIndex": 2},
                {"title": "第四章", "outline": ["余波", "埋线"], "volumeIndex": 1},
            ],
        },
    )

    assert response.status_code == 201
    project_id = response.json()["id"]
    volumes = (
        await async_db_session.execute(
            select(Volume).where(Volume.project_id == project_id).order_by(Volume.idx)
        )
    ).scalars().all()
    chapters = (
        await async_db_session.execute(
            select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.idx)
        )
    ).scalars().all()

    assert [chapter.volume_id for chapter in chapters] == [
        volumes[0].id,
        volumes[1].id,
        volumes[2].id,
        volumes[1].id,
    ]


async def test_create_project_rejects_initial_chapter_volume_index_out_of_range(
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
            "title": "非法卷索引测试",
            "volumes": [{"title": "第一卷", "summary": "起步"}],
            "chapters": [
                {"title": "第一章", "outline": ["开场", "冲突"], "volumeIndex": 1}
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "CHAPTER_VOLUME_INDEX_OUT_OF_RANGE",
        "field": "chapters[0].volumeIndex",
        "volume_index": 1,
        "volume_count": 1,
        "message": "volumeIndex 必须是 0 到 0 之间的整数。",
    }


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
