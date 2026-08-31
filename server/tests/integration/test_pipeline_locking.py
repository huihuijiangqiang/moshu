"""
一致性管道在真实 PostgreSQL 上的集成测试。

⚠️ 本文件在本次开发中**从未运行过**：需要真实 PostgreSQL。
tests/test_tasks_consistency.py 覆盖的是「锁 + 版本比较」这段逻辑本身，但两件
事在 SQLite 上结构性地无法验证：

1. `SELECT ... FOR UPDATE` —— SQLite 方言直接把它丢掉，不会真正互斥；
2. 部分唯一索引（postgresql_where）—— tests/conftest.py 建表时把它们剔除了，
   所以同 revision 重放到底会不会撞索引，只有这里能证明。

跑法见 tests/integration/conftest.py 的模块文档。
"""
import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models_consistency_extended import ConsistencyClaim
from db.models_core import Chapter, ChapterBody, Project, User
from tasks import consistency as tasks
from tests.integration.conftest import requires_postgres

pytestmark = [pytest.mark.postgres, requires_postgres]


@pytest.fixture
async def seeded(pg_session):
    """user -> project -> chapter -> ChapterBody 头行（rev 1）。"""
    pg_session.add(
        User(
            id="user_pg",
            name="pg user",
            email="pg@example.test",
            plan="free",
            quota_remaining=1000,
            quota_total=1000,
        )
    )
    await pg_session.flush()
    pg_session.add(
        Project(
            id="proj_pg",
            owner_id="user_pg",
            title="pg project",
            status="ongoing",
            target_words_daily=3000,
        )
    )
    await pg_session.flush()
    pg_session.add(
        Chapter(
            id="ch_pg",
            project_id="proj_pg",
            volume_id=None,
            title="第一章",
            idx=1024,
            words=0,
            outline=[],
        )
    )
    await pg_session.flush()
    pg_session.add(
        ChapterBody(
            chapter_id="ch_pg",
            content_html="<p>正文</p>",
            content_json={"type": "doc"},
            rev=1,
        )
    )
    await pg_session.commit()
    return pg_session


def claim_row(**overrides) -> ConsistencyClaim:
    fields = {
        "project_id": "proj_pg",
        "chapter_id": "ch_pg",
        "body_rev": 1,
        "subject_text": "李长风",
        "predicate": "alive",
        "object_type": "scalar",
        "object_value": "true",
        "polarity": "positive",
        "certainty": "explicit",
        "source_kind": "body",
        "extractor_version": "1.0.0",
        "fingerprint": "fp_pg",
        "status": "accepted",
    }
    fields.update(overrides)
    return ConsistencyClaim(**fields)


# --- 部分唯一索引真的会拦住重复行 ---------------------------------------------


async def test_partial_unique_index_rejects_a_duplicate_body_claim(seeded):
    """uq_claim_body_source 在同 (chapter, body_rev, fingerprint, version) 上拒绝第二行。"""
    seeded.add(claim_row())
    await seeded.commit()

    seeded.add(claim_row())
    with pytest.raises(IntegrityError):
        await seeded.commit()


async def test_marking_superseded_does_not_free_the_unique_key(seeded):
    """索引不含 status —— 这正是同 revision 重放必须 DELETE 而不是标记的原因。

    单元测试只能断言「重放后没有重复键」；这里证明如果改回「标记 superseded
    再插入」，数据库会直接拒绝。
    """
    first = claim_row()
    seeded.add(first)
    await seeded.commit()

    first.status = "superseded"
    await seeded.flush()

    seeded.add(claim_row())
    with pytest.raises(IntegrityError):
        await seeded.commit()


async def test_deleting_the_old_row_does_free_the_unique_key(seeded):
    """抽取步骤采用的 DELETE 路径在真实索引下可行。"""
    seeded.add(claim_row())
    await seeded.commit()

    await seeded.execute(
        ConsistencyClaim.__table__.delete().where(
            ConsistencyClaim.chapter_id == "ch_pg",
            ConsistencyClaim.source_kind == "body",
            ConsistencyClaim.body_rev == 1,
            ConsistencyClaim.extractor_version == "1.0.0",
        )
    )
    seeded.add(claim_row())
    await seeded.commit()

    rows = list((await seeded.execute(select(ConsistencyClaim))).scalars().all())
    assert len(rows) == 1


async def test_partial_index_is_scoped_by_source_kind(seeded):
    """body 与 outline 的同 fingerprint 互不冲突 —— where 子句按来源分区。

    两个索引的列几乎一样（只差 body_rev / outline_rev），靠 postgresql_where
    按 source_kind 分区。丢了 where，大纲 claim 就会撞上正文的唯一键。
    """
    seeded.add(claim_row())
    seeded.add(claim_row(source_kind="outline", body_rev=None, outline_rev=1))
    await seeded.commit()

    rows = list((await seeded.execute(select(ConsistencyClaim))).scalars().all())
    assert len(rows) == 2


# --- FOR UPDATE 真的互斥 -------------------------------------------------------


async def test_for_update_serializes_two_workers_on_the_head_row(pg_engine, seeded):
    """两个 worker 同时锁 ChapterBody 头行时，后到的必须等前一个提交。

    这是 lock_body_head 在生产上的实际保护：SQLite 会静默丢掉 FOR UPDATE，
    所以「旧 worker 不会在新版本落库后再提交」只有在这里才是真的。
    """
    engine, schema = pg_engine
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    order: list[str] = []

    async def first_worker():
        async with maker() as session:
            await session.execute(text(f'SET search_path TO "{schema}", public'))
            await tasks.lock_body_head(session, "ch_pg")
            order.append("first_locked")
            await asyncio.sleep(0.3)
            order.append("first_committing")
            await session.commit()

    async def second_worker():
        async with maker() as session:
            await session.execute(text(f'SET search_path TO "{schema}", public'))
            await asyncio.sleep(0.1)  # 确保第一个先拿到锁
            order.append("second_waiting")
            await tasks.lock_body_head(session, "ch_pg")
            order.append("second_locked")
            await session.commit()

    await asyncio.gather(first_worker(), second_worker())

    assert order.index("second_locked") > order.index("first_committing"), (
        f"第二个 worker 没有等锁: {order}"
    )


async def test_head_row_lock_makes_the_revision_check_authoritative(pg_engine, seeded):
    """持锁期间别的会话改不动 rev —— 提交前的版本检查因此不会看到脏值。"""
    engine, schema = pg_engine
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    events: list[str] = []

    async def holder():
        async with maker() as session:
            await session.execute(text(f'SET search_path TO "{schema}", public'))
            await tasks.lock_body_head(session, "ch_pg")
            events.append("locked")
            await asyncio.sleep(0.3)
            # 持锁期间读到的仍是 rev 1，检查结论可信
            assert await tasks.head_revision(session, "ch_pg") == 1
            events.append("checked")
            await session.commit()

    async def writer():
        async with maker() as session:
            await session.execute(text(f'SET search_path TO "{schema}", public'))
            await asyncio.sleep(0.1)
            events.append("writing")
            body = (
                await session.execute(
                    select(ChapterBody)
                    .where(ChapterBody.chapter_id == "ch_pg")
                    .with_for_update()
                )
            ).scalar_one()
            body.rev = 2
            await session.commit()
            events.append("written")

    await asyncio.gather(holder(), writer())

    assert events.index("written") > events.index("checked"), (
        f"写入没有被锁挡住: {events}"
    )
