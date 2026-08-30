"""Schema-level tests for consistency persistence primitives."""

from sqlalchemy import CheckConstraint, UniqueConstraint

from db.models_consistency import (
    ChapterOutlineRevision,
    ChapterOutlineState,
    IdempotencyRecord,
    OutboxEvent,
)


def constraint_names(model: type) -> set[str]:
    return {constraint.name for constraint in model.__table__.constraints if constraint.name}


def test_outline_state_has_one_to_one_key_and_consistency_checks() -> None:
    table = ChapterOutlineState.__table__

    assert list(table.primary_key.columns.keys()) == ["chapter_id"]
    assert {foreign_key.target_fullname for foreign_key in table.c.chapter_id.foreign_keys} == {"chapters.id"}
    assert {
        "ck_outline_state_revision_nonnegative",
        "ck_outline_state_marker_consistent",
        "ck_outline_state_marked_revision_valid",
    } <= constraint_names(ChapterOutlineState)
    assert table.c.revision.server_default is not None
    assert table.c.body_needs_revision.server_default is not None


def test_outline_revision_is_versioned_and_author_is_optional() -> None:
    table = ChapterOutlineRevision.__table__
    unique_columns = {
        tuple(constraint.columns.keys()) for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    }

    assert ("chapter_id", "revision") in unique_columns
    assert table.c.created_by.nullable is True
    assert {foreign_key.target_fullname for foreign_key in table.c.created_by.foreign_keys} == {"users.id"}
    assert {"ck_body_policy_enum", "ck_outline_revision_positive"} <= constraint_names(ChapterOutlineRevision)


def test_outbox_schema_enforces_unique_event_and_valid_state() -> None:
    table = OutboxEvent.__table__
    unique_columns = {
        tuple(constraint.columns.keys()) for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    }

    assert ("topic", "aggregate_id", "aggregate_rev") in unique_columns
    assert {"ck_outbox_status_enum", "ck_outbox_attempts_nonnegative"} <= constraint_names(OutboxEvent)
    assert table.c.available_at.type.timezone is True
    assert table.c.lease_until.type.timezone is True


def test_idempotency_schema_supports_pending_then_completed() -> None:
    table = IdempotencyRecord.__table__
    unique_columns = {
        tuple(constraint.columns.keys()) for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    }

    assert ("scope", "key") in unique_columns
    assert table.c.response_status.nullable is True
    assert table.c.response_body.nullable is True
    assert table.c.expires_at.type.timezone is True
    assert {
        "ck_idempotency_status_enum",
        "ck_idempotency_phase_consistent",
    } <= constraint_names(IdempotencyRecord)


def test_every_declared_check_constraint_has_sql_text() -> None:
    for model in (ChapterOutlineState, ChapterOutlineRevision, OutboxEvent, IdempotencyRecord):
        checks = [constraint for constraint in model.__table__.constraints if isinstance(constraint, CheckConstraint)]
        assert checks
        assert all(str(constraint.sqltext).strip() for constraint in checks)
