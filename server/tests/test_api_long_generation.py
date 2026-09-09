import hashlib

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
