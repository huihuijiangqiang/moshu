"""
claim upsert 的冲突推断与真实索引的对应关系。

为什么这组测试必须存在：upsert_claim 用的是 PostgreSQL 的
INSERT ... ON CONFLICT，SQLite 不编译这条语句，所以它在任何 SQLite 单元测试里
都跑不到。原先 index_elements 写的是六列
(source_kind, chapter_id, body_rev, outline_rev, fingerprint, extractor_version)，
这六列上没有任何唯一索引 —— PostgreSQL 会报
    InvalidColumnReference: there is no unique or exclusion constraint
    matching the ON CONFLICT specification
也就是说这个函数上线后一次都不可能成功，而测试全绿。

这里不连数据库，而是把语句按 postgresql 方言编译出来，再拿编译结果去和
ConsistencyClaim 上真实存在的部分唯一索引做结构比对。跑得到、且能抓住同一类
缺陷；真实的拒绝行为由 tests/integration/（未跑过）覆盖。
"""
import pytest
from sqlalchemy import Index, text
from sqlalchemy.dialects import postgresql

from db.models_consistency_extended import ConsistencyClaim
from services.consistency import _CLAIM_CONFLICT_TARGETS


def partial_unique_indexes() -> dict[str, Index]:
    """ConsistencyClaim 上真实存在的部分唯一索引，按名字索引。"""
    return {
        index.name: index
        for index in ConsistencyClaim.__table__.indexes
        if index.unique and index.dialect_options["postgresql"].get("where") is not None
    }


def test_every_source_kind_has_a_conflict_target():
    """CHECK 约束允许的四种 source_kind 都要有对应的冲突目标。"""
    assert set(_CLAIM_CONFLICT_TARGETS) == {"body", "outline", "codex", "resolution"}


def test_conflict_targets_match_real_partial_indexes():
    """每个冲突目标的列集合都必须等于某个真实部分唯一索引的列集合。

    这正是原缺陷的所在：六列的冲突推断匹配不到任何索引。
    """
    actual = {
        tuple(column.name for column in index.expressions)
        for index in partial_unique_indexes().values()
    }
    assert actual, "ConsistencyClaim 上没有部分唯一索引，模型被改坏了"

    for source_kind, (columns, _where) in _CLAIM_CONFLICT_TARGETS.items():
        assert tuple(columns) in actual, (
            f"source_kind={source_kind} 的冲突推断 {columns} 匹配不到任何部分唯一索引；"
            f"现有索引列集合: {sorted(actual)}"
        )


@pytest.mark.parametrize("source_kind", sorted(_CLAIM_CONFLICT_TARGETS))
def test_conflict_where_matches_the_index_where(source_kind):
    """index_where 必须和索引自身的 postgresql_where 一致，否则推断不到那个部分索引。"""
    _columns, where = _CLAIM_CONFLICT_TARGETS[source_kind]
    index_wheres = {
        str(index.dialect_options["postgresql"]["where"])
        for index in partial_unique_indexes().values()
    }
    assert where in index_wheres, (
        f"source_kind={source_kind} 的 index_where={where!r} 不等于任何索引的 where；"
        f"现有: {sorted(index_wheres)}"
    )


@pytest.mark.parametrize("source_kind", sorted(_CLAIM_CONFLICT_TARGETS))
def test_statement_compiles_with_both_columns_and_predicate(source_kind):
    """编译出的 ON CONFLICT 必须同时带列和 WHERE —— 只给列推断不到部分索引。"""
    columns, where = _CLAIM_CONFLICT_TARGETS[source_kind]
    stmt = (
        postgresql.insert(ConsistencyClaim)
        .values(
            project_id="p",
            subject_text="s",
            predicate="alive",
            object_type="scalar",
            source_kind=source_kind,
            extractor_version="1.0.0",
            fingerprint="fp",
        )
        .on_conflict_do_update(
            index_elements=columns,
            index_where=text(where),
            set_={"status": "candidate"},
        )
    )

    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "ON CONFLICT" in sql
    for column in columns:
        assert column in sql, f"编译结果缺少冲突列 {column}"
    assert "WHERE" in sql.split("ON CONFLICT", 1)[1], "ON CONFLICT 之后必须有 WHERE 谓词"


def test_the_old_six_column_target_matches_no_index():
    """回归锚点：旧的六列写法确实匹配不到任何索引。

    如果哪天真加了这样一个索引，这条测试会失败，提醒把上面的约定一起改掉。
    """
    stale = ("source_kind", "chapter_id", "body_rev", "outline_rev", "fingerprint", "extractor_version")
    all_index_columns = {
        tuple(column.name for column in index.expressions)
        for index in ConsistencyClaim.__table__.indexes
    }
    assert stale not in all_index_columns
