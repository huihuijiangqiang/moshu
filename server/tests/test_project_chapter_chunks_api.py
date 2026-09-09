"""Project-level chapter chunk index operations."""

from sqlalchemy import select

from db.models_chapter_chunks import ChapterChunk
from db.models_consistency import OutboxEvent
from db.models_core import ChapterBody


def document(text: str, pid: str = "p1") -> dict:
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


def chunk(
    chunk_id: str,
    *,
    chapter_id: str,
    body_rev: int,
    status: str,
    embedding: list[float] | None = None,
) -> ChapterChunk:
    return ChapterChunk(
        id=chunk_id,
        project_id="proj_a",
        chapter_id=chapter_id,
        body_rev=body_rev,
        chunk_index=0,
        paragraph_start=0,
        paragraph_end=0,
        paragraph_ids=["p1"],
        content_text=f"正文 {chunk_id}",
        content_hash=f"hash-{chunk_id}",
        embedding=embedding,
        embedding_text_hash=f"hash-{chunk_id}" if embedding else None,
        status=status,
    )


async def test_status_requires_project_access(
    app_client, async_db_session, seed_project, make_user, auth_headers
):
    await seed_project(chapter_ids=())
    async_db_session.add(make_user("stranger"))
    await async_db_session.commit()
    assert (await app_client.get("/projects/proj_a/chapter-chunks/status")).status_code == 401
    assert (
        await app_client.get(
            "/projects/proj_a/chapter-chunks/status", headers=auth_headers("stranger")
        )
    ).status_code == 403
    assert (
        await app_client.get(
            "/projects/proj_a/chapter-chunks/status", headers=auth_headers("user_a")
        )
    ).status_code == 200


async def test_empty_project_status_and_reindex_are_zero(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(chapter_ids=())
    status = await app_client.get(
        "/projects/proj_a/chapter-chunks/status", headers=auth_headers("user_a")
    )
    reindex = await app_client.post(
        "/projects/proj_a/chapter-chunks/reindex", headers=auth_headers("user_a")
    )
    assert status.status_code == 200
    assert status.json()["chunks"] == {
        "total": 0,
        "ready": 0,
        "pending": 0,
        "failed": 0,
        "stale": 0,
    }
    assert status.json()["chapters"] == {
        "total": 0,
        "indexed": 0,
        "unindexed": 0,
        "queued": 0,
    }
    assert reindex.status_code == 202
    assert reindex.json()["chapter_count"] == 0
    assert reindex.json()["queued"] == 0
    assert list((await async_db_session.execute(select(OutboxEvent))).scalars()) == []


async def test_status_separates_current_failure_from_historical_stale(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(chapter_ids=("ch_ready", "ch_failed", "ch_unindexed"))
    async_db_session.add_all(
        [
            ChapterBody(
                chapter_id="ch_ready", content_html="<p>当前</p>", content_json=document("当前"), rev=2
            ),
            ChapterBody(
                chapter_id="ch_failed", content_html="<p>失败</p>", content_json=document("失败"), rev=4
            ),
        ]
    )
    await async_db_session.flush()
    vector = [0.0] * 2048
    vector[0] = 1.0
    async_db_session.add_all(
        [
            chunk("old-ready", chapter_id="ch_ready", body_rev=1, status="ready", embedding=vector),
            chunk("current-ready", chapter_id="ch_ready", body_rev=2, status="ready", embedding=vector),
            chunk("current-failed", chapter_id="ch_failed", body_rev=4, status="failed"),
        ]
    )
    await async_db_session.commit()

    response = await app_client.get(
        "/projects/proj_a/chapter-chunks/status", headers=auth_headers("user_a")
    )
    assert response.status_code == 200
    assert response.json()["chunks"] == {
        "total": 3,
        "ready": 1,
        "pending": 0,
        "failed": 1,
        "stale": 1,
    }
    # Chapters without a body are not index candidates.
    assert response.json()["chapters"] == {
        "total": 2,
        "indexed": 1,
        "unindexed": 1,
        "queued": 0,
    }


async def test_batch_reindex_is_async_idempotent_and_bound_to_head_revision(
    app_client, async_db_session, seed_project, make_user, auth_headers
):
    await seed_project(chapter_ids=("ch_1", "ch_2"))
    async_db_session.add_all(
        [
            ChapterBody(chapter_id="ch_1", content_html="<p>一</p>", content_json=document("一"), rev=2),
            ChapterBody(chapter_id="ch_2", content_html="<p>二</p>", content_json=document("二"), rev=5),
            make_user("stranger"),
        ]
    )
    await async_db_session.flush()
    vector = [0.0] * 2048
    vector[0] = 1.0
    async_db_session.add(
        chunk("ready-before-reindex", chapter_id="ch_1", body_rev=2, status="ready", embedding=vector)
    )
    await async_db_session.commit()
    forbidden = await app_client.post(
        "/projects/proj_a/chapter-chunks/reindex", headers=auth_headers("stranger")
    )
    first = await app_client.post(
        "/projects/proj_a/chapter-chunks/reindex", headers=auth_headers("user_a")
    )
    second = await app_client.post(
        "/projects/proj_a/chapter-chunks/reindex", headers=auth_headers("user_a")
    )
    assert forbidden.status_code == 403
    assert first.status_code == second.status_code == 202
    assert first.json() == {
        "project_id": "proj_a",
        "chapter_count": 2,
        "queued": 2,
        "already_queued": 0,
        "requeued": 0,
    }
    assert second.json()["queued"] == 0
    assert second.json()["already_queued"] == 2
    invalidated = await async_db_session.get(ChapterChunk, "ready-before-reindex")
    assert invalidated.status == "pending"
    assert invalidated.embedding is None
    events = list(
        (
            await async_db_session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.topic == "chapter.chunk_reindex_requested")
                .order_by(OutboxEvent.aggregate_id)
            )
        )
        .scalars()
    )
    assert [(event.aggregate_id, event.aggregate_rev) for event in events] == [
        ("ch_1", 2),
        ("ch_2", 5),
    ]
    assert all(event.payload["force"] is True for event in events)


async def test_terminal_reindex_event_can_be_requeued(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(chapter_ids=("ch_1",))
    async_db_session.add(
        ChapterBody(chapter_id="ch_1", content_html="<p>一</p>", content_json=document("一"), rev=3)
    )
    async_db_session.add(
        OutboxEvent(
            topic="chapter.chunk_reindex_requested",
            aggregate_id="ch_1",
            aggregate_rev=3,
            payload={"project_id": "proj_a", "chapter_id": "ch_1", "body_rev": 3, "force": True},
            status="dead_letter",
            attempts=5,
        )
    )
    await async_db_session.commit()
    response = await app_client.post(
        "/projects/proj_a/chapter-chunks/reindex", headers=auth_headers("user_a")
    )
    assert response.status_code == 202
    assert response.json()["requeued"] == 1
    event = (
        await async_db_session.execute(
            select(OutboxEvent).where(OutboxEvent.topic == "chapter.chunk_reindex_requested")
        )
    ).scalar_one()
    assert event.status == "pending"
    assert event.attempts == 0


async def test_recent_sent_event_with_pending_chunks_is_not_republished(
    app_client, async_db_session, seed_project, auth_headers
):
    from datetime import UTC, datetime

    await seed_project(chapter_ids=("ch_1",))
    async_db_session.add(
        ChapterBody(chapter_id="ch_1", content_html="<p>一</p>", content_json=document("一"), rev=3)
    )
    await async_db_session.flush()
    async_db_session.add(chunk("pending", chapter_id="ch_1", body_rev=3, status="pending"))
    async_db_session.add(
        OutboxEvent(
            topic="chapter.chunk_reindex_requested",
            aggregate_id="ch_1",
            aggregate_rev=3,
            payload={"project_id": "proj_a", "chapter_id": "ch_1", "body_rev": 3, "force": True},
            status="sent",
            attempts=1,
            sent_at=datetime.now(UTC),
        )
    )
    await async_db_session.commit()
    response = await app_client.post(
        "/projects/proj_a/chapter-chunks/reindex", headers=auth_headers("user_a")
    )
    assert response.status_code == 202
    assert response.json()["already_queued"] == 1
    event = (
        await async_db_session.execute(
            select(OutboxEvent).where(OutboxEvent.topic == "chapter.chunk_reindex_requested")
        )
    ).scalar_one()
    assert event.status == "sent"
    assert event.attempts == 1


async def test_body_save_embedding_event_is_not_duplicated_by_project_reindex(
    app_client, async_db_session, seed_project, auth_headers
):
    """Project reindex reuses a fresh body-save embedding event."""
    await seed_project(chapter_ids=("ch_1",))
    async_db_session.add(
        ChapterBody(
            chapter_id="ch_1",
            content_html="<p>一</p>",
            content_json=document("一"),
            rev=3,
        )
    )
    async_db_session.add(
        OutboxEvent(
            topic="chapter.chunk_embedding_requested",
            aggregate_id="ch_1",
            aggregate_rev=3,
            payload={"project_id": "proj_a", "chapter_id": "ch_1", "body_rev": 3},
            status="pending",
            attempts=0,
        )
    )
    await async_db_session.commit()

    response = await app_client.post(
        "/projects/proj_a/chapter-chunks/reindex", headers=auth_headers("user_a")
    )

    assert response.status_code == 202
    assert response.json()["already_queued"] == 1
    assert response.json()["queued"] == 0
    events = list((await async_db_session.execute(select(OutboxEvent))).scalars())
    assert len(events) == 1
    assert events[0].topic == "chapter.chunk_embedding_requested"


async def test_active_body_save_event_wins_over_stale_reindex_event(
    app_client, async_db_session, seed_project, auth_headers
):
    """A pending save event must not be duplicated by an old dead letter."""
    await seed_project(chapter_ids=("ch_1",))
    async_db_session.add(
        ChapterBody(
            chapter_id="ch_1",
            content_html="<p>一</p>",
            content_json=document("一"),
            rev=3,
        )
    )
    async_db_session.add_all(
        [
            OutboxEvent(
                topic="chapter.chunk_reindex_requested",
                aggregate_id="ch_1",
                aggregate_rev=3,
                payload={"project_id": "proj_a", "chapter_id": "ch_1", "body_rev": 3, "force": True},
                status="dead_letter",
                attempts=5,
            ),
            OutboxEvent(
                topic="chapter.chunk_embedding_requested",
                aggregate_id="ch_1",
                aggregate_rev=3,
                payload={"project_id": "proj_a", "chapter_id": "ch_1", "body_rev": 3},
                status="pending",
                attempts=0,
            ),
        ]
    )
    await async_db_session.commit()

    response = await app_client.post(
        "/projects/proj_a/chapter-chunks/reindex", headers=auth_headers("user_a")
    )

    assert response.status_code == 202
    assert response.json()["already_queued"] == 1
    assert response.json()["queued"] == 0
    events = list((await async_db_session.execute(select(OutboxEvent))).scalars())
    assert {(event.topic, event.status) for event in events} == {
        ("chapter.chunk_reindex_requested", "dead_letter"),
        ("chapter.chunk_embedding_requested", "pending"),
    }
