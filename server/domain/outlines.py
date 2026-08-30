"""Chapter-outline transitions and body-safety invariants."""

from dataclasses import dataclass
from enum import StrEnum


class BodyPolicy(StrEnum):
    """Author choice when an edited plan already has body text."""

    PLAN_ONLY = "plan_only"
    MARK_BODY_FOR_REVISION = "mark_body_for_revision"


class BodyPolicyRequiredError(ValueError):
    """Raised when an existing body requires an explicit author choice."""


class BodyRevisionResolutionConflictError(ValueError):
    """Raised when a stale body or outline tries to clear the marker."""


@dataclass(frozen=True)
class OutlineContent:
    title: str
    nodes: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class OutlineState:
    revision: int = 0
    body_needs_revision: bool = False
    marked_outline_rev: int | None = None
    marked_body_rev: int | None = None


@dataclass(frozen=True)
class OutlineTransition:
    content: OutlineContent
    state: OutlineState
    body_policy: BodyPolicy | None
    body_rev_at_change: int | None
    changed: bool


def normalize_outline(title: str, nodes: list[str] | tuple[str, ...], note: str) -> OutlineContent:
    """Normalize author input before comparing or persisting a plan."""

    return OutlineContent(
        title=title.strip(),
        nodes=tuple(node.strip() for node in nodes if node.strip()),
        note=note.strip(),
    )


def plan_outline_transition(
    *,
    current: OutlineContent,
    state: OutlineState,
    requested: OutlineContent,
    body_rev: int | None,
    body_policy: BodyPolicy | None,
) -> OutlineTransition:
    """Compute a plan update without exposing any operation that can edit body text."""

    if requested == current:
        return OutlineTransition(
            content=current,
            state=state,
            body_policy=None,
            body_rev_at_change=None,
            changed=False,
        )

    has_body = body_rev is not None
    if has_body and body_policy is None:
        raise BodyPolicyRequiredError("existing body requires an explicit body policy")

    effective_policy = body_policy if has_body else BodyPolicy.PLAN_ONLY
    next_revision = state.revision + 1
    mark_requested = effective_policy is BodyPolicy.MARK_BODY_FOR_REVISION
    marker_stays_set = state.body_needs_revision or mark_requested

    if mark_requested:
        marked_outline_rev = next_revision
        marked_body_rev = body_rev
    elif state.body_needs_revision:
        marked_outline_rev = state.marked_outline_rev
        marked_body_rev = state.marked_body_rev
    else:
        marked_outline_rev = None
        marked_body_rev = None

    return OutlineTransition(
        content=requested,
        state=OutlineState(
            revision=next_revision,
            body_needs_revision=marker_stays_set,
            marked_outline_rev=marked_outline_rev,
            marked_body_rev=marked_body_rev,
        ),
        body_policy=effective_policy,
        body_rev_at_change=body_rev,
        changed=True,
    )


def resolve_body_revision_marker(
    *,
    state: OutlineState,
    current_body_rev: int,
    base_body_rev: int,
    addressed_outline_revision: int,
) -> OutlineState:
    """Clear the marker only after an explicit, current author acknowledgement."""

    if not state.body_needs_revision:
        return state
    if base_body_rev != current_body_rev:
        raise BodyRevisionResolutionConflictError("body revision changed before acknowledgement")
    if addressed_outline_revision != state.revision:
        raise BodyRevisionResolutionConflictError("outline revision changed before acknowledgement")

    return OutlineState(revision=state.revision)
