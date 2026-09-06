"""候选集替换的服务层行为。

管道层面的行为（A/B -> 仅 A、字段变化、消失后复现、证据指针保持）在
tests/test_tasks_consistency.py 里走真实任务验证。这里补的是只有直接调用才能
构造出来的边界：身份字段的契约、重复行的收敛、缺省值。
"""
import pytest
from sqlalchemy import select

from db.models_consistency_extended import ConsistencyClaim
from services.claim_set import (
    DEFAULT_CONFIDENCE,
    MUTABLE_FIELDS,
    replace_body_claim_set,
)

SCOPE = {
    "project_id": "proj_test",
    "chapter_id": "ch_test",
    "body_rev": 1,
    "extractor_version": "1.0.0",
}


def payload(fingerprint: str, **overrides) -> dict:
    data = {
        "subject_text": "李长风",
        "predicate": "alive",
        "object_type": "scalar",
        "object_value": "true",
        "polarity": "positive",
        "certainty": "explicit",
        "confidence": 0.9,
        "fingerprint": fingerprint,
    }
    data.update(overrides)
    return data


@pytest.fixture
async def project(async_db_session, seed_project):
    await seed_project(project_id="proj_test", chapter_ids=("ch_test",))
    await async_db_session.commit()
    return async_db_session


async def load(db) -> list[ConsistencyClaim]:
    db.expunge_all()
    result = await db.execute(select(ConsistencyClaim).order_by(ConsistencyClaim.id))
    return list(result.scalars().all())


# --- 身份字段的契约 -----------------------------------------------------------


def test_identity_fields_are_never_refreshed():
    """身份字段变了就不是同一条 claim，只能走插入。

    把它们放进刷新列表，等于让一行 claim 在原地变成另一条事实：历史告警的证据
    还指着这个 id，指向的内容却已经换了。
    """
    for field in (
        "project_id",
        "chapter_id",
        "body_rev",
        "source_kind",
        "extractor_version",
        "fingerprint",
        "predicate",
        "object_type",
        "polarity",
        "status",
        "id",
    ):
        assert field not in MUTABLE_FIELDS


def test_recomputed_fields_are_all_refreshed():
    """每次重放都可能重算的字段都必须在刷新列表里。"""
    for field in (
        "subject_entry_id",
        "object_entry_id",
        "timeline_id",
        "story_order",
        "valid_from_order",
        "valid_to_order",
        "confidence",
        "source_anchor",
        "temporal_anchor_text",
        "temporal_anchor_value",
        "temporal_event_ref",
        "temporal_relation",
        "temporal_relation_ref",
        "order_basis",
        "order_confidence",
    ):
        assert field in MUTABLE_FIELDS


async def test_temporal_evidence_is_persisted_and_refreshed(project):
    first = payload(
        "fp_a",
        temporal_anchor_text="三日后",
        temporal_event_ref="抵达县城",
        temporal_relation="after",
        temporal_relation_ref="离开村庄",
        order_basis="relative_to_anchor",
        order_confidence=0.91,
    )
    await replace_body_claim_set(project, claims=[first], **SCOPE)
    await project.commit()

    row = (await load(project))[0]
    assert row.temporal_event_ref == "抵达县城"
    assert row.temporal_relation_ref == "离开村庄"
    assert row.order_basis == "relative_to_anchor"

    second = {**first, "temporal_event_ref": "抵达府城", "order_confidence": 0.93}
    await replace_body_claim_set(project, claims=[second], **SCOPE)
    await project.commit()

    row = (await load(project))[0]
    assert row.temporal_event_ref == "抵达府城"
    assert float(row.order_confidence) == pytest.approx(0.93)


async def test_author_time_decision_survives_only_unchanged_temporal_evidence(project):
    base = payload(
        "fp_a",
        timeline_id="main",
        temporal_anchor_text="过几日后",
        temporal_relation="after",
        temporal_relation_ref="启程",
        order_basis="relative_to_anchor",
        temporal_resolution={
            "author_override": {"offset_seconds": 4 * 86400},
            "author_override_version": 1,
            "author_override_history": [{"version": 1, "action": "confirm"}],
        },
    )
    await replace_body_claim_set(project, claims=[base], **SCOPE)
    await project.commit()

    replay = {
        **base,
        "temporal_resolution": {
            "original": "过几日后",
            "offset_min_seconds": 2 * 86400,
            "offset_max_seconds": 7 * 86400,
        },
    }
    await replace_body_claim_set(project, claims=[replay], **SCOPE)
    await project.commit()
    row = (await load(project))[0]
    assert row.temporal_resolution["author_override_version"] == 1

    changed_evidence = {**replay, "temporal_anchor_text": "一月后"}
    await replace_body_claim_set(project, claims=[changed_evidence], **SCOPE)
    await project.commit()
    row = (await load(project))[0]
    assert "author_override" not in row.temporal_resolution


# --- 行为 ---------------------------------------------------------------------


async def test_an_empty_result_supersedes_the_whole_set(project):
    """抽取这一版一条都没抽到时，上一轮的结论必须全部退出有效集合。"""
    await replace_body_claim_set(project, claims=[payload("fp_a")], **SCOPE)
    await project.commit()

    diff = await replace_body_claim_set(project, claims=[], **SCOPE)
    await project.commit()

    assert diff.superseded == 1
    rows = await load(project)
    assert [row.status for row in rows] == ["superseded"], "行被删掉了"


async def test_other_revisions_are_out_of_scope(project, make_claim):
    """集合替换只管本版本；别的版本、别的抽取器版本的行不受影响。"""
    project.add(
        make_claim(chapter_id="ch_test", body_rev=2, fingerprint="fp_other_rev")
    )
    project.add(
        make_claim(
            chapter_id="ch_test",
            body_rev=1,
            fingerprint="fp_other_extractor",
            extractor_version="2.0.0",
        )
    )
    await project.commit()

    diff = await replace_body_claim_set(project, claims=[payload("fp_a")], **SCOPE)
    await project.commit()

    assert diff.inserted == 1
    assert diff.superseded == 0
    statuses = {row.fingerprint: row.status for row in await load(project)}
    assert statuses["fp_other_rev"] == "accepted"
    assert statuses["fp_other_extractor"] == "accepted"


async def test_duplicate_rows_collapse_onto_the_oldest(project, make_claim):
    """同指纹重复行（SQLite 上没有部分唯一索引兜底）收敛到最早那行。

    最早那行才是历史证据指过来的那行；后来的重复行退出有效集合，而不是让两条
    同一事实并存、被规则各算一次。
    """
    for _ in range(2):
        project.add(make_claim(chapter_id="ch_test", body_rev=1, fingerprint="fp_a"))
    await project.commit()
    rows_before = await load(project)
    assert len(rows_before) == 2, "前置条件：先造出两行重复"
    canonical_id = rows_before[0].id

    diff = await replace_body_claim_set(project, claims=[payload("fp_a")], **SCOPE)
    await project.commit()

    assert diff.updated == 1
    assert diff.superseded == 1
    statuses = {row.id: row.status for row in await load(project)}
    assert statuses[canonical_id] == "accepted"
    assert statuses[rows_before[1].id] == "superseded"


async def test_a_missing_confidence_does_not_blank_the_stored_value(project):
    """模型没给 confidence 时用与插入相同的缺省值，不能把已有值刷成 NULL。"""
    await replace_body_claim_set(
        project, claims=[payload("fp_a", confidence=0.42)], **SCOPE
    )
    await project.commit()

    await replace_body_claim_set(
        project, claims=[payload("fp_a", confidence=None)], **SCOPE
    )
    await project.commit()

    row = (await load(project))[0]
    assert row.confidence is not None
    assert float(row.confidence) == pytest.approx(DEFAULT_CONFIDENCE)


async def test_the_caller_owns_the_transaction(project):
    """本函数只 flush 不 commit —— 集合替换必须与版本校验在同一个事务里落库。"""
    await replace_body_claim_set(project, claims=[payload("fp_a")], **SCOPE)
    await project.rollback()

    assert await load(project) == []
