import pytest

from domain.outlines import (
    BodyPolicy,
    BodyPolicyRequiredError,
    BodyRevisionResolutionConflictError,
    OutlineContent,
    OutlineState,
    normalize_outline,
    plan_outline_transition,
    resolve_body_revision_marker,
)


def test_normalize_outline_trims_and_removes_empty_nodes() -> None:
    assert normalize_outline(" 章名 ", [" 节点一 ", " ", "节点二"], " 注记 ") == OutlineContent(
        title="章名",
        nodes=("节点一", "节点二"),
        note="注记",
    )


def test_existing_body_requires_explicit_policy_for_real_change() -> None:
    with pytest.raises(BodyPolicyRequiredError):
        plan_outline_transition(
            current=OutlineContent("旧章", ("旧节点",), ""),
            state=OutlineState(revision=2),
            requested=OutlineContent("新章", ("新节点",), ""),
            body_rev=7,
            body_policy=None,
        )


def test_plan_only_never_sets_or_clears_marker() -> None:
    unmarked = plan_outline_transition(
        current=OutlineContent("旧章", (), ""),
        state=OutlineState(revision=2),
        requested=OutlineContent("新章", (), ""),
        body_rev=7,
        body_policy=BodyPolicy.PLAN_ONLY,
    )
    assert unmarked.state == OutlineState(revision=3)

    marked = plan_outline_transition(
        current=unmarked.content,
        state=OutlineState(
            revision=3,
            body_needs_revision=True,
            marked_outline_rev=2,
            marked_body_rev=6,
        ),
        requested=OutlineContent("更新章名", (), ""),
        body_rev=7,
        body_policy=BodyPolicy.PLAN_ONLY,
    )
    assert marked.state.body_needs_revision is True
    assert marked.state.marked_outline_rev == 2
    assert marked.state.marked_body_rev == 6


def test_mark_policy_records_exact_outline_and_body_revisions() -> None:
    transition = plan_outline_transition(
        current=OutlineContent("旧章", (), ""),
        state=OutlineState(revision=4),
        requested=OutlineContent("新章", ("节点",), ""),
        body_rev=9,
        body_policy=BodyPolicy.MARK_BODY_FOR_REVISION,
    )

    assert transition.changed is True
    assert transition.state == OutlineState(
        revision=5,
        body_needs_revision=True,
        marked_outline_rev=5,
        marked_body_rev=9,
    )
    assert transition.body_rev_at_change == 9


def test_noop_does_not_require_policy_or_create_revision() -> None:
    current = OutlineContent("章名", ("节点",), "注记")
    state = OutlineState(revision=5, body_needs_revision=True, marked_outline_rev=4, marked_body_rev=8)

    transition = plan_outline_transition(
        current=current,
        state=state,
        requested=current,
        body_rev=9,
        body_policy=None,
    )

    assert transition.changed is False
    assert transition.state is state


def test_new_chapter_normalizes_mark_policy_to_plan_only() -> None:
    transition = plan_outline_transition(
        current=OutlineContent("", (), ""),
        state=OutlineState(),
        requested=OutlineContent("开篇", ("冲突出现",), ""),
        body_rev=None,
        body_policy=BodyPolicy.MARK_BODY_FOR_REVISION,
    )

    assert transition.body_policy is BodyPolicy.PLAN_ONLY
    assert transition.state.body_needs_revision is False


def test_marker_clear_requires_current_body_and_outline_revisions() -> None:
    state = OutlineState(revision=5, body_needs_revision=True, marked_outline_rev=3, marked_body_rev=7)

    with pytest.raises(BodyRevisionResolutionConflictError):
        resolve_body_revision_marker(
            state=state,
            current_body_rev=9,
            base_body_rev=8,
            addressed_outline_revision=5,
        )
    with pytest.raises(BodyRevisionResolutionConflictError):
        resolve_body_revision_marker(
            state=state,
            current_body_rev=9,
            base_body_rev=9,
            addressed_outline_revision=4,
        )

    assert resolve_body_revision_marker(
        state=state,
        current_body_rev=9,
        base_body_rev=9,
        addressed_outline_revision=5,
    ) == OutlineState(revision=5)
