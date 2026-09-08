"""Transactional chapter scene-card operations.

Scene cards complement (rather than replace) the legacy ``Chapter.outline``
string list.  Every write is checked against the chapter's current outline and
body revisions so a stale planning panel cannot silently overwrite newer work.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexEntry
from db.models_consistency import ChapterOutlineState
from db.models_core import Chapter, ChapterBody
from db.models_scene_cards import ChapterScene
from domain.outlines import BodyPolicy
from services.outbox import OutboxService


class SceneChapterNotFoundError(LookupError):
    pass


class SceneNotFoundError(LookupError):
    pass


class SceneRevisionConflictError(RuntimeError):
    def __init__(self, expected: int, actual: int, scene: ChapterScene):
        self.expected = expected
        self.actual = actual
        self.scene = scene
        super().__init__(f"scene revision conflict: expected={expected}, actual={actual}")


class ScenePlanRevisionConflictError(RuntimeError):
    def __init__(self, expected_outline: int, actual_outline: int, expected_body: int | None, actual_body: int | None):
        self.expected_outline = expected_outline
        self.actual_outline = actual_outline
        self.expected_body = expected_body
        self.actual_body = actual_body
        super().__init__("chapter plan revision is stale")


class SceneBodyPolicyRequiredError(ValueError):
    pass


class SceneOrderError(ValueError):
    pass


class SceneReferenceError(ValueError):
    pass


@dataclass(frozen=True)
class SceneRevision:
    outline_rev: int
    body_rev: int | None


def scene_id() -> str:
    return f"sc_{uuid4().hex[:24]}"


async def _chapter_context(
    db: AsyncSession,
    chapter_id: str,
    *,
    lock: bool = False,
) -> tuple[Chapter, ChapterOutlineState | None, ChapterBody | None, SceneRevision]:
    statement = select(Chapter).where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
    if lock:
        statement = statement.with_for_update()
    chapter = (await db.execute(statement)).scalar_one_or_none()
    if chapter is None:
        raise SceneChapterNotFoundError(chapter_id)
    state_statement = select(ChapterOutlineState).where(ChapterOutlineState.chapter_id == chapter_id)
    if lock:
        state_statement = state_statement.with_for_update()
    state = (await db.execute(state_statement)).scalar_one_or_none()
    body = (await db.execute(select(ChapterBody).where(ChapterBody.chapter_id == chapter_id))).scalar_one_or_none()
    revision = SceneRevision(outline_rev=state.revision if state else 0, body_rev=body.rev if body else None)
    return chapter, state, body, revision


def _check_plan_revision(
    expected_outline_rev: int,
    expected_body_rev: int | None,
    current: SceneRevision,
) -> None:
    expected_body = expected_body_rev
    # A body-less chapter is represented as None on the server.  Clients use 0
    # for that state, which keeps the API convenient for newly imported books.
    if expected_body == 0 and current.body_rev is None:
        expected_body = None
    if expected_outline_rev != current.outline_rev or expected_body != current.body_rev:
        raise ScenePlanRevisionConflictError(
            expected_outline_rev,
            current.outline_rev,
            expected_body_rev,
            current.body_rev,
        )


def _require_body_policy(body: ChapterBody | None, body_policy: BodyPolicy | None) -> None:
    if body is not None and body_policy is None:
        raise SceneBodyPolicyRequiredError("existing body requires an explicit body policy")


def _apply_revision_marker(
    db: AsyncSession,
    state: ChapterOutlineState | None,
    chapter_id: str,
    revision: SceneRevision,
    body: ChapterBody | None,
    body_policy: BodyPolicy | None,
) -> ChapterOutlineState | None:
    _require_body_policy(body, body_policy)
    if body is None or body_policy is not BodyPolicy.MARK_BODY_FOR_REVISION:
        return state
    if state is None:
        state = ChapterOutlineState(chapter_id=chapter_id)
        db.add(state)
    state.body_needs_revision = True
    state.marked_outline_rev = revision.outline_rev
    state.marked_body_rev = revision.body_rev
    return state


async def _emit_scene_event(db: AsyncSession, scene: ChapterScene, project_id: str, event: str) -> None:
    await OutboxService.enqueue(
        db,
        topic=event,
        aggregate_id=scene.id,
        aggregate_rev=scene.rev,
        payload={
            "project_id": project_id,
            "chapter_id": scene.chapter_id,
            "scene_id": scene.id,
            "scene_rev": scene.rev,
            "outline_rev": scene.outline_rev,
            "body_rev": scene.body_rev,
        },
    )


def normalize_scene_fields(data: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "pov_entry_id",
        "location_entry_id",
        "goal",
        "obstacle",
        "turn",
        "info_gain",
        "emotion_shift",
        "hook",
        "status",
    }
    normalized = {key: value for key, value in data.items() if key in fields}
    # Text/status columns are non-nullable.  ``None`` in a PATCH means the
    # client omitted a value (IDs are the only fields where null clears data).
    normalized = {
        key: value
        for key, value in normalized.items()
        if value is not None or key in {"pov_entry_id", "location_entry_id"}
    }
    for key in ("goal", "obstacle", "turn", "info_gain", "emotion_shift", "hook"):
        if key in normalized and normalized[key] is not None:
            normalized[key] = str(normalized[key]).strip()
    return normalized


async def _validate_scene_refs(
    db: AsyncSession,
    *,
    project_id: str,
    fields: dict[str, Any],
) -> None:
    expected_kinds = {
        "pov_entry_id": "character",
        "location_entry_id": "location",
    }
    requested = {
        field: value
        for field, value in fields.items()
        if field in expected_kinds and value is not None
    }
    if not requested:
        return
    entries = {
        entry.id: entry
        for entry in (await db.execute(
            select(CodexEntry).where(CodexEntry.id.in_(requested.values()))
        )).scalars()
    }
    for field, entry_id in requested.items():
        entry = entries.get(entry_id)
        if (
            entry is None
            or entry.project_id != project_id
            or entry.kind != expected_kinds[field]
            or entry.status != "confirmed"
        ):
            raise SceneReferenceError(f"invalid {field}: {entry_id}")


async def list_scenes(
    db: AsyncSession,
    chapter_id: str,
    *,
    include_archived: bool = False,
) -> list[ChapterScene]:
    await _chapter_context(db, chapter_id)
    statement = select(ChapterScene).where(ChapterScene.chapter_id == chapter_id)
    if not include_archived:
        statement = statement.where(ChapterScene.status != "archived")
    result = await db.execute(statement.order_by(ChapterScene.order, ChapterScene.created_at, ChapterScene.id))
    return list(result.scalars().all())


async def create_scene(
    db: AsyncSession,
    *,
    chapter_id: str,
    order: int,
    fields: dict[str, Any],
    base_outline_rev: int,
    base_body_rev: int | None,
    body_policy: BodyPolicy | None,
    project_id: str | None = None,
) -> ChapterScene:
    if order < 1:
        raise SceneOrderError("scene order must be positive")
    chapter, state, body, revision = await _chapter_context(db, chapter_id, lock=True)
    _check_plan_revision(base_outline_rev, base_body_rev, revision)
    _require_body_policy(body, body_policy)
    normalized = normalize_scene_fields(fields)
    await _validate_scene_refs(db, project_id=chapter.project_id, fields=normalized)
    existing = await db.scalar(select(ChapterScene).where(ChapterScene.chapter_id == chapter_id, ChapterScene.order == order))
    if existing is not None:
        raise SceneOrderError(f"scene order {order} already exists")
    _apply_revision_marker(db, state, chapter_id, revision, body, body_policy)
    scene = ChapterScene(
        id=scene_id(),
        chapter_id=chapter_id,
        order=order,
        outline_rev=revision.outline_rev,
        body_rev=revision.body_rev,
        **normalized,
    )
    db.add(scene)
    await db.flush()
    await _emit_scene_event(db, scene, project_id or chapter.project_id, "chapter.scene_updated")
    return scene


async def update_scene(
    db: AsyncSession,
    *,
    scene_id_value: str,
    expected_rev: int,
    fields: dict[str, Any],
    base_outline_rev: int,
    base_body_rev: int | None,
    body_policy: BodyPolicy | None,
) -> ChapterScene:
    scene = (await db.execute(select(ChapterScene).where(ChapterScene.id == scene_id_value).with_for_update())).scalar_one_or_none()
    if scene is None:
        raise SceneNotFoundError(scene_id_value)
    if scene.rev != expected_rev:
        raise SceneRevisionConflictError(expected_rev, scene.rev, scene)
    chapter, state, body, revision = await _chapter_context(db, scene.chapter_id, lock=True)
    _check_plan_revision(base_outline_rev, base_body_rev, revision)
    updates = normalize_scene_fields(fields)
    await _validate_scene_refs(db, project_id=chapter.project_id, fields=updates)
    if not updates and scene.outline_rev == revision.outline_rev and scene.body_rev == revision.body_rev:
        return scene
    _require_body_policy(body, body_policy)
    _apply_revision_marker(db, state, scene.chapter_id, revision, body, body_policy)
    for key, value in updates.items():
        setattr(scene, key, value)
    scene.outline_rev = revision.outline_rev
    scene.body_rev = revision.body_rev
    scene.rev += 1
    if scene.status == "archived" and "status" in updates and updates.get("status") != "archived":
        scene.archived_at = None
    await db.flush()
    await _emit_scene_event(db, scene, chapter.project_id, "chapter.scene_updated")
    return scene


async def reorder_scenes(
    db: AsyncSession,
    *,
    chapter_id: str,
    scene_ids: list[str],
    base_outline_rev: int,
    base_body_rev: int | None,
    body_policy: BodyPolicy | None,
) -> list[ChapterScene]:
    chapter, state, body, revision = await _chapter_context(db, chapter_id, lock=True)
    _check_plan_revision(base_outline_rev, base_body_rev, revision)
    scenes = list(
        (await db.execute(
            select(ChapterScene).where(ChapterScene.chapter_id == chapter_id, ChapterScene.status != "archived").with_for_update()
        )).scalars().all()
    )
    active_ids = {item.id for item in scenes}
    if len(scene_ids) != len(active_ids) or set(scene_ids) != active_ids:
        raise SceneOrderError("scene_ids must contain every active scene exactly once")
    current_ids = [item.id for item in sorted(scenes, key=lambda item: item.order)]
    if scene_ids == current_ids:
        return scenes
    _require_body_policy(body, body_policy)
    _apply_revision_marker(db, state, chapter_id, revision, body, body_policy)
    by_id = {item.id: item for item in scenes}
    # Move through a disjoint positive range to satisfy both the unique and
    # positive-order constraints even when two cards swap places.
    for position, item in enumerate(scenes, start=1):
        item.order = 1_000_000 + position
    await db.flush()
    for position, item_id in enumerate(scene_ids, start=1):
        item = by_id[item_id]
        item.order = position
        item.outline_rev = revision.outline_rev
        item.body_rev = revision.body_rev
        item.rev += 1
        await _emit_scene_event(db, item, chapter.project_id, "chapter.scene_reordered")
    await db.flush()
    return [by_id[item_id] for item_id in scene_ids]


async def archive_scene(
    db: AsyncSession,
    *,
    scene_id_value: str,
    expected_rev: int,
    base_outline_rev: int,
    base_body_rev: int | None,
    body_policy: BodyPolicy | None,
) -> ChapterScene:
    scene = (await db.execute(select(ChapterScene).where(ChapterScene.id == scene_id_value).with_for_update())).scalar_one_or_none()
    if scene is None:
        raise SceneNotFoundError(scene_id_value)
    if scene.rev != expected_rev:
        raise SceneRevisionConflictError(expected_rev, scene.rev, scene)
    if scene.status == "archived":
        return scene
    chapter, state, body, revision = await _chapter_context(db, scene.chapter_id, lock=True)
    _check_plan_revision(base_outline_rev, base_body_rev, revision)
    _require_body_policy(body, body_policy)
    _apply_revision_marker(db, state, scene.chapter_id, revision, body, body_policy)
    max_order = await db.scalar(select(func.max(ChapterScene.order)).where(ChapterScene.chapter_id == scene.chapter_id))
    # Archived cards stay addressable but release their active slot so a new
    # scene can take the former position without violating the unique index.
    scene.order = max(int(max_order or 0) + 1, scene.order)
    scene.status = "archived"
    scene.archived_at = datetime.now(UTC)
    scene.outline_rev = revision.outline_rev
    scene.body_rev = revision.body_rev
    scene.rev += 1
    await db.flush()
    await _emit_scene_event(db, scene, chapter.project_id, "chapter.scene_archived")
    return scene
