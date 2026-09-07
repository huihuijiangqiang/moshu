"""Chapter body history API: lightweight reads and immutable restore semantics."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from db.models_core import Chapter, ChapterBody, ChapterVersion


def document(text: str, pid: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"pid": pid},
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


async def seed_versions(async_db_session, seed_project) -> None:
    await seed_project(chapter_ids=("ch_1",))
    old_json = document("她在荒地边立下第一根木桩。", "p-old")
    current_json = document("春雨落下，她把两亩薄田分成四畦。", "p-current")
    now = datetime.now(UTC)
    async_db_session.add_all(
        [
            ChapterBody(
                chapter_id="ch_1",
                content_html="<p>春雨落下，她把两亩薄田分成四畦。</p>",
                content_json=current_json,
                rev=2,
            ),
            ChapterVersion(
                chapter_id="ch_1",
                content_html="<p>她在荒地边立下第一根木桩。</p>",
                content_json=old_json,
                rev=1,
                trigger="manual",
                content_hash="old",
                created_at=now - timedelta(hours=1),
            ),
            ChapterVersion(
                chapter_id="ch_1",
                content_html="<p>春雨落下，她把两亩薄田分成四畦。</p>",
                content_json=current_json,
                rev=2,
                trigger="autosave",
                content_hash="current",
                created_at=now,
            ),
        ]
    )
    chapter = await async_db_session.get(Chapter, "ch_1")
    chapter.words = len("春雨落下，她把两亩薄田分成四畦。")
    await async_db_session.commit()


async def test_version_list_is_lightweight_ordered_and_authorized(
    app_client,
    async_db_session,
    seed_project,
    make_user,
    auth_headers,
):
    await seed_versions(async_db_session, seed_project)
    async_db_session.add(make_user("user_b"))
    await async_db_session.flush()

    assert (await app_client.get("/chapters/ch_1/versions")).status_code == 401
    assert (
        await app_client.get("/chapters/ch_1/versions", headers=auth_headers("user_b"))
    ).status_code == 403

    response = await app_client.get(
        "/chapters/ch_1/versions?limit=1", headers=auth_headers("user_a")
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["rev"] == 2
    assert rows[0]["is_current"] is True
    assert rows[0]["excerpt"] == "春雨落下，她把两亩薄田分成四畦。"
    assert "content_html" not in rows[0]
    assert "content_json" not in rows[0]

    detail = await app_client.get(
        "/chapters/ch_1/versions/1", headers=auth_headers("user_a")
    )
    assert detail.status_code == 200
    assert detail.json()["content_html"] == "<p>她在荒地边立下第一根木桩。</p>"
    assert detail.json()["is_current"] is False


async def test_restore_creates_new_head_preserves_history_and_replays_idempotently(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_versions(async_db_session, seed_project)
    headers = auth_headers("user_a", **{"Idempotency-Key": "restore-once"})
    payload = {"base_rev": 2}

    first = await app_client.post(
        "/chapters/ch_1/versions/1/restore", headers=headers, json=payload
    )
    replay = await app_client.post(
        "/chapters/ch_1/versions/1/restore", headers=headers, json=payload
    )

    assert first.status_code == 200
    assert replay.status_code == 200, replay.text
    assert first.json() == replay.json()
    assert first.json()["rev"] == 3
    assert first.json()["restored_from_rev"] == 1
    assert first.json()["content_html"] == "<p>她在荒地边立下第一根木桩。</p>"

    body = await async_db_session.get(ChapterBody, "ch_1")
    chapter = await async_db_session.get(Chapter, "ch_1")
    assert body.rev == 3
    assert body.content_json == document("她在荒地边立下第一根木桩。", "p-old")
    assert chapter.words == len("她在荒地边立下第一根木桩。")
    assert await async_db_session.scalar(
        select(func.count()).select_from(ChapterVersion).where(ChapterVersion.chapter_id == "ch_1")
    ) == 3
    restored = await async_db_session.scalar(
        select(ChapterVersion).where(
            ChapterVersion.chapter_id == "ch_1", ChapterVersion.rev == 3
        )
    )
    assert restored.trigger == "restore_version"


async def test_restore_rejects_stale_base_revision_with_both_versions(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_versions(async_db_session, seed_project)

    response = await app_client.post(
        "/chapters/ch_1/versions/1/restore",
        headers=auth_headers("user_a", **{"Idempotency-Key": "restore-stale"}),
        json={"base_rev": 1},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["conflict"] is True
    assert detail["server_rev"] == 2
    assert detail["server_content_html"] == "<p>春雨落下，她把两亩薄田分成四畦。</p>"
    assert detail["client_content_html"] == "<p>她在荒地边立下第一根木桩。</p>"
    assert await async_db_session.scalar(
        select(func.count()).select_from(ChapterVersion).where(ChapterVersion.chapter_id == "ch_1")
    ) == 2


async def test_deleted_chapter_hides_version_history(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_versions(async_db_session, seed_project)
    chapter = await async_db_session.get(Chapter, "ch_1")
    chapter.deleted_at = datetime.now(UTC)
    await async_db_session.flush()

    response = await app_client.get(
        "/chapters/ch_1/versions", headers=auth_headers("user_a")
    )
    assert response.status_code == 404
