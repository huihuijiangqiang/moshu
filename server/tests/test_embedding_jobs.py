import contextlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from db.models_embedding import CodexEmbeddingJob
from db.models_chapter_chunks import ChapterChunk
from db.models_core import ChapterBody
from services.codex import create_entry
from services.embedding import EmbeddingProviderError


def test_dispatch_uses_configured_celery_app(monkeypatch):
    from api.codex import dispatch_embedding_backfill
    from celery_app import celery_app

    calls = []

    class Result:
        id = "configured-task"

    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda name, args: calls.append((name, args)) or Result(),
    )
    assert dispatch_embedding_backfill("novel", 16) == "configured-task"
    assert calls == [("codex.backfill_embeddings", ("novel", 16))]


class FailingProvider:
    async def embed_batch(self, texts, model=None):
        raise EmbeddingProviderError("gateway unavailable")


async def test_status_is_pending_then_queue_is_idempotent(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
    monkeypatch,
):
    from api import codex as codex_api

    await seed_project(user_id="writer", project_id="novel")
    await create_entry(async_db_session, project_id="novel", kind="character", name="许知微")
    await async_db_session.commit()
    dispatched: list[tuple[str, int]] = []

    def dispatch(project_id: str, batch_size: int) -> str:
        dispatched.append((project_id, batch_size))
        return "task-1"

    monkeypatch.setattr(codex_api, "dispatch_embedding_backfill", dispatch)
    pending = await app_client.get(
        "/codex/novel/embedding-status", headers=auth_headers("writer")
    )
    first = await app_client.post(
        "/codex/novel/embedding-backfill", headers=auth_headers("writer")
    )
    second = await app_client.post(
        "/codex/novel/embedding-backfill", headers=auth_headers("writer")
    )

    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"
    assert pending.json()["remaining_count"] == 1
    assert first.status_code == second.status_code == 202
    assert first.json()["status"] == second.json()["status"] == "queued"
    assert first.json()["task_id"] == "task-1"
    assert dispatched == [("novel", 32)]


async def test_status_endpoint_is_project_scoped(
    app_client,
    async_db_session,
    seed_project,
    make_user,
    auth_headers,
):
    await seed_project(user_id="writer", project_id="novel")
    async_db_session.add(make_user("stranger"))
    await async_db_session.commit()

    response = await app_client.get(
        "/codex/novel/embedding-status", headers=auth_headers("stranger")
    )
    assert response.status_code == 403


async def test_dead_letter_can_be_queued_again(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
    monkeypatch,
):
    from api import codex as codex_api

    await seed_project(user_id="writer", project_id="novel")
    await create_entry(async_db_session, project_id="novel", kind="character", name="许知微")
    async_db_session.add(CodexEmbeddingJob(
        project_id="novel",
        status="dead_letter",
        attempts=6,
        embedded_count=0,
        remaining_count=1,
        error_code="EmbeddingProviderError",
        last_error="old failure",
    ))
    await async_db_session.commit()
    monkeypatch.setattr(codex_api, "dispatch_embedding_backfill", lambda *_: "task-retry")

    response = await app_client.post(
        "/codex/novel/embedding-backfill", headers=auth_headers("writer")
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["attempts"] == 0
    assert response.json()["last_error"] is None
    assert response.json()["task_id"] == "task-retry"


@pytest.mark.parametrize(
    ("exhausted", "expected_status"),
    [(False, "retrying"), (True, "dead_letter")],
)
async def test_task_failure_persists_attempt_and_exhaustion(
    async_db_session,
    seed_project,
    monkeypatch,
    exhausted,
    expected_status,
):
    from tasks import codex as codex_tasks

    @contextlib.asynccontextmanager
    async def session_factory():
        yield async_db_session

    monkeypatch.setattr(codex_tasks, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(codex_tasks, "GatewayEmbeddingProvider", FailingProvider)
    await seed_project(user_id="writer", project_id="novel")
    await create_entry(async_db_session, project_id="novel", kind="character", name="许知微")
    await async_db_session.commit()

    with pytest.raises(EmbeddingProviderError):
        await codex_tasks._backfill_async(
            "novel",
            32,
            attempt=6 if exhausted else 2,
            task_id="task-1",
            exhausted=exhausted,
        )

    job = await async_db_session.get(CodexEmbeddingJob, "novel")
    assert job.status == expected_status
    assert job.attempts == (6 if exhausted else 2)
    assert job.remaining_count == 1
    assert job.error_code == "EmbeddingProviderError"
    assert job.last_error == "gateway unavailable"
    assert (job.exhausted_at is not None) is exhausted


async def test_terminal_chapter_chunk_embedding_failure_is_visible(
    async_db_session, seed_project, monkeypatch
):
    from tasks import codex as codex_tasks

    @contextlib.asynccontextmanager
    async def session_factory():
        yield async_db_session

    await seed_project(user_id="writer", project_id="novel", chapter_ids=("chapter",))
    async_db_session.add(
        ChapterBody(chapter_id="chapter", content_html="<p>正文</p>", content_json={}, rev=3)
    )
    async_db_session.add(
        ChapterChunk(
            id="chunk",
            project_id="novel",
            chapter_id="chapter",
            body_rev=3,
            chunk_index=0,
            paragraph_start=0,
            paragraph_end=0,
            paragraph_ids=["p1"],
            content_text="正文",
            content_hash="hash",
            status="pending",
        )
    )
    await async_db_session.commit()
    monkeypatch.setattr(codex_tasks, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(codex_tasks, "GatewayEmbeddingProvider", FailingProvider)

    with pytest.raises(EmbeddingProviderError):
        await codex_tasks._backfill_chapter_chunks_async(
            "novel", "chapter", 3, 32, task_id="task-chunk", exhausted=True
        )

    row = await async_db_session.get(ChapterChunk, "chunk")
    assert row.status == "failed"
    assert row.error_detail == "gateway unavailable"


async def test_queue_dispatch_failure_becomes_visible_dead_letter(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
    monkeypatch,
):
    from api import codex as codex_api

    await seed_project(user_id="writer", project_id="novel")
    await create_entry(async_db_session, project_id="novel", kind="character", name="许知微")
    await async_db_session.commit()
    monkeypatch.setattr(
        codex_api,
        "dispatch_embedding_backfill",
        lambda project_id, batch_size: (_ for _ in ()).throw(OSError("broker unavailable")),
    )

    queued = await app_client.post(
        "/codex/novel/embedding-backfill", headers=auth_headers("writer")
    )
    status = await app_client.get(
        "/codex/novel/embedding-status", headers=auth_headers("writer")
    )
    assert queued.status_code == 503
    assert status.json()["status"] == "dead_letter"
    assert status.json()["can_retry"] is True
    assert status.json()["error_code"] == "OSError"


async def test_stale_queued_job_is_redispatched(
    async_db_session,
    seed_project,
    monkeypatch,
):
    from celery_app import celery_app
    from tasks import codex as codex_tasks

    @contextlib.asynccontextmanager
    async def session_factory():
        yield async_db_session

    await seed_project(user_id="writer", project_id="novel")
    async_db_session.add(
        CodexEmbeddingJob(
            project_id="novel",
            status="queued",
            attempts=0,
            embedded_count=0,
            remaining_count=1,
            updated_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    await async_db_session.commit()
    monkeypatch.setattr(codex_tasks, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda *args, **kwargs: SimpleNamespace(id="recovered-task"),
    )

    result = await codex_tasks._recover_stale_embedding_jobs_async()

    job = await async_db_session.get(CodexEmbeddingJob, "novel")
    assert result == {"recovered": 1, "failed": 0}
    assert job.status == "queued"
    assert job.task_id == "recovered-task"
    assert job.dispatch_attempts == 1


async def test_stale_queue_dispatch_exhaustion_becomes_dead_letter(
    async_db_session,
    seed_project,
    monkeypatch,
):
    from celery_app import celery_app
    from tasks import codex as codex_tasks

    @contextlib.asynccontextmanager
    async def session_factory():
        yield async_db_session

    await seed_project(user_id="writer", project_id="novel")
    async_db_session.add(
        CodexEmbeddingJob(
            project_id="novel",
            status="queued",
            attempts=4,
            dispatch_attempts=4,
            embedded_count=0,
            remaining_count=1,
            updated_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    await async_db_session.commit()
    monkeypatch.setattr(codex_tasks, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("broker unavailable")),
    )

    result = await codex_tasks._recover_stale_embedding_jobs_async()

    job = await async_db_session.get(CodexEmbeddingJob, "novel")
    assert result == {"recovered": 0, "failed": 1}
    assert job.status == "dead_letter"
    assert job.attempts == 4
    assert job.dispatch_attempts == 5
    assert job.error_code == "OSError"
    assert job.exhausted_at is not None
