"""Preview-first, atomic manuscript find/replace API."""

from sqlalchemy import func, select

from db.models_codex import CodexAlias, CodexEntry
from db.models_core import ChapterBody, ChapterVersion, Project, Volume
from db.models_editing import TextReplacementRun
from db.models_org import ChapterAssignment, Org, OrgMember
from services.text_replacement import find_matches, replace_document, replace_html


def document(*paragraphs: tuple[str, str]) -> dict:
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"pid": pid},
                "content": [{"type": "text", "text": text, "marks": [{"type": "bold"}]}],
            }
            for pid, text in paragraphs
        ],
    }


def test_structure_preserving_replacement_can_select_individual_matches():
    source = document(("p-1", "春雨落在春田。"), ("p-2", "春雨未停。"))
    matches = find_matches("ch-1", source, "春雨", case_sensitive=True)

    updated = replace_document(source, "春雨", "秋风", {1}, case_sensitive=True)
    updated_html, count = replace_html(
        '<p data-paragraph-id="p-1"><strong>春雨落在春田。</strong></p><p data-paragraph-id="p-2">春雨未停。</p>',
        "春雨",
        "秋风",
        {1},
        case_sensitive=True,
    )

    assert [match.paragraph_id for match in matches] == ["p-1", "p-2"]
    assert updated["content"][0]["content"][0]["text"] == "春雨落在春田。"
    assert updated["content"][1]["content"][0]["text"] == "秋风未停。"
    assert updated["content"][1]["content"][0]["marks"] == [{"type": "bold"}]
    assert 'data-paragraph-id="p-1"' in updated_html
    assert "<strong>春雨落在春田。</strong>" in updated_html
    assert "秋风未停" in updated_html
    assert count == 2


def test_case_insensitive_replacement_keeps_actual_unicode_spans_and_escapes_html():
    source = document(("p-1", "STRASSE Straße"))
    matches = find_matches("ch-1", source, "straße", case_sensitive=False)
    updated = replace_document(source, "straße", "A&B", {0, 1}, case_sensitive=False)
    updated_html, count = replace_html("<p>STRASSE Straße</p>", "straße", "A&B", {0, 1}, case_sensitive=False)

    assert [match.matched for match in matches] == ["Straße"]
    assert updated["content"][0]["content"][0]["text"] == "STRASSE A&B"
    assert updated_html == "<p>STRASSE A&amp;B</p>"
    assert count == 1


async def seed_manuscript(async_db_session, seed_project):
    chapters = await seed_project(chapter_ids=("ch_1", "ch_2"), title="春山有账")
    volume = Volume(id="vol_a", project_id="proj_a", title="第一卷", idx=1024)
    async_db_session.add(volume)
    await async_db_session.flush()
    bodies = [
        (chapters[0], "<p data-paragraph-id=\"p-1\">许知微走进春山。许知微记下一笔。</p>", document(("p-1", "许知微走进春山。许知微记下一笔。"))),
        (chapters[1], "<p data-paragraph-id=\"p-2\">春山下起雨。</p>", document(("p-2", "春山下起雨。"))),
    ]
    for chapter, body_html, body_json in bodies:
        chapter.volume_id = "vol_a"
        chapter.words = len(body_json["content"][0]["content"][0]["text"])
        async_db_session.add(
            ChapterBody(chapter_id=chapter.id, content_html=body_html, content_json=body_json, rev=1)
        )
        async_db_session.add(
            ChapterVersion(
                chapter_id=chapter.id,
                content_html=body_html,
                content_json=body_json,
                rev=1,
                trigger="manual",
                content_hash=f"hash-{chapter.id}",
            )
        )
    entry = CodexEntry(
        id="cx_1",
        project_id="proj_a",
        kind="character",
        name="许知微",
        description="女主",
        attrs={},
        resident=True,
        status="confirmed",
        ref_chapters=["ch_1"],
        conflicts=[],
    )
    async_db_session.add(entry)
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id=entry.id, alias="知微"))
    await async_db_session.commit()


async def test_preview_is_scoped_authorized_and_flags_codex_names(
    app_client,
    async_db_session,
    seed_project,
    make_user,
    auth_headers,
):
    await seed_manuscript(async_db_session, seed_project)
    async_db_session.add(make_user("user_b"))
    await async_db_session.commit()
    request = {
        "query": "许知微",
        "replacement": "许掌柜",
        "scope": "volume",
        "volume_id": "vol_a",
        "case_sensitive": True,
    }

    assert (await app_client.post("/projects/proj_a/text-replacements/preview", json=request)).status_code == 401
    denied = await app_client.post(
        "/projects/proj_a/text-replacements/preview", json=request, headers=auth_headers("user_b")
    )
    assert denied.status_code == 403

    response = await app_client.post(
        "/projects/proj_a/text-replacements/preview", json=request, headers=auth_headers("user_a")
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_matches"] == 2
    assert len(payload["preview_token"]) == 64
    assert {item["chapter_id"] for item in payload["matches"]} == {"ch_1"}
    assert payload["warnings"] == [
        {
            "entry_id": "cx_1",
            "name": "许知微",
            "kind": "character",
            "matched_term": "许知微",
            "referenced_chapters": 1,
        }
    ]


async def test_execute_selected_matches_is_atomic_idempotent_and_undoable(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_manuscript(async_db_session, seed_project)
    spec = {
        "query": "春山",
        "replacement": "秋岭",
        "scope": "project",
        "case_sensitive": True,
    }
    preview = (
        await app_client.post(
            "/projects/proj_a/text-replacements/preview", json=spec, headers=auth_headers("user_a")
        )
    ).json()
    selected = [match["id"] for match in preview["matches"]]
    request = {**spec, "preview_token": preview["preview_token"], "selected_match_ids": selected}
    headers = auth_headers("user_a", **{"Idempotency-Key": "replace-spring-mountain"})

    first = await app_client.post("/projects/proj_a/text-replacements", json=request, headers=headers)
    replay = await app_client.post("/projects/proj_a/text-replacements", json=request, headers=headers)
    assert first.status_code == replay.status_code == 200, first.text
    assert first.json() == replay.json()
    assert first.json()["total_matches"] == 2
    assert len(first.json()["affected_chapters"]) == 2

    body_one = await async_db_session.get(ChapterBody, "ch_1")
    body_two = await async_db_session.get(ChapterBody, "ch_2")
    await async_db_session.refresh(body_one)
    await async_db_session.refresh(body_two)
    assert "秋岭" in body_one.content_html
    assert "秋岭" in body_two.content_html
    assert body_one.rev == body_two.rev == 2
    assert await async_db_session.scalar(select(func.count()).select_from(TextReplacementRun)) == 1

    undone = await app_client.post(
        f"/projects/proj_a/text-replacements/{first.json()['id']}/undo",
        headers=auth_headers("user_a"),
    )
    assert undone.status_code == 200, undone.text
    async_db_session.expire_all()
    body_one = await async_db_session.get(ChapterBody, "ch_1")
    body_two = await async_db_session.get(ChapterBody, "ch_2")
    assert "春山" in body_one.content_html
    assert "春山" in body_two.content_html
    assert body_one.rev == body_two.rev == 3
    assert (
        await async_db_session.scalar(
            select(func.count()).select_from(ChapterVersion).where(ChapterVersion.trigger == "bulk_replace_undo")
        )
    ) == 2


async def test_writer_cannot_bulk_replace_a_chapter_assigned_to_another_writer(
    app_client,
    async_db_session,
    seed_project,
    make_user,
    auth_headers,
):
    await seed_manuscript(async_db_session, seed_project)
    assigned = make_user("replace_assigned")
    other = make_user("replace_other")
    async_db_session.add_all([assigned, other, Org(id="replace_org", name="Replace Org", seats=3, seats_used=3)])
    await async_db_session.flush()
    async_db_session.add_all([
        OrgMember(org_id="replace_org", user_id="user_a", role="owner"),
        OrgMember(org_id="replace_org", user_id=assigned.id, role="writer"),
        OrgMember(org_id="replace_org", user_id=other.id, role="writer"),
    ])
    project = await async_db_session.get(Project, "proj_a")
    project.org_id = "replace_org"
    async_db_session.add(ChapterAssignment(
        chapter_id="ch_1",
        assigned_to=assigned.id,
        assigned_by="user_a",
        status="claimed",
    ))
    await async_db_session.commit()

    spec = {
        "query": "春山",
        "replacement": "秋岭",
        "scope": "chapter",
        "chapter_id": "ch_1",
        "case_sensitive": True,
    }
    preview = (
        await app_client.post(
            "/projects/proj_a/text-replacements/preview",
            json=spec,
            headers=auth_headers(other.id),
        )
    ).json()
    response = await app_client.post(
        "/projects/proj_a/text-replacements",
        json={
            **spec,
            "preview_token": preview["preview_token"],
            "selected_match_ids": [item["id"] for item in preview["matches"]],
        },
        headers=auth_headers(other.id, **{"Idempotency-Key": "assigned-replacement-denied"}),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "CHAPTER_ASSIGNED_TO_ANOTHER_WRITER"
    body = await async_db_session.get(ChapterBody, "ch_1")
    await async_db_session.refresh(body)
    assert "春山" in body.content_html
    assert body.rev == 1


async def test_execute_rejects_stale_preview_without_partial_writes(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_manuscript(async_db_session, seed_project)
    spec = {"query": "春山", "replacement": "秋岭", "scope": "project", "case_sensitive": True}
    preview = (
        await app_client.post(
            "/projects/proj_a/text-replacements/preview", json=spec, headers=auth_headers("user_a")
        )
    ).json()
    body = await async_db_session.get(ChapterBody, "ch_2")
    body.rev = 2
    await async_db_session.commit()

    response = await app_client.post(
        "/projects/proj_a/text-replacements",
        json={
            **spec,
            "preview_token": preview["preview_token"],
            "selected_match_ids": [match["id"] for match in preview["matches"]],
        },
        headers=auth_headers("user_a", **{"Idempotency-Key": "stale-preview"}),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PREVIEW_STALE"
    async_db_session.expire_all()
    unchanged = await async_db_session.get(ChapterBody, "ch_1")
    assert unchanged.rev == 1
    assert "春山" in unchanged.content_html


async def test_codex_name_replacement_requires_explicit_acknowledgement(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_manuscript(async_db_session, seed_project)
    spec = {"query": "许知微", "replacement": "许掌柜", "scope": "project", "case_sensitive": True}
    preview = (
        await app_client.post(
            "/projects/proj_a/text-replacements/preview", json=spec, headers=auth_headers("user_a")
        )
    ).json()
    response = await app_client.post(
        "/projects/proj_a/text-replacements",
        json={
            **spec,
            "preview_token": preview["preview_token"],
            "selected_match_ids": [match["id"] for match in preview["matches"]],
        },
        headers=auth_headers("user_a", **{"Idempotency-Key": "unacknowledged-codex"}),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CODEX_RISK_NOT_ACKNOWLEDGED"


async def test_undo_refuses_to_overwrite_a_later_manual_edit(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_manuscript(async_db_session, seed_project)
    spec = {"query": "春山", "replacement": "秋岭", "scope": "chapter", "chapter_id": "ch_1"}
    preview = (
        await app_client.post(
            "/projects/proj_a/text-replacements/preview", json=spec, headers=auth_headers("user_a")
        )
    ).json()
    applied = await app_client.post(
        "/projects/proj_a/text-replacements",
        json={
            **spec,
            "preview_token": preview["preview_token"],
            "selected_match_ids": [match["id"] for match in preview["matches"]],
        },
        headers=auth_headers("user_a", **{"Idempotency-Key": "undo-conflict"}),
    )
    body = await async_db_session.get(ChapterBody, "ch_1")
    await async_db_session.refresh(body)
    body.rev += 1
    await async_db_session.commit()

    response = await app_client.post(
        f"/projects/proj_a/text-replacements/{applied.json()['id']}/undo",
        headers=auth_headers("user_a"),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "UNDO_CONFLICT", "chapter_ids": ["ch_1"]}
