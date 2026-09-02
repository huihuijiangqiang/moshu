from db.models_usage import GenerationRun
from services.provenance import generated_paragraph_hashes, provenance_hash


def _run(run_id: str, project_id: str, chapter_id: str, hashes: list[str]) -> GenerationRun:
    return GenerationRun(
        id=run_id,
        user_id="writer",
        project_id=project_id,
        chapter_id=chapter_id,
        task_type="chapter",
        model_tier="main",
        prompt_tokens=100,
        cached_tokens=0,
        completion_tokens=20,
        generated_words=20,
        accepted_words=0,
        layer_report={
            "provenance": {
                "algorithm": "djb2-32-v1",
                "paragraph_hashes": hashes,
            }
        },
    )


def _paragraph(pid: str, text: str, run_id: str | None = None, source_hash: str | None = None) -> dict:
    attrs = {"pid": pid}
    if run_id:
        attrs["aiRunId"] = run_id
    if source_hash:
        attrs["aiSourceHash"] = source_hash
    return {
        "type": "paragraph",
        "attrs": attrs,
        "content": [{"type": "text", "text": text}],
    }


def test_generated_paragraph_hashes_match_editor_contract():
    assert generated_paragraph_hashes("第一段\n\n第二段") == [
        provenance_hash("第一段"),
        provenance_hash("第二段"),
    ]


async def test_save_syncs_accepted_words_and_report_rejects_forged_markers(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(user_id="writer", project_id="novel", chapter_ids=("ch1", "ch2"))
    raw_hash = provenance_hash("AI 原段")
    edited_hash = provenance_hash("AI 待修改")
    async_db_session.add_all([
        _run("run-valid", "novel", "ch1", [raw_hash, edited_hash]),
        _run("run-other-chapter", "novel", "ch2", [provenance_hash("跨章原文")]),
    ])
    await async_db_session.flush()
    content = {
        "type": "doc",
        "content": [
            _paragraph("p1", "AI 原段", "run-valid", raw_hash),
            _paragraph("p2", "AI 已由作者修改", "run-valid", edited_hash),
            _paragraph("p3", "作者手写"),
            _paragraph("p4", "伪造来源", "not-a-run", provenance_hash("伪造来源")),
            _paragraph("p5", "跨章节来源", "run-other-chapter", provenance_hash("跨章原文")),
            _paragraph("p6", "复制同一指纹", "run-valid", raw_hash),
        ],
    }
    response = await app_client.put(
        "/chapters/ch1/body",
        headers=auth_headers("writer", **{"Idempotency-Key": "save-provenance-1"}),
        json={"content_html": "<p>正文</p>", "content_json": content, "base_rev": 0},
    )
    assert response.status_code == 200

    run = await async_db_session.get(GenerationRun, "run-valid")
    assert run.accepted_words == len("AI 原段") + len("AI 已由作者修改")

    report = await app_client.get(
        "/projects/novel/provenance",
        params={"scope": "chapter", "chapter_id": "ch1"},
        headers=auth_headers("writer"),
    )
    assert report.status_code == 200
    payload = report.json()
    assert [paragraph["source"] for paragraph in payload["paragraphs"]] == [
        "ai-raw",
        "ai-edited",
        "human",
        "human",
        "human",
        "human",
    ]
    assert payload["ai_raw_words"] == len("AI 原段")
    assert payload["ai_edited_words"] == len("AI 已由作者修改")
    assert payload["total_words"] == sum(paragraph["words"] for paragraph in payload["paragraphs"])

    second = {
        "type": "doc",
        "content": [_paragraph("p1", "全部改为手写")],
    }
    response = await app_client.put(
        "/chapters/ch1/body",
        headers=auth_headers("writer", **{"Idempotency-Key": "save-provenance-2"}),
        json={"content_html": "<p>全部改为手写</p>", "content_json": second, "base_rev": 1},
    )
    assert response.status_code == 200
    await async_db_session.refresh(run)
    assert run.accepted_words == 0


async def test_provenance_report_requires_project_access_and_chapter_scope(
    app_client,
    async_db_session,
    seed_project,
    make_user,
    auth_headers,
):
    await seed_project(user_id="writer", project_id="novel", chapter_ids=("ch1",))
    async_db_session.add(make_user("stranger"))
    await async_db_session.flush()

    missing = await app_client.get(
        "/projects/novel/provenance",
        params={"scope": "chapter"},
        headers=auth_headers("writer"),
    )
    forbidden = await app_client.get(
        "/projects/novel/provenance",
        params={"scope": "book"},
        headers=auth_headers("stranger"),
    )
    assert missing.status_code == 422
    assert forbidden.status_code == 403
