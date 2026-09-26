import hashlib
from types import SimpleNamespace

from sqlalchemy import select

from db.models_core import ChapterBody
from db.models_long_generation import GenerationSegment
from db.models_usage import GenerationDraft


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def test_long_segment_lifecycle_merge_and_author_acceptance(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(
        user_id="long_api_writer",
        project_id="long_api_project",
        chapter_ids=("long_api_chapter",),
    )
    headers = auth_headers("long_api_writer")

    planned = await app_client.post(
        "/generate/long-plan",
        headers=headers,
        json={"chapterId": "long_api_chapter", "targetWords": 1600, "segmentWords": 800},
    )
    assert planned.status_code == 200
    inspected = await app_client.get(
        "/generate/long-plan/long_api_chapter", headers=headers
    )
    assert inspected.status_code == 200
    assert inspected.json()["targetWords"] == 1600
    assert inspected.json()["segmentWords"] == 800
    segments = (await async_db_session.execute(select(GenerationSegment))).scalars().all()
    assert len(segments) == 2

    for row in sorted(segments, key=lambda item: item.segment_index):
        claimed = await app_client.post(
            f"/generate/long-segments/{row.id}/claim",
            headers=headers,
            json={"leaseSeconds": 300, "leaseOwner": "api-worker"},
        )
        assert claimed.status_code == 200, claimed.text
        claim = claimed.json()
        heartbeat = await app_client.post(
            f"/generate/long-segments/{row.id}/heartbeat",
            headers=headers,
            json={"leaseRevision": claim["leaseRevision"], "leaseOwner": "api-worker", "leaseSeconds": 300},
        )
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json()["leaseOwner"] == "api-worker"
        prefix = "沈禾核对粮账并记录新的证据。" if row.segment_index == 0 else "周掌柜交出账册，承诺三日内补齐短缺。"
        content = prefix * 50
        checkpoint = await app_client.post(
            f"/generate/long-segments/{row.id}/checkpoint",
            headers=headers,
            json={
                "leaseRevision": claim["leaseRevision"],
                "expectedCheckpointHash": claim["checkpointHash"],
                "contentText": content,
                "leaseOwner": "api-worker",
            },
        )
        assert checkpoint.status_code == 200, checkpoint.text
        validated = await app_client.post(
            f"/generate/long-segments/{row.id}/validate",
            headers=headers,
            json={
                "leaseRevision": claim["leaseRevision"],
                "requiredTerms": ["粮账"] if row.segment_index == 0 else ["账册"],
                "leaseOwner": "api-worker",
            },
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["blocking"] is False
    merged = await app_client.post(
        "/generate/long-plan/long_api_chapter/merge", headers=headers
    )
    assert merged.status_code == 200, merged.text
    draft = merged.json()
    assert draft["status"] == "ready"
    assert draft["requestSummary"]["source"] == "long_generation_segments"
    assert draft["requestSummary"]["longGenerationMerge"]["contentHash"] == _hash(draft["content"])

    merged_again = await app_client.post(
        "/generate/long-plan/long_api_chapter/merge", headers=headers
    )
    assert merged_again.status_code == 200
    assert merged_again.json()["id"] == draft["id"]

    accepted_draft = await app_client.post(
        f"/generate/drafts/{draft['id']}/accept", headers=headers
    )
    assert accepted_draft.status_code == 200, accepted_draft.text
    rows = (await async_db_session.execute(select(GenerationSegment))).scalars().all()
    assert {row.status for row in rows} == {"accepted"}
    assert await async_db_session.get(ChapterBody, "long_api_chapter") is None
    saved_draft = await async_db_session.get(GenerationDraft, draft["id"])
    assert saved_draft is not None and saved_draft.status == "accepted"


async def test_long_plan_blocks_stale_accepted_ledger_after_body_restore(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(
        user_id="long_mismatch_writer",
        project_id="long_mismatch_project",
        chapter_ids=("long_mismatch_chapter",),
    )
    async_db_session.add(
        ChapterBody(
            chapter_id="long_mismatch_chapter",
            content_html="<p>第一段已经在正文里。</p>",
            content_json={
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "attrs": {"pid": "body-p1"},
                        "content": [{"type": "text", "text": "第一段已经在正文里。"}],
                    }
                ],
            },
            rev=3,
        )
    )
    async_db_session.add_all(
        [
            GenerationSegment(
                id="long_mismatch_segment_0",
                project_id="long_mismatch_project",
                chapter_id="long_mismatch_chapter",
                segment_index=0,
                target_words=800,
                status="accepted",
                content_text="第一段已经在正文里。",
                generated_words=800,
                revision=1,
            ),
            GenerationSegment(
                id="long_mismatch_segment_1",
                project_id="long_mismatch_project",
                chapter_id="long_mismatch_chapter",
                segment_index=1,
                target_words=800,
                status="accepted",
                content_text="第二段已经被回退，不应再进入上下文。",
                generated_words=800,
                revision=1,
            ),
        ]
    )
    await async_db_session.commit()

    blocked = await app_client.post(
        "/generate/long-plan",
        headers=auth_headers("long_mismatch_writer"),
        json={
            "chapterId": "long_mismatch_chapter",
            "targetWords": 2400,
            "segmentWords": 800,
        },
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "LONG_LEDGER_BODY_MISMATCH"
    assert blocked.json()["detail"]["segmentIndexes"] == [1]


async def test_long_merge_materializes_only_contiguous_ready_prefix(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(
        user_id="long_prefix_writer",
        project_id="long_prefix_project",
        chapter_ids=("long_prefix_chapter",),
    )
    rows = [
        GenerationSegment(
            id="long_prefix_segment_0",
            project_id="long_prefix_project",
            chapter_id="long_prefix_chapter",
            segment_index=0,
            target_words=800,
            status="ready",
            content_text="第一段已经完成，留下一个可审阅的局面。" * 30,
            generated_words=800,
            revision=1,
        ),
        GenerationSegment(
            id="long_prefix_segment_1",
            project_id="long_prefix_project",
            chapter_id="long_prefix_chapter",
            segment_index=1,
            target_words=800,
            status="accepted",
            content_text="第二段也已完成，继续推进冲突。" * 30,
            generated_words=800,
            revision=1,
        ),
        GenerationSegment(
            id="long_prefix_segment_2",
            project_id="long_prefix_project",
            chapter_id="long_prefix_chapter",
            segment_index=2,
            target_words=800,
            status="pending",
            content_text="",
            generated_words=0,
            revision=1,
        ),
    ]
    async_db_session.add_all(rows)
    await async_db_session.commit()

    merged = await app_client.post(
        "/generate/long-plan/long_prefix_chapter/merge",
        headers=auth_headers("long_prefix_writer"),
    )

    assert merged.status_code == 200, merged.text
    payload = merged.json()
    metadata = payload["requestSummary"]["longGenerationMerge"]
    # Segment 1 is already present in the editor's accepted ledger.  The
    # candidate must contain only the newly ready segment 0; otherwise the UI
    # appends the accepted prefix a second time.
    assert metadata["segmentIds"] == ["long_prefix_segment_0"]
    assert "第二段也已完成" not in payload["content"]


async def test_replanning_changed_segment_discards_incompatible_checkpoint(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(
        user_id="long_replan_writer",
        project_id="long_replan_project",
        chapter_ids=("long_replan_chapter",),
    )
    headers = auth_headers("long_replan_writer")
    first = await app_client.post(
        "/generate/long-plan",
        headers=headers,
        json={
            "chapterId": "long_replan_chapter",
            "targetWords": 800,
            "segmentWords": 800,
            "scenePurposes": ["核对旧账"],
        },
    )
    segment = first.json()["segments"][0]
    claimed = await app_client.post(
        f"/generate/long-segments/{segment['id']}/claim",
        headers=headers,
        json={"leaseOwner": "replan-worker"},
    )
    claim = claimed.json()
    checkpoint = await app_client.post(
        f"/generate/long-segments/{segment['id']}/checkpoint",
        headers=headers,
        json={
            "leaseRevision": claim["leaseRevision"],
            "expectedCheckpointHash": claim["checkpointHash"],
            "contentText": "这一段属于旧的章节目标。" * 20,
            "leaseOwner": "replan-worker",
        },
    )
    assert checkpoint.status_code == 200
    failed = await app_client.post(
        f"/generate/long-segments/{segment['id']}/fail",
        headers=headers,
        json={
            "leaseRevision": claim["leaseRevision"],
            "leaseOwner": "replan-worker",
            "errorCode": "outline_changed",
        },
    )
    assert failed.status_code == 200

    replanned = await app_client.post(
        "/generate/long-plan",
        headers=headers,
        json={
            "chapterId": "long_replan_chapter",
            "targetWords": 800,
            "segmentWords": 800,
            "scenePurposes": ["追查新契"],
        },
    )
    assert replanned.status_code == 200, replanned.text
    updated = replanned.json()["segments"][0]
    assert updated["id"] == segment["id"]
    assert updated["status"] == "pending"
    assert updated["generatedWords"] == 0
    assert updated["revision"] == claim["leaseRevision"] + 1
    reclaimed = await app_client.post(
        f"/generate/long-segments/{segment['id']}/claim",
        headers=headers,
        json={"leaseOwner": "replan-worker"},
    )
    assert reclaimed.status_code == 200
    assert reclaimed.json()["resumed"] is False


async def test_rejected_long_merge_requires_changed_segments(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(
        user_id="long_reject_writer",
        project_id="long_reject_project",
        chapter_ids=("long_reject_chapter",),
    )
    row = GenerationSegment(
        id="long_reject_segment",
        project_id="long_reject_project",
        chapter_id="long_reject_chapter",
        segment_index=0,
        target_words=800,
        status="ready",
        content_text="沈砚秋核完粮账，将副本交给三名见证人按印。" * 40,
        generated_words=800,
        revision=2,
    )
    async_db_session.add(row)
    await async_db_session.commit()
    headers = auth_headers("long_reject_writer")

    merged = await app_client.post(
        "/generate/long-plan/long_reject_chapter/merge",
        headers=headers,
    )
    assert merged.status_code == 200, merged.text
    rejected = await app_client.delete(
        f"/generate/drafts/{merged.json()['id']}",
        headers=headers,
    )
    assert rejected.status_code == 204

    repeated = await app_client.post(
        "/generate/long-plan/long_reject_chapter/merge",
        headers=headers,
    )
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "LONG_MERGE_REJECTED"


async def test_queue_long_segment_claims_and_persists_worker_options(
    app_client, async_db_session, seed_project, auth_headers, monkeypatch
):
    await seed_project(
        user_id="long_queue_writer",
        project_id="long_queue_project",
        chapter_ids=("long_queue_chapter",),
    )
    headers = auth_headers("long_queue_writer")
    planned = await app_client.post(
        "/generate/long-plan",
        headers=headers,
        json={"chapterId": "long_queue_chapter", "targetWords": 800, "segmentWords": 800},
    )
    segment_id = planned.json()["segments"][0]["id"]
    sent = {}

    def fake_send_task(name, *, args, retry):
        sent.update(name=name, args=args, retry=retry)
        return SimpleNamespace(id="celery-long-1")

    monkeypatch.setattr("api.generate.celery_app.send_task", fake_send_task)
    queued = await app_client.post(
        f"/generate/long-segments/{segment_id}/generate",
        headers=headers,
        json={
            "model": "basic",
            "modelId": "doubao-seed-2.1-turbo",
            "contextMode": "deep",
            "dialogueDensity": "high",
            "instruction": "让本段在结尾产生可见局面变化。",
        },
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["status"] == "running"
    assert queued.json()["taskId"] == "celery-long-1"
    assert sent["name"] == "generation.generate_long_segment"
    assert sent["retry"] is False
    assert sent["args"][0] == segment_id
    row = await async_db_session.get(GenerationSegment, segment_id)
    assert row is not None and row.status == "running"
    assert row.context_manifest["generationRequest"]["contextMode"] == "deep"
    assert row.context_manifest["generationRequest"]["instruction"]
