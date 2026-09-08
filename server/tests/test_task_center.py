from datetime import UTC, datetime, timedelta

from db.models_consistency import OutboxEvent
from db.models_consistency_extended import ConsistencyRun
from db.models_embedding import CodexEmbeddingJob
from db.models_usage import GenerationDraft


async def test_task_overview_aggregates_visible_records_and_retry_actions(
    app_client, async_db_session, make_user, make_project, make_chapter, auth_headers
):
    user = make_user("task_user")
    other = make_user("hidden_user")
    async_db_session.add_all([user, other])
    await async_db_session.flush()
    project = make_project("task_project", owner_id=user.id, title="我的长篇")
    hidden = make_project("hidden_project", owner_id=other.id, title="不可见作品")
    async_db_session.add_all([project, hidden])
    await async_db_session.flush()
    chapter = make_chapter("task_chapter", project_id=project.id, idx=1024, title="第一章")
    hidden_chapter = make_chapter("hidden_chapter", project_id=hidden.id)
    async_db_session.add_all([chapter, hidden_chapter])
    await async_db_session.flush()
    now = datetime.now(UTC)
    async_db_session.add_all(
        [
            GenerationDraft(
                id="draft_failed",
                user_id=user.id,
                project_id=project.id,
                chapter_id=chapter.id,
                kind="chapter",
                status="failed",
                content_text="已生成的一段正文",
                generated_words=300,
                request_summary={"targetWords": 1000, "model": "advanced"},
                error_code="stream_interrupted",
                created_at=now - timedelta(minutes=3),
                updated_at=now - timedelta(minutes=2),
            ),
            GenerationDraft(
                id="draft_hidden",
                user_id=other.id,
                project_id=hidden.id,
                chapter_id=hidden_chapter.id,
                kind="chapter",
                status="streaming",
                content_text="hidden",
                generated_words=1,
                request_summary={"targetWords": 1000},
            ),
            ConsistencyRun(
                id=7,
                project_id=project.id,
                chapter_id=chapter.id,
                body_rev=1,
                pipeline_version="test",
                status="failed",
                extract_state="succeeded",
                summary_state="failed",
                scan_state="succeeded",
                trigger="manual_scan",
                error_code="provider_error",
                error_detail="authorization: Bearer sk-secret-key",
                created_at=now - timedelta(minutes=4),
                updated_at=now - timedelta(minutes=1),
            ),
            CodexEmbeddingJob(
                project_id=project.id,
                status="dead_letter",
                attempts=5,
                embedded_count=2,
                remaining_count=3,
                error_code="EmbeddingProviderError",
                last_error="embedding unavailable",
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            ),
            OutboxEvent(
                id=11,
                topic="consistency.manual_scan",
                aggregate_id=chapter.id,
                aggregate_rev=1,
                payload={"project_id": project.id, "chapter_id": chapter.id, "body_rev": 1},
                status="failed",
                attempts=1,
                available_at=now,
                last_error="worker unavailable",
                created_at=now - timedelta(minutes=6),
                updated_at=now - timedelta(minutes=5),
            ),
        ]
    )
    await async_db_session.commit()

    response = await app_client.get("/tasks/overview?limit=20", headers=auth_headers(user.id))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["counts"]["total"] == 4
    assert all(item["project_id"] == project.id for item in payload["items"])
    by_kind = {item["kind"]: item for item in payload["items"]}
    assert by_kind["generation"]["state"] == "failed"
    assert by_kind["generation"]["retry"]["path"] == "/generate/drafts/draft_failed/continue"
    assert by_kind["consistency"]["retry"]["path"] == "/consistency/scan"
    assert "sk-secret-key" not in (by_kind["consistency"]["error_detail"] or "")
    assert by_kind["embedding"]["retry"]["path"].endswith("/chapter-chunks/reindex")
    assert by_kind["outbox"]["retry"]["path"] == "/consistency/scan"


async def test_task_overview_requires_authentication(app_client):
    response = await app_client.get("/tasks/overview")
    assert response.status_code == 401
