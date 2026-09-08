"""Naturalization review service tests."""

from unittest.mock import AsyncMock

import pytest

from db.models_core import ChapterBody
from services.naturalization import (
    NaturalizationStaleError,
    accept_finding,
    scan_naturalization,
)


def _body_json(text: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"pid": "p-1"},
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_scan_creates_explainable_finding(seed_project, async_db_session):
    await seed_project(chapter_ids=("ch-natural",))
    async_db_session.add(
        ChapterBody(
            chapter_id="ch-natural",
            content_html="<p>值得注意的是，她缓缓走进门，仿佛已经等了很久。</p>",
            content_json=_body_json("值得注意的是，她缓缓走进门，仿佛已经等了很久。"),
            rev=3,
        )
    )
    await async_db_session.flush()

    run = await scan_naturalization(
        async_db_session,
        user_id="user_a",
        project_id="proj_a",
        chapter_id="ch-natural",
    )
    assert run.source_body_rev == 3
    assert run.finding_count == 1
    # The run and finding are separate tables; verify the candidate through a query
    # to keep the test independent of ORM relationship configuration.
    from sqlalchemy import select
    from db.models_naturalization import NaturalizationFinding

    finding = await async_db_session.scalar(select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id))
    assert finding is not None
    assert finding.paragraph_id == "p-1"
    assert finding.candidate_text != finding.original_text
    assert "template.transition" in finding.rule_ids


@pytest.mark.asyncio
async def test_accept_replaces_range_and_preserves_pid(seed_project, async_db_session, monkeypatch):
    await seed_project(chapter_ids=("ch-accept",))
    original = "值得注意的是，她缓缓走进门，仿佛已经等了很久。"
    async_db_session.add(
        ChapterBody(
            chapter_id="ch-accept",
            content_html=f"<p>{original}</p>",
            content_json=_body_json(original),
            rev=1,
        )
    )
    await async_db_session.flush()
    monkeypatch.setattr("services.naturalization.OutboxService.enqueue", AsyncMock())
    run = await scan_naturalization(
        async_db_session, user_id="user_a", project_id="proj_a", chapter_id="ch-accept"
    )
    from sqlalchemy import select
    from db.models_naturalization import NaturalizationFinding

    finding = await async_db_session.scalar(select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id))
    assert finding is not None
    accepted, _, new_rev = await accept_finding(async_db_session, finding_id=finding.id, project_id="proj_a")
    await async_db_session.flush()
    body = await async_db_session.get(ChapterBody, "ch-accept")
    assert accepted.status == "accepted"
    assert new_rev == 2
    assert body is not None and body.rev == 2
    paragraph = body.content_json["content"][0]
    assert paragraph["attrs"]["pid"] == "p-1"
    text = paragraph["content"][0]["text"]
    assert text == finding.candidate_text


@pytest.mark.asyncio
async def test_accept_replaces_only_the_matching_html_paragraph(
    seed_project, async_db_session, monkeypatch
):
    await seed_project(chapter_ids=("ch-duplicate",))
    original = "值得注意的是，她缓缓走进门，仿佛已经等了很久。"
    content_json = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"pid": paragraph_id},
                "content": [{"type": "text", "text": original}],
            }
            for paragraph_id in ("p-1", "p-2")
        ],
    }
    async_db_session.add(
        ChapterBody(
            chapter_id="ch-duplicate",
            content_html=(
                f'<p data-paragraph-id="p-1">{original}</p>'
                f'<p data-paragraph-id="p-2">{original}</p>'
            ),
            content_json=content_json,
            rev=1,
        )
    )
    await async_db_session.flush()
    monkeypatch.setattr("services.naturalization.OutboxService.enqueue", AsyncMock())
    run = await scan_naturalization(
        async_db_session,
        user_id="user_a",
        project_id="proj_a",
        chapter_id="ch-duplicate",
    )
    from sqlalchemy import select
    from db.models_naturalization import NaturalizationFinding

    findings = list(
        (
            await async_db_session.execute(
                select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id)
            )
        ).scalars()
    )
    finding = next(item for item in findings if item.paragraph_id == "p-2")

    await accept_finding(async_db_session, finding_id=finding.id, project_id="proj_a")
    body = await async_db_session.get(ChapterBody, "ch-duplicate")

    assert body is not None
    assert f'<p data-paragraph-id="p-1">{original}</p>' in body.content_html
    assert f'<p data-paragraph-id="p-2">{finding.candidate_text}</p>' in body.content_html


@pytest.mark.asyncio
async def test_accept_rejects_stale_body(seed_project, async_db_session, monkeypatch):
    await seed_project(chapter_ids=("ch-stale",))
    original = "值得注意的是，她缓缓走进门，仿佛已经等了很久。"
    body = ChapterBody(
        chapter_id="ch-stale", content_html=f"<p>{original}</p>", content_json=_body_json(original), rev=1
    )
    async_db_session.add(body)
    await async_db_session.flush()
    run = await scan_naturalization(
        async_db_session, user_id="user_a", project_id="proj_a", chapter_id="ch-stale"
    )
    from sqlalchemy import select
    from db.models_naturalization import NaturalizationFinding

    finding = await async_db_session.scalar(select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id))
    assert finding is not None
    body.content_json = _body_json("正文已经由作者改过。")
    with pytest.raises(NaturalizationStaleError):
        await accept_finding(async_db_session, finding_id=finding.id, project_id="proj_a")
    assert finding.status == "stale"
