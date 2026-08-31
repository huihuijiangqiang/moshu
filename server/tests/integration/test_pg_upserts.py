"""
PostgreSQL 专属 upsert（INSERT ... ON CONFLICT）的行为验证。

⚠️ 本文件在本次开发中**从未运行过**：需要真实 PostgreSQL。
`sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(...)` 在
SQLite 上无法编译，所以 upsert_claim 的冲突推断只能在这里验证。

背景（这里覆盖的是一个真实缺陷）：upsert_claim 原先把 index_elements 写成
(source_kind, chapter_id, body_rev, outline_rev, fingerprint, extractor_version)，
而这六列上没有任何唯一索引 —— PostgreSQL 会报 InvalidColumnReference，函数在
生产上一次都不可能成功。真正的唯一性是四个按 source_kind 分区的**部分**索引，
所以冲突推断必须同时给出 index_where。
"""
import pytest
from sqlalchemy import select

from db.models_codex import CodexAlias, CodexEntry
from db.models_consistency_extended import ConsistencyClaim
from db.models_core import Chapter, Project, User
from services.consistency import (
    EXTRACTOR_VERSION,
    PIPELINE_VERSION,
    compute_claim_fingerprint,
    get_or_create_run,
    upsert_active_summary,
    upsert_claim,
)
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
    await pg_session.flush()
    pg_session.add(
        CodexEntry(
            id="cx_lee",
            project_id="proj_pg",
            kind="character",
            name="李长风",
            description="",
            attrs={},
            resident=False,
            status="confirmed",
            ref_chapters=[],
            conflicts=[],
        )
    )
    await pg_session.flush()
    pg_session.add(CodexAlias(entry_id="cx_lee", alias="李长风"))
    await pg_session.commit()
    return pg_session


def body_claim_kwargs(**overrides):
    fields = {
        "project_id": "proj_pg",
        "subject_text": "李长风",
        "predicate": "alive",
        "object_type": "scalar",
        "object_value": "true",
        "source_kind": "body",
        "chapter_id": "ch_pg",
        "body_rev": 1,
        "extractor_version": EXTRACTOR_VERSION,
        "confidence": 0.9,
    }
    fields.update(overrides)
    return fields


async def test_upsert_claim_inserts_then_updates_in_place(seeded):
    """同一冲突键第二次调用必须更新同一行，而不是插入第二行或报错。"""
    first_id = await upsert_claim(seeded, **body_claim_kwargs())
    await seeded.commit()

    second_id = await upsert_claim(seeded, **body_claim_kwargs(confidence=0.55))
    await seeded.commit()

    assert first_id == second_id, "命中部分唯一索引，应原地更新"
    rows = (await seeded.execute(select(ConsistencyClaim))).scalars().all()
    assert len(rows) == 1
    assert float(rows[0].confidence) == pytest.approx(0.55)


async def test_upsert_claim_resolves_subject_entry_id(seeded):
    await upsert_claim(seeded, **body_claim_kwargs())
    await seeded.commit()

    row = (await seeded.execute(select(ConsistencyClaim))).scalar_one()
    assert row.subject_entry_id == "cx_lee"


async def test_upsert_claim_fingerprint_matches_the_helper(seeded):
    """写库的指纹与 compute_claim_fingerprint 一致，否则去重键对不上。"""
    await upsert_claim(seeded, **body_claim_kwargs())
    await seeded.commit()

    row = (await seeded.execute(select(ConsistencyClaim))).scalar_one()
    assert row.fingerprint == compute_claim_fingerprint(
        "李长风", "alive", "scalar", "true", "positive"
    )


async def test_upsert_claim_separates_source_kinds(seeded):
    """同一 fingerprint 在不同 source_kind 下互不冲突，各插一行。"""
    await upsert_claim(seeded, **body_claim_kwargs())
    await upsert_claim(
        seeded,
        **body_claim_kwargs(
            source_kind="outline", body_rev=None, outline_rev=1
        ),
    )
    await upsert_claim(
        seeded,
        **body_claim_kwargs(source_kind="codex", chapter_id=None, body_rev=None),
    )
    await seeded.commit()

    rows = (await seeded.execute(select(ConsistencyClaim))).scalars().all()
    assert {row.source_kind for row in rows} == {"body", "outline", "codex"}
    assert len(rows) == 3


async def test_upsert_claim_rejects_unknown_source_kind(seeded):
    with pytest.raises(ValueError, match="unknown source_kind"):
        await upsert_claim(seeded, **body_claim_kwargs(source_kind="nonsense"))


async def test_get_or_create_run_is_idempotent_on_postgres(seeded):
    """SAVEPOINT 兜住唯一键竞态：真实 PostgreSQL 上也只有一个 run。"""
    first = await get_or_create_run(
        seeded,
        project_id="proj_pg",
        chapter_id="ch_pg",
        body_rev=1,
        pipeline_version=PIPELINE_VERSION,
        trigger="body_save",
    )
    await seeded.commit()

    second = await get_or_create_run(
        seeded,
        project_id="proj_pg",
        chapter_id="ch_pg",
        body_rev=1,
        pipeline_version=PIPELINE_VERSION,
        trigger="manual_scan",
    )
    await seeded.commit()

    assert first == second


async def test_upsert_active_summary_is_idempotent_on_postgres(seeded):
    from db.models_consistency_extended import DocumentSummary

    await upsert_active_summary(
        seeded,
        owner_type="chapter",
        owner_id="ch_pg",
        source_rev=1,
        summary_version="1.0.0",
        content="第一次",
        model_id="model-a",
        token_count=10,
    )
    await seeded.commit()

    await upsert_active_summary(
        seeded,
        owner_type="chapter",
        owner_id="ch_pg",
        source_rev=1,
        summary_version="1.0.0",
        content="第二次",
        model_id="model-b",
        token_count=20,
    )
    await seeded.commit()

    rows = (await seeded.execute(select(DocumentSummary))).scalars().all()
    assert len(rows) == 1
    assert rows[0].content == "第二次"
