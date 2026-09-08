"""Authenticated AI draft provenance reports."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, verify_project_access
from db.models_core import Chapter, ChapterBody, User
from db.models_usage import GenerationRun
from db.session import get_db
from services.provenance import (
    SENTENCE_RISK_VERSION,
    ProvenanceParagraph,
    classify_document,
    detect_suspected_sentences,
)

router = APIRouter()


def _summary(paragraphs: list[ProvenanceParagraph]) -> dict:
    counts = {"ai-raw": 0, "ai-edited": 0, "human": 0}
    for paragraph in paragraphs:
        counts[paragraph.source] += paragraph.words
    total = sum(counts.values())
    return {
        "total_words": total,
        "ai_raw_words": counts["ai-raw"],
        "ai_edited_words": counts["ai-edited"],
        "human_words": counts["human"],
    }


@router.get("/projects/{project_id}/provenance")
async def provenance_report(
    project_id: str,
    scope: Literal["chapter", "book"] = Query("chapter"),
    chapter_id: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await verify_project_access(project_id, user, db)
    statement = (
        select(Chapter, ChapterBody)
        .outerjoin(ChapterBody, ChapterBody.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id, Chapter.deleted_at.is_(None))
        .order_by(Chapter.idx)
    )
    if scope == "chapter":
        if not chapter_id:
            raise HTTPException(status_code=422, detail={"code": "CHAPTER_REQUIRED"})
        statement = statement.where(Chapter.id == chapter_id)
    rows = (await db.execute(statement)).all()
    if scope == "chapter" and not rows:
        raise HTTPException(status_code=404, detail="Chapter not found")

    known_runs = None
    if scope == "book":
        known_runs = {
            run.id: run
            for run in (await db.execute(
                select(GenerationRun).where(GenerationRun.project_id == project_id)
            )).scalars()
        }
    all_paragraphs: list[ProvenanceParagraph] = []
    for chapter, body in rows:
        if body is None:
            continue
        all_paragraphs.extend(await classify_document(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
            content_json=body.content_json,
            known_runs=known_runs,
        ))
    result = {"scope": scope, **_summary(all_paragraphs)}
    result["paragraphs"] = [
        {
            "id": paragraph.paragraph_id,
            "text": paragraph.text,
            "words": paragraph.words,
            "source": paragraph.source,
            "run_id": paragraph.run_id,
        }
        for paragraph in all_paragraphs
    ] if scope == "chapter" else []
    suspected = detect_suspected_sentences(all_paragraphs) if scope == "chapter" else []
    result["suspected_sentences"] = [
        {
            "id": item.id,
            "paragraph_id": item.paragraph_id,
            "text": item.text,
            "start": item.start,
            "end": item.end,
            "source": item.source,
            "score": item.score,
            "reasons": list(item.reasons),
            "risk_rules": [
                {
                    "id": risk.rule_id,
                    "category": risk.category,
                    "label": risk.label,
                    "score": risk.score,
                    "confidence": risk.confidence,
                }
                for risk in item.risks
            ],
        }
        for item in suspected
    ]
    result["sentence_risk_version"] = SENTENCE_RISK_VERSION
    result["sentence_risk_disclaimer"] = (
        "疑似句式来自可解释的文本规则，只提示需要人工复核的表达风险，不代表平台检测结论或 AI 鉴定。"
    )
    return result
