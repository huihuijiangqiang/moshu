"""Atomic, preview-first find/replace across a manuscript."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db.models_codex import CodexAlias, CodexEntry
from db.models_core import Chapter, ChapterBody, ChapterVersion, User, Volume
from db.models_editing import TextReplacementRun
from db.session import get_db
from services.body import BodyRevisionConflictError, save_chapter_body
from services.idempotency import IdempotencyConflictError, IdempotencyService
from services.text_replacement import find_matches, replace_document, replace_html

router = APIRouter()
MAX_MATCHES = 5000


class ReplacementSpec(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    replacement: str = Field(max_length=2000)
    scope: Literal["chapter", "volume", "project"] = "project"
    chapter_id: str | None = Field(default=None, max_length=32)
    volume_id: str | None = Field(default=None, max_length=32)
    case_sensitive: bool = True

    @model_validator(mode="after")
    def validate_scope_target(self):
        if self.scope == "chapter" and not self.chapter_id:
            raise ValueError("chapter_id is required for chapter scope")
        if self.scope == "volume" and not self.volume_id:
            raise ValueError("volume_id is required for volume scope")
        if (self.query if self.case_sensitive else self.query.casefold()) == (
            self.replacement if self.case_sensitive else self.replacement.casefold()
        ):
            raise ValueError("replacement must change the matched text")
        return self


class ReplacementExecuteRequest(ReplacementSpec):
    preview_token: str = Field(min_length=64, max_length=64)
    selected_match_ids: list[str] = Field(min_length=1, max_length=MAX_MATCHES)
    acknowledge_codex_risk: bool = False


def _preview_token(spec: ReplacementSpec, rows: list[tuple[Chapter, ChapterBody]]) -> str:
    payload = {
        "query": spec.query,
        "replacement": spec.replacement,
        "scope": spec.scope,
        "chapter_id": spec.chapter_id,
        "volume_id": spec.volume_id,
        "case_sensitive": spec.case_sensitive,
        "chapters": [(chapter.id, body.rev) for chapter, body in rows],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


async def _scope_rows(
    project_id: str,
    spec: ReplacementSpec,
    db: AsyncSession,
    *,
    lock: bool = False,
) -> list[tuple[Chapter, ChapterBody]]:
    if spec.scope == "chapter":
        target = await db.scalar(
            select(Chapter.id).where(
                Chapter.id == spec.chapter_id,
                Chapter.project_id == project_id,
                Chapter.deleted_at.is_(None),
            )
        )
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"})
    elif spec.scope == "volume":
        target = await db.scalar(
            select(Volume.id).where(
                Volume.id == spec.volume_id,
                Volume.project_id == project_id,
                Volume.deleted_at.is_(None),
            )
        )
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "VOLUME_NOT_FOUND"})

    statement = (
        select(Chapter, ChapterBody)
        .join(ChapterBody, ChapterBody.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id, Chapter.deleted_at.is_(None))
        .order_by(Chapter.idx, Chapter.id)
    )
    if spec.scope == "chapter":
        statement = statement.where(Chapter.id == spec.chapter_id)
    elif spec.scope == "volume":
        statement = statement.where(Chapter.volume_id == spec.volume_id)
    if lock:
        statement = statement.with_for_update()
    return list((await db.execute(statement)).all())


async def _codex_warnings(project_id: str, query: str, case_sensitive: bool, db: AsyncSession) -> list[dict]:
    entries = list(
        (
            await db.execute(
                select(CodexEntry).where(CodexEntry.project_id == project_id, CodexEntry.status == "confirmed")
            )
        )
        .scalars()
        .all()
    )
    if not entries:
        return []
    aliases = list(
        (
            await db.execute(select(CodexAlias).where(CodexAlias.entry_id.in_([entry.id for entry in entries])))
        )
        .scalars()
        .all()
    )
    aliases_by_entry: dict[str, list[str]] = {}
    for alias in aliases:
        aliases_by_entry.setdefault(alias.entry_id, []).append(alias.alias)

    needle = query if case_sensitive else query.casefold()
    warnings: list[dict] = []
    for entry in entries:
        for term in [entry.name, *aliases_by_entry.get(entry.id, [])]:
            candidate = term if case_sensitive else term.casefold()
            if needle in candidate or candidate in needle:
                warnings.append(
                    {
                        "entry_id": entry.id,
                        "name": entry.name,
                        "kind": entry.kind,
                        "matched_term": term,
                        "referenced_chapters": len(entry.ref_chapters or []),
                    }
                )
                break
        if len(warnings) >= 20:
            break
    return warnings


async def _build_preview(project_id: str, spec: ReplacementSpec, db: AsyncSession, *, lock: bool = False) -> dict:
    rows = await _scope_rows(project_id, spec, db, lock=lock)
    matches: list[dict] = []
    chapters: list[dict] = []
    for chapter, body in rows:
        chapter_matches = find_matches(
            chapter.id,
            body.content_json,
            spec.query,
            case_sensitive=spec.case_sensitive,
        )
        if chapter_matches:
            chapters.append(
                {
                    "id": chapter.id,
                    "title": chapter.title,
                    "index": chapter.idx,
                    "volume_id": chapter.volume_id,
                    "rev": body.rev,
                    "match_count": len(chapter_matches),
                }
            )
            matches.extend(
                {
                    "id": match.id,
                    "chapter_id": chapter.id,
                    "chapter_title": chapter.title,
                    "chapter_index": chapter.idx,
                    "paragraph_id": match.paragraph_id,
                    "before": match.before,
                    "matched": match.matched,
                    "after": match.after,
                    "replacement": spec.replacement,
                    "ordinal": match.ordinal,
                }
                for match in chapter_matches
            )
        if len(matches) > MAX_MATCHES:
            raise HTTPException(status_code=413, detail={"code": "TOO_MANY_MATCHES", "limit": MAX_MATCHES})
    return {
        "preview_token": _preview_token(spec, rows),
        "total_matches": len(matches),
        "chapters": chapters,
        "matches": matches,
        "warnings": await _codex_warnings(project_id, spec.query, spec.case_sensitive, db),
        "_rows": rows,
    }


@router.post("/{project_id}/text-replacements/preview")
async def preview_replacement(
    project_id: str,
    request: ReplacementSpec,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    preview = await _build_preview(project_id, request, db)
    preview.pop("_rows")
    return preview


@router.post("/{project_id}/text-replacements")
async def execute_replacement(
    project_id: str,
    request: ReplacementExecuteRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_project_permission(project_id, ProjectPermission.EDIT_BODY, user, db)
    scope = f"text_replace:{project_id}:{user.id}"
    request_payload = request.model_dump()
    try:
        reservation = await IdempotencyService.reserve(db, scope, idempotency_key, request_payload)
    except IdempotencyConflictError:
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_CONFLICT"})
    if reservation["action"] == "replay":
        return reservation["body"]
    if reservation["action"] == "wait":
        raise HTTPException(status_code=202, detail={"code": "REQUEST_IN_PROGRESS"})

    preview = await _build_preview(project_id, request, db, lock=True)
    if preview["preview_token"] != request.preview_token:
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": "PREVIEW_STALE"})
    if preview["warnings"] and not request.acknowledge_codex_risk:
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": "CODEX_RISK_NOT_ACKNOWLEDGED"})

    matches_by_id = {match["id"]: match for match in preview["matches"]}
    selected_ids = set(request.selected_match_ids)
    if not selected_ids.issubset(matches_by_id):
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": "MATCH_SET_STALE"})

    selected_by_chapter: dict[str, set[int]] = {}
    for match_id in selected_ids:
        match = matches_by_id[match_id]
        selected_by_chapter.setdefault(match["chapter_id"], set()).add(match["ordinal"])

    affected: list[dict] = []
    try:
        for chapter, body in preview["_rows"]:
            selected_ordinals = selected_by_chapter.get(chapter.id)
            if not selected_ordinals:
                continue
            updated_json = replace_document(
                body.content_json,
                request.query,
                request.replacement,
                selected_ordinals,
                case_sensitive=request.case_sensitive,
            )
            updated_html, html_match_count = replace_html(
                body.content_html,
                request.query,
                request.replacement,
                selected_ordinals,
                case_sensitive=request.case_sensitive,
            )
            chapter_match_count = sum(match["chapter_id"] == chapter.id for match in preview["matches"])
            if html_match_count != chapter_match_count:
                raise HTTPException(status_code=409, detail={"code": "BODY_REPRESENTATION_MISMATCH"})
            before_rev = body.rev
            result = await save_chapter_body(
                db,
                chapter_id=chapter.id,
                base_rev=before_rev,
                content_html=updated_html,
                content_json=updated_json,
                trigger="bulk_replace",
            )
            affected.append(
                {
                    "chapter_id": chapter.id,
                    "chapter_title": chapter.title,
                    "before_rev": before_rev,
                    "after_rev": result["rev"],
                    "match_count": len(selected_ordinals),
                }
            )
    except BodyRevisionConflictError:
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": "PREVIEW_STALE"})
    except HTTPException:
        await db.rollback()
        raise

    run = TextReplacementRun(
        id=uuid.uuid4().hex,
        user_id=user.id,
        project_id=project_id,
        status="applied",
        query_text=request.query,
        replacement_text=request.replacement,
        scope=request.scope,
        case_sensitive=request.case_sensitive,
        total_matches=len(selected_ids),
        affected_chapters=affected,
    )
    db.add(run)
    response = {
        "id": run.id,
        "status": run.status,
        "total_matches": run.total_matches,
        "affected_chapters": affected,
    }
    await IdempotencyService.complete(
        db,
        scope,
        idempotency_key,
        reservation["owner_token"],
        200,
        response,
    )
    await db.commit()
    return response


@router.post("/{project_id}/text-replacements/{run_id}/undo")
async def undo_replacement(
    project_id: str,
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_project_permission(project_id, ProjectPermission.EDIT_BODY, user, db)
    run = await db.scalar(
        select(TextReplacementRun)
        .where(TextReplacementRun.id == run_id, TextReplacementRun.project_id == project_id)
        .with_for_update()
    )
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "TEXT_REPLACEMENT_NOT_FOUND"})
    if run.status == "undone":
        return {"id": run.id, "status": run.status, "affected_chapters": run.affected_chapters}

    affected = run.affected_chapters or []
    chapter_ids = [item["chapter_id"] for item in affected]
    bodies = {
        body.chapter_id: body
        for body in (
            await db.execute(select(ChapterBody).where(ChapterBody.chapter_id.in_(chapter_ids)).with_for_update())
        )
        .scalars()
        .all()
    }
    conflicts = [
        item["chapter_id"]
        for item in affected
        if item["chapter_id"] not in bodies or bodies[item["chapter_id"]].rev != item["after_rev"]
    ]
    if conflicts:
        raise HTTPException(status_code=409, detail={"code": "UNDO_CONFLICT", "chapter_ids": conflicts})

    for item in affected:
        version = await db.scalar(
            select(ChapterVersion).where(
                ChapterVersion.chapter_id == item["chapter_id"],
                ChapterVersion.rev == item["before_rev"],
            )
        )
        if version is None:
            await db.rollback()
            raise HTTPException(status_code=409, detail={"code": "UNDO_VERSION_MISSING"})
        await save_chapter_body(
            db,
            chapter_id=item["chapter_id"],
            base_rev=item["after_rev"],
            content_html=version.content_html,
            content_json=version.content_json,
            trigger="bulk_replace_undo",
        )
    run.status = "undone"
    run.undone_at = datetime.now(UTC)
    await db.commit()
    return {"id": run.id, "status": run.status, "affected_chapters": affected}
