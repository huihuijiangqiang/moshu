"""Narrative chapter state history for Codex entries."""

from collections import defaultdict
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from db.models_codex import CodexEntry, CodexStateChange
from db.models_consistency_extended import ConsistencyClaim
from db.models_core import Chapter


class CodexStateError(ValueError):
    pass


class CodexStateNotFoundError(LookupError):
    pass


class CodexStateConflictError(RuntimeError):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__(f"Codex state revision conflict: current={current_revision}")


def _clean_required(value: str, *, field: str, limit: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise CodexStateError(f"{field} is required")
    if len(cleaned) > limit:
        raise CodexStateError(f"{field} exceeds {limit} characters")
    return cleaned


def _clean_note(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > 2000:
        raise CodexStateError("note exceeds 2000 characters")
    return cleaned or None


async def _confirmed_entry(
    db: AsyncSession, *, project_id: str, entry_id: str, lock: bool = False
) -> CodexEntry:
    statement = select(CodexEntry).where(
        CodexEntry.id == entry_id,
        CodexEntry.project_id == project_id,
        CodexEntry.status == "confirmed",
    )
    if lock:
        statement = statement.with_for_update()
    entry = await db.scalar(statement)
    if entry is None:
        raise CodexStateNotFoundError("confirmed Codex entry not found")
    return entry


async def _active_chapter(
    db: AsyncSession, *, project_id: str, chapter_id: str
) -> Chapter:
    chapter = await db.scalar(
        select(Chapter).where(
            Chapter.id == chapter_id,
            Chapter.project_id == project_id,
            Chapter.deleted_at.is_(None),
        )
    )
    if chapter is None:
        raise CodexStateError("chapter does not belong to this project")
    return chapter


async def _ensure_no_duplicate(
    db: AsyncSession,
    *,
    entry_id: str,
    chapter_id: str,
    state_key: str,
    exclude_id: str | None = None,
) -> None:
    statement = select(CodexStateChange.id).where(
        CodexStateChange.entry_id == entry_id,
        CodexStateChange.chapter_id == chapter_id,
        CodexStateChange.state_key == state_key,
        CodexStateChange.status == "active",
    )
    if exclude_id is not None:
        statement = statement.where(CodexStateChange.id != exclude_id)
    if await db.scalar(statement) is not None:
        raise CodexStateError("this state key already has a change in the selected chapter")


async def create_state_change(
    db: AsyncSession,
    *,
    project_id: str,
    entry_id: str,
    actor_id: str,
    chapter_id: str,
    state_key: str,
    value: str,
    note: str | None,
) -> CodexStateChange:
    await _confirmed_entry(db, project_id=project_id, entry_id=entry_id, lock=True)
    await _active_chapter(db, project_id=project_id, chapter_id=chapter_id)
    normalized_key = _clean_required(state_key, field="state_key", limit=100)
    normalized_value = _clean_required(value, field="value", limit=2000)
    await _ensure_no_duplicate(
        db,
        entry_id=entry_id,
        chapter_id=chapter_id,
        state_key=normalized_key,
    )
    change = CodexStateChange(
        id=f"cs_{uuid4().hex[:24]}",
        project_id=project_id,
        entry_id=entry_id,
        chapter_id=chapter_id,
        state_key=normalized_key,
        value=normalized_value,
        note=_clean_note(note),
        status="active",
        rev=1,
        created_by=actor_id,
    )
    db.add(change)
    await db.flush()
    return change


async def _locked_change(
    db: AsyncSession, *, project_id: str, entry_id: str, change_id: str
) -> CodexStateChange:
    change = await db.scalar(
        select(CodexStateChange)
        .where(
            CodexStateChange.id == change_id,
            CodexStateChange.project_id == project_id,
            CodexStateChange.entry_id == entry_id,
            CodexStateChange.status == "active",
        )
        .with_for_update()
    )
    if change is None:
        raise CodexStateNotFoundError("Codex state change not found")
    return change


async def update_state_change(
    db: AsyncSession,
    *,
    project_id: str,
    entry_id: str,
    change_id: str,
    expected_revision: int,
    chapter_id: str,
    state_key: str,
    value: str,
    note: str | None,
) -> CodexStateChange:
    await _confirmed_entry(db, project_id=project_id, entry_id=entry_id, lock=True)
    change = await _locked_change(
        db, project_id=project_id, entry_id=entry_id, change_id=change_id
    )
    if change.rev != expected_revision:
        raise CodexStateConflictError(change.rev)
    await _active_chapter(db, project_id=project_id, chapter_id=chapter_id)
    normalized_key = _clean_required(state_key, field="state_key", limit=100)
    normalized_value = _clean_required(value, field="value", limit=2000)
    await _ensure_no_duplicate(
        db,
        entry_id=entry_id,
        chapter_id=chapter_id,
        state_key=normalized_key,
        exclude_id=change.id,
    )
    change.chapter_id = chapter_id
    change.state_key = normalized_key
    change.value = normalized_value
    change.note = _clean_note(note)
    change.rev += 1
    await db.flush()
    return change


async def archive_state_change(
    db: AsyncSession,
    *,
    project_id: str,
    entry_id: str,
    change_id: str,
    expected_revision: int,
) -> CodexStateChange:
    await _confirmed_entry(db, project_id=project_id, entry_id=entry_id, lock=True)
    change = await _locked_change(
        db, project_id=project_id, entry_id=entry_id, change_id=change_id
    )
    if change.rev != expected_revision:
        raise CodexStateConflictError(change.rev)
    change.status = "archived"
    change.rev += 1
    await db.flush()
    return change


def _author_item(change: CodexStateChange, chapter: Chapter) -> dict:
    return {
        "id": change.id,
        "source": "author",
        "editable": True,
        "state_key": change.state_key,
        "value": change.value,
        "polarity": "positive",
        "note": change.note,
        "chapter_id": chapter.id,
        "chapter_index": chapter.idx,
        "chapter_title": chapter.title,
        "body_revision": None,
        "paragraph_id": None,
        "confidence": None,
        "revision": change.rev,
        "created_at": change.created_at.isoformat(),
    }


async def list_state_history(
    db: AsyncSession, *, project_id: str, entry_id: str
) -> list[dict]:
    await _confirmed_entry(db, project_id=project_id, entry_id=entry_id)
    author_rows = (
        await db.execute(
            select(CodexStateChange, Chapter)
            .join(Chapter, Chapter.id == CodexStateChange.chapter_id)
            .where(
                CodexStateChange.project_id == project_id,
                CodexStateChange.entry_id == entry_id,
                CodexStateChange.status == "active",
                Chapter.deleted_at.is_(None),
            )
        )
    ).all()

    object_entry = aliased(CodexEntry)
    claim_rows = (
        await db.execute(
            select(ConsistencyClaim, Chapter, object_entry.name)
            .join(Chapter, Chapter.id == ConsistencyClaim.chapter_id)
            .outerjoin(object_entry, object_entry.id == ConsistencyClaim.object_entry_id)
            .where(
                ConsistencyClaim.project_id == project_id,
                ConsistencyClaim.subject_entry_id == entry_id,
                ConsistencyClaim.status == "accepted",
                ConsistencyClaim.source_kind.in_(("body", "outline", "resolution")),
                Chapter.deleted_at.is_(None),
            )
        )
    ).all()

    items = [_author_item(change, chapter) for change, chapter in author_rows]
    for claim, chapter, object_name in claim_rows:
        items.append(
            {
                "id": f"claim:{claim.id}",
                "source": "extracted" if claim.source_kind == "body" else claim.source_kind,
                "editable": False,
                "state_key": claim.predicate,
                "value": object_name or claim.object_value or "未记录值",
                "polarity": claim.polarity,
                "note": None,
                "chapter_id": chapter.id,
                "chapter_index": chapter.idx,
                "chapter_title": chapter.title,
                "body_revision": claim.body_rev,
                "paragraph_id": claim.paragraph_id or claim.source_anchor,
                "confidence": float(claim.confidence) if claim.confidence is not None else None,
                "revision": None,
                "created_at": claim.created_at.isoformat(),
            }
        )
    items.sort(
        key=lambda item: (
            int(item["chapter_index"]),
            str(item["state_key"]),
            0 if item["source"] == "author" else 1,
            str(item["id"]),
        )
    )
    return items


async def latest_author_states(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    entry_ids: list[str],
) -> dict[str, list[dict[str, object]]]:
    """Return each key's latest state at or before the target narrative chapter."""
    if not entry_ids:
        return {}
    target = await _active_chapter(db, project_id=project_id, chapter_id=chapter_id)
    rows = (
        await db.execute(
            select(CodexStateChange, Chapter)
            .join(Chapter, Chapter.id == CodexStateChange.chapter_id)
            .where(
                CodexStateChange.project_id == project_id,
                CodexStateChange.entry_id.in_(entry_ids),
                CodexStateChange.status == "active",
                Chapter.deleted_at.is_(None),
                Chapter.idx <= target.idx,
            )
            .order_by(
                CodexStateChange.entry_id,
                CodexStateChange.state_key,
                Chapter.idx.desc(),
                CodexStateChange.id.desc(),
            )
        )
    ).all()
    latest: dict[tuple[str, str], dict[str, object]] = {}
    for change, chapter in rows:
        latest.setdefault(
            (change.entry_id, change.state_key),
            {
                "state_key": change.state_key,
                "value": change.value,
                "chapter_id": chapter.id,
                "chapter_index": chapter.idx,
            },
        )
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for (entry_id, _state_key), item in latest.items():
        grouped[entry_id].append(item)
    for items in grouped.values():
        items.sort(key=lambda item: str(item["state_key"]))
    return dict(grouped)


__all__ = [
    "CodexStateConflictError",
    "CodexStateError",
    "CodexStateNotFoundError",
    "archive_state_change",
    "create_state_change",
    "latest_author_states",
    "list_state_history",
    "update_state_change",
]
