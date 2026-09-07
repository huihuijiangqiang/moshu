"""
部分唯一索引的真实行为验证。

⚠️ 本文件在本次开发中**从未运行过**：需要真实 PostgreSQL。
SQLite 上部分唯一索引（postgresql_where）被剔除，所以重复插入不会被拒绝。
只有在 PostgreSQL 上才能验证约束真的生效。
"""
import pytest
from sqlalchemy.exc import IntegrityError

from db.models_consistency_extended import ConsistencyClaim
from db.models_core import Chapter, Project, User
from tests.integration.conftest import requires_postgres

pytestmark = [pytest.mark.postgres, requires_postgres]


@pytest.fixture
async def seeded(pg_session):
    pg_session.add(
        User(
            id="user_pg",
            name="pg",
            email="pg@example.test",
            plan="free",
            quota_remaining=100,
            quota_total=100,
        )
    )
    await pg_session.flush()
    pg_session.add(
        Project(
            id="proj_pg",
            owner_id="user_pg",
            title="PG 项目",
            status="ongoing",
            target_words_daily=3000,
        )
    )
    await pg_session.flush()
    pg_session.add(
        Chapter(
            id="ch_pg",
            project_id="proj_pg",
            title="第一章",
            idx=1024,
            words=0,
            outline=[],
        )
    )
    await pg_session.commit()
    return pg_session


def claim_row(**overrides):
    fields = {
        "project_id": "proj_pg",
        "subject_text": "李长风",
        "predicate": "alive",
        "object_type": "scalar",
        "object_value": "true",
        "polarity": "positive",
        "certainty": "explicit",
        "source_kind": "body",
        "chapter_id": "ch_pg",
        "body_rev": 1,
        "extractor_version": "1.0.0",
        "fingerprint": "fp_unique",
        "status": "accepted",
    }
    fields.update(overrides)
    return ConsistencyClaim(**fields)


async def test_uq_claim_body_source_rejects_duplicate(seeded):
    """(chapter_id, body_rev, fingerprint, extractor_version) 在 source_kind='body' 内唯一。"""
    seeded.add(claim_row(source_kind="body", fingerprint="fp_body_1"))
    await seeded.commit()

    seeded.add(claim_row(source_kind="body", fingerprint="fp_body_1"))
    with pytest.raises(IntegrityError, match="uq_claim_body_source"):
        await seeded.commit()


async def test_uq_claim_outline_source_rejects_duplicate(seeded):
    """(chapter_id, outline_rev, fingerprint, extractor_version) 在 source_kind='outline' 内唯一。"""
    seeded.add(
        claim_row(
            source_kind="outline",
            body_rev=None,
            outline_rev=1,
            fingerprint="fp_outline_1",
        )
    )
    await seeded.commit()

    seeded.add(
        claim_row(
            source_kind="outline",
            body_rev=None,
            outline_rev=1,
            fingerprint="fp_outline_1",
        )
    )
    with pytest.raises(IntegrityError, match="uq_claim_outline_source"):
        await seeded.commit()


async def test_uq_claim_codex_source_rejects_duplicate(seeded):
    """(fingerprint, extractor_version) 在 source_kind='codex' 内唯一。"""
    seeded.add(
        claim_row(
            source_kind="codex",
            chapter_id=None,
            body_rev=None,
            fingerprint="fp_codex_1",
        )
    )
    await seeded.commit()

    seeded.add(
        claim_row(
            source_kind="codex",
            chapter_id=None,
            body_rev=None,
            fingerprint="fp_codex_1",
        )
    )
    with pytest.raises(IntegrityError, match="uq_claim_codex_source"):
        await seeded.commit()


async def test_uq_claim_resolution_source_rejects_duplicate(seeded):
    """(fingerprint, extractor_version) 在 source_kind='resolution' 内唯一。"""
    seeded.add(
        claim_row(
            source_kind="resolution",
            chapter_id=None,
            body_rev=None,
            fingerprint="fp_resolution_1",
        )
    )
    await seeded.commit()

    seeded.add(
        claim_row(
            source_kind="resolution",
            chapter_id=None,
            body_rev=None,
            fingerprint="fp_resolution_1",
        )
    )
    with pytest.raises(IntegrityError, match="uq_claim_resolution_source"):
        await seeded.commit()


async def test_two_timelines_of_the_same_statement_both_survive_the_unique_key(seeded):
    """同一句话在两条独立时间线上是两条事实，唯一键不得把它们吞掉。

    唯一键的列里没有 timeline_id —— 隔离靠的是 timeline_id 参与指纹（见
    services.claim_identity）。这条测试证明真实索引下两行确实能共存。
    """
    from services.claim_identity import claim_fingerprint

    semantics = {
        "subject_text": "李长风",
        "predicate": "alive",
        "object_type": "scalar",
        "object_value": "true",
        "polarity": "positive",
    }
    seeded.add(
        claim_row(
            timeline_id="line_a",
            source_anchor="3",
            fingerprint=claim_fingerprint(
                **semantics, timeline_id="line_a", source_anchor="3"
            ),
        )
    )
    seeded.add(
        claim_row(
            timeline_id="line_b",
            source_anchor="3",
            fingerprint=claim_fingerprint(
                **semantics, timeline_id="line_b", source_anchor="3"
            ),
        )
    )
    await seeded.commit()  # 不该抛 IntegrityError

    from sqlalchemy import select

    rows = (await seeded.execute(select(ConsistencyClaim))).scalars().all()
    assert {row.timeline_id for row in rows} == {"line_a", "line_b"}


async def test_two_paragraphs_of_the_same_fact_both_survive_the_unique_key(seeded):
    """同一条线上不同段落的同语义陈述是两次出现，各自成行。"""
    from services.claim_identity import claim_fingerprint

    semantics = {
        "subject_text": "李长风",
        "predicate": "alive",
        "object_type": "scalar",
        "object_value": "true",
        "polarity": "positive",
    }
    for anchor in ("3", "17"):
        seeded.add(
            claim_row(
                timeline_id="main",
                source_anchor=anchor,
                fingerprint=claim_fingerprint(
                    **semantics, timeline_id="main", source_anchor=anchor
                ),
            )
        )
    await seeded.commit()  # 不该抛 IntegrityError

    from sqlalchemy import select

    rows = (await seeded.execute(select(ConsistencyClaim))).scalars().all()
    assert {row.source_anchor for row in rows} == {"3", "17"}


async def test_the_same_paragraph_extracted_twice_still_collides(seeded):
    """同一段重复抽到的同一事实仍然是同一行 —— 重叠去重没有被 source_anchor 破坏。"""
    from services.claim_identity import claim_fingerprint

    fingerprint = claim_fingerprint(
        subject_text="李长风",
        predicate="alive",
        object_type="scalar",
        object_value="true",
        polarity="positive",
        timeline_id="main",
        source_anchor="7",
    )
    seeded.add(claim_row(timeline_id="main", source_anchor="7", fingerprint=fingerprint))
    await seeded.commit()

    seeded.add(claim_row(timeline_id="main", source_anchor="7", fingerprint=fingerprint))
    with pytest.raises(IntegrityError, match="uq_claim_body_source"):
        await seeded.commit()


async def test_same_fingerprint_across_different_source_kinds_is_allowed(seeded):
    """同一 fingerprint 可以在不同 source_kind 之间重复（部分索引隔离）。"""
    seeded.add(claim_row(source_kind="body", fingerprint="fp_shared"))
    seeded.add(
        claim_row(
            source_kind="outline",
            body_rev=None,
            outline_rev=1,
            fingerprint="fp_shared",
        )
    )
    seeded.add(
        claim_row(
            source_kind="codex",
            chapter_id=None,
            body_rev=None,
            fingerprint="fp_shared",
        )
    )
    seeded.add(
        claim_row(
            source_kind="resolution",
            chapter_id=None,
            body_rev=None,
            fingerprint="fp_shared",
        )
    )
    await seeded.commit()

    from sqlalchemy import select

    count = (await seeded.execute(select(ConsistencyClaim))).scalars().all()
    assert len(count) == 4
