"""Chapter body chunk lifecycle tests (SQLite-safe portions)."""

import pytest
from sqlalchemy import select

from db.models_chapter_chunks import ChapterChunk
from db.models_core import ChapterBody
from services.chapter_chunks import current_chunk_query, embed_pending_chapter_chunks, replace_chapter_chunks
from services.providers import MockEmbeddingProvider


def document(*paragraphs: tuple[str, str]) -> dict:
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"pid": pid},
                "content": [{"type": "text", "text": text}],
            }
            for pid, text in paragraphs
        ],
    }


@pytest.mark.asyncio
async def test_replace_marks_previous_revision_stale_and_writes_current_chunks(
    async_db_session, seed_project
):
    await seed_project(chapter_ids=("ch_1",))
    first = document(("p1", "春雨落下。"), ("p2", "她翻地播种。"))
    await replace_chapter_chunks(
        async_db_session,
        chapter_id="ch_1",
        project_id="proj_a",
        body_rev=1,
        content_html="<p>春雨落下。</p><p>她翻地播种。</p>",
        content_json=first,
        max_chars=100,
        overlap_chars=0,
    )
    second = document(("p1", "春雨落下。"), ("p2", "她翻地播种。"), ("p3", "邻村送来种子。"))
    await replace_chapter_chunks(
        async_db_session,
        chapter_id="ch_1",
        project_id="proj_a",
        body_rev=2,
        content_html="<p>春雨落下。</p><p>她翻地播种。</p><p>邻村送来种子。</p>",
        content_json=second,
        max_chars=100,
        overlap_chars=0,
    )
    rows = list(
        (
            await async_db_session.execute(
                select(ChapterChunk).order_by(ChapterChunk.body_rev, ChapterChunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    assert rows
    assert all(row.status == "stale" for row in rows if row.body_rev == 1)
    assert all(row.body_rev == 2 and row.status == "pending" for row in rows if row.body_rev == 2)
    assert rows[-1].paragraph_ids == ["p1", "p2", "p3"]


@pytest.mark.asyncio
async def test_same_revision_is_idempotent_and_preserves_ready_vector(
    async_db_session, seed_project
):
    await seed_project(chapter_ids=("ch_1",))
    body = document(("p1", "她在田埂边扎下木桩。"),)
    kwargs = dict(
        chapter_id="ch_1",
        project_id="proj_a",
        body_rev=1,
        content_html="<p>她在田埂边扎下木桩。</p>",
        content_json=body,
        max_chars=100,
        overlap_chars=0,
    )
    provider = MockEmbeddingProvider()
    await replace_chapter_chunks(async_db_session, embedding_provider=provider, **kwargs)
    first = (await async_db_session.execute(select(ChapterChunk))).scalar_one()
    first_id = first.id
    first_vector = list(first.embedding)
    await replace_chapter_chunks(async_db_session, embedding_provider=provider, **kwargs)
    rows = list((await async_db_session.execute(select(ChapterChunk))).scalars().all())
    assert len(rows) == 1
    assert rows[0].id == first_id
    assert rows[0].status == "ready"
    assert list(rows[0].embedding) == first_vector


@pytest.mark.asyncio
async def test_replace_rejects_cross_project_chapter(async_db_session, seed_project):
    await seed_project(chapter_ids=("ch_1",))
    with pytest.raises(ValueError, match="does not belong"):
        await replace_chapter_chunks(
            async_db_session,
            chapter_id="ch_1",
            project_id="other-project",
            body_rev=1,
            content_html="<p>x</p>",
            content_json=document(("p1", "x"),),
        )


@pytest.mark.asyncio
async def test_reindex_endpoint_requires_edit_permission_and_embeds_current_revision(
    app_client, async_db_session, seed_project, make_user, auth_headers, monkeypatch
):
    await seed_project(chapter_ids=("ch_1",))
    async_db_session.add(
        ChapterBody(
            chapter_id="ch_1",
            content_html="<p>她在田边整理秧苗。</p>",
            content_json=document(("p1", "她在田边整理秧苗。"),),
            rev=1,
        )
    )
    await async_db_session.flush()
    await replace_chapter_chunks(
        async_db_session,
        chapter_id="ch_1",
        project_id="proj_a",
        body_rev=1,
        content_html="<p>她在田边整理秧苗。</p>",
        content_json=document(("p1", "她在田边整理秧苗。"),),
        max_chars=100,
        overlap_chars=0,
    )
    async_db_session.add(make_user("user_b"))
    await async_db_session.flush()
    assert (await app_client.post("/chapters/ch_1/chunks/reindex")).status_code == 401
    assert (
        await app_client.post(
            "/chapters/ch_1/chunks/reindex", headers=auth_headers("user_b")
        )
    ).status_code == 403

    import api.chapters as chapters_api

    monkeypatch.setattr(chapters_api, "GatewayEmbeddingProvider", MockEmbeddingProvider)
    response = await app_client.post(
        "/chapters/ch_1/chunks/reindex", headers=auth_headers("user_a")
    )
    assert response.status_code == 200, response.text
    assert response.json()["embedded"] == 1
    assert response.json()["counts"]["ready"] == 1


def test_current_chunk_query_contains_head_revision_guard():
    sql = str(current_chunk_query(project_id="proj_a").compile(compile_kwargs={"literal_binds": False}))
    assert "chapter_chunks.body_rev = chapter_bodies.rev" in sql
    assert "chapter_chunks.status =" in sql


@pytest.mark.asyncio
async def test_outbox_revision_cannot_embed_a_newer_body_revision(async_db_session, seed_project):
    await seed_project(chapter_ids=("ch_1",))
    body = ChapterBody(
        chapter_id="ch_1", content_html="<p>新版正文</p>", content_json=document(("p2", "新版正文"),), rev=2
    )
    async_db_session.add(body)
    await async_db_session.flush()
    await replace_chapter_chunks(
        async_db_session,
        chapter_id="ch_1",
        project_id="proj_a",
        body_rev=2,
        content_html="<p>新版正文</p>",
        content_json=document(("p2", "新版正文"),),
        max_chars=100,
        overlap_chars=0,
    )
    embedded = await embed_pending_chapter_chunks(
        async_db_session,
        MockEmbeddingProvider(),
        project_id="proj_a",
        chapter_id="ch_1",
        body_rev=1,
    )
    row = (await async_db_session.execute(select(ChapterChunk))).scalar_one()
    assert embedded == 0
    assert row.body_rev == 2
    assert row.status == "pending"
