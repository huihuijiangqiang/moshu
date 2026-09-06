"""Author-managed POV and auditable character-presence statistics."""

from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexEntry, CodexRef
from db.models_consistency_extended import ConsistencyClaim
from db.models_core import Chapter


class PovRevisionConflictError(RuntimeError):
    def __init__(self, current_entry_id: str | None, current_revision: int):
        self.current_entry_id = current_entry_id
        self.current_revision = current_revision
        super().__init__(f"POV revision conflict: current={current_revision}")


class InvalidPovEntryError(ValueError):
    pass


class CharacterStatisticsNotFoundError(LookupError):
    pass


async def update_chapter_pov(
    db: AsyncSession,
    *,
    chapter_id: str,
    entry_id: str | None,
    expected_revision: int,
) -> Chapter:
    chapter = await db.scalar(
        select(Chapter)
        .where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
        .with_for_update()
    )
    if chapter is None:
        raise CharacterStatisticsNotFoundError(chapter_id)
    if chapter.pov_revision != expected_revision:
        raise PovRevisionConflictError(chapter.pov_entry_id, chapter.pov_revision)

    if entry_id is not None:
        entry = await db.scalar(
            select(CodexEntry).where(
                CodexEntry.id == entry_id,
                CodexEntry.project_id == chapter.project_id,
                CodexEntry.kind == "character",
                CodexEntry.status == "confirmed",
            )
        )
        if entry is None:
            raise InvalidPovEntryError(entry_id)

    chapter.pov_entry_id = entry_id
    chapter.pov_revision += 1
    await db.flush()
    return chapter


async def character_statistics(
    db: AsyncSession,
    *,
    project_id: str,
    entry_id: str,
) -> dict:
    """Merge explicit refs, accepted extracted facts and author POV decisions.

    Presence is entity-ID based. Name matching is deliberately excluded because aliases,
    renames and same-name characters make it impossible to audit.
    """
    entry = await db.scalar(
        select(CodexEntry).where(
            CodexEntry.id == entry_id,
            CodexEntry.project_id == project_id,
            CodexEntry.kind == "character",
            CodexEntry.status == "confirmed",
        )
    )
    if entry is None:
        raise CharacterStatisticsNotFoundError(entry_id)

    chapters = list(
        (
            await db.execute(
                select(Chapter)
                .where(Chapter.project_id == project_id, Chapter.deleted_at.is_(None))
                .order_by(Chapter.idx, Chapter.id)
            )
        )
        .scalars()
        .all()
    )
    chapter_by_id = {chapter.id: chapter for chapter in chapters}
    presence: dict[str, dict[str, int | bool]] = defaultdict(
        lambda: {"explicit_references": 0, "extracted_claims": 0, "is_pov": False}
    )

    reference_rows = (
        await db.execute(
            select(CodexRef.chapter_id, CodexRef.count)
            .join(Chapter, Chapter.id == CodexRef.chapter_id)
            .where(
                CodexRef.entry_id == entry_id,
                Chapter.project_id == project_id,
                Chapter.deleted_at.is_(None),
            )
        )
    ).all()
    for chapter_id, count in reference_rows:
        presence[chapter_id]["explicit_references"] = int(count)

    claim_rows = (
        await db.execute(
            select(
                ConsistencyClaim.id,
                ConsistencyClaim.chapter_id,
                ConsistencyClaim.subject_entry_id,
                ConsistencyClaim.object_entry_id,
            )
            .join(Chapter, Chapter.id == ConsistencyClaim.chapter_id)
            .where(
                ConsistencyClaim.project_id == project_id,
                ConsistencyClaim.source_kind == "body",
                ConsistencyClaim.status == "accepted",
                Chapter.deleted_at.is_(None),
                or_(
                    ConsistencyClaim.subject_entry_id == entry_id,
                    ConsistencyClaim.object_entry_id == entry_id,
                ),
            )
        )
    ).all()
    claim_ids_by_chapter: dict[str, set[int]] = defaultdict(set)
    for claim_id, chapter_id, _subject_id, _object_id in claim_rows:
        if chapter_id:
            claim_ids_by_chapter[chapter_id].add(int(claim_id))
    for chapter_id, claim_ids in claim_ids_by_chapter.items():
        presence[chapter_id]["extracted_claims"] = len(claim_ids)

    # Imported projects can carry legacy reference chapter IDs without CodexRef rows.
    for chapter_id in entry.ref_chapters or []:
        if chapter_id in chapter_by_id:
            presence[chapter_id]

    for chapter in chapters:
        if chapter.pov_entry_id == entry_id:
            presence[chapter.id]["is_pov"] = True

    points = []
    for chapter in chapters:
        source = presence.get(chapter.id)
        if source is None:
            continue
        points.append(
            {
                "chapter_id": chapter.id,
                "chapter_index": chapter.idx,
                "chapter_title": chapter.title,
                "words": chapter.words,
                "explicit_references": int(source["explicit_references"]),
                "extracted_claims": int(source["extracted_claims"]),
                "is_pov": bool(source["is_pov"]),
            }
        )

    first = points[0] if points else None
    last = points[-1] if points else None
    pov_points = [point for point in points if point["is_pov"]]
    explicit_references = sum(int(point["explicit_references"]) for point in points)
    extracted_claims = sum(int(point["extracted_claims"]) for point in points)
    max_chapter_index = chapters[-1].idx if chapters else 0
    return {
        "entry_id": entry.id,
        "name": entry.name,
        "appearance_chapters": len(points),
        "explicit_references": explicit_references,
        "extracted_claims": extracted_claims,
        "pov_chapters": len(pov_points),
        "pov_words": sum(int(point["words"]) for point in pov_points),
        "first_appearance": first["chapter_index"] if first else None,
        "last_appearance": last["chapter_index"] if last else None,
        "hiatus_chapters": max_chapter_index - int(last["chapter_index"]) if last else None,
        "chapters": points,
    }


__all__ = [
    "CharacterStatisticsNotFoundError",
    "InvalidPovEntryError",
    "PovRevisionConflictError",
    "character_statistics",
    "update_chapter_pov",
]
