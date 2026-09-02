"""
Alembic 迁移与 ORM 模型的结构对等性测试。

为什么要有这组测试：生产库由 alembic 建表，而所有查询按 ORM 模型写。两边一旦
漂移，代码在本地 SQLite（按 ORM 建表）全绿，上线后却在 PostgreSQL 上报
UndefinedColumn。原先这个文件只对迁移文件做字符串/概念断言（甚至断言了一个
不存在的表名 organizations），任何真实漂移都发现不了。

做法：tests/migration_harness.py 把 alembic 的 op 代理换成记录器，真正执行一次
upgrade()，把 create_table / create_index 重放成 sa.Table / sa.Index，然后逐表
比对列名、列类型、nullable、主键、外键（含 ondelete）、CHECK / UNIQUE 约束名与
索引名。比对的是结构对象，不是文本，所以重构迁移文件不会误报。
"""
import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from db import Base
from tests.migration_harness import (
    build_migration_metadata,
    load_migration_module,
    migration_files,
)


@pytest.fixture(scope="module")
def migration_metadata():
    metadata, _ = build_migration_metadata()
    return metadata


@pytest.fixture(scope="module")
def recorder():
    _, rec = build_migration_metadata()
    return rec


@pytest.fixture(scope="module")
def shared_tables(migration_metadata):
    return sorted(set(Base.metadata.tables) & set(migration_metadata.tables))


def _foreign_keys(table):
    """(本地列, 目标表, 目标列, ondelete) 的有序集合。"""
    return sorted(
        (fk.parent.name, fk.column.table.name, fk.column.name, fk.ondelete)
        for fk in table.foreign_keys
    )


def _check_names(table):
    return sorted(
        c.name for c in table.constraints if isinstance(c, CheckConstraint) and c.name
    )


def _unique_names(table):
    return sorted(
        c.name for c in table.constraints if isinstance(c, UniqueConstraint) and c.name
    )


def _index_names(table):
    return sorted(index.name for index in table.indexes)


# --- 迁移脚本自身的健全性 -----------------------------------------------------


def test_migration_files_exist():
    assert migration_files(), "no alembic migration files found"


def test_migration_chain_is_linear_and_replayable(migration_metadata):
    """能重放说明 revision 链无环、无分叉、无缺失父节点。"""
    assert len(migration_metadata.tables) > 0


def test_pgvector_extension_is_created(recorder):
    """codex_entries.embedding 是 vector 类型，缺这句建表就会失败。"""
    assert any("CREATE EXTENSION" in sql and "vector" in sql for sql in recorder.executed_sql)


def test_pg_trgm_extension_is_created(recorder):
    """The fuzzy alias GIN index requires pg_trgm's gin_trgm_ops."""
    assert any("CREATE EXTENSION" in sql and "pg_trgm" in sql for sql in recorder.executed_sql)


def test_downgrade_drops_every_created_table(recorder):
    """downgrade 必须能回滚干净，否则失败的发布无法退回。"""
    metadata, rec = build_migration_metadata()
    for path in reversed(migration_files()):
        module = load_migration_module(path)
        original = module.op
        module.op = rec
        try:
            module.downgrade()
        finally:
            module.op = original

    assert set(rec.dropped_tables) == set(metadata.tables), (
        "downgrade 没有删掉全部建过的表: "
        f"{sorted(set(metadata.tables) - set(rec.dropped_tables))}"
    )


# --- 表级对等 -----------------------------------------------------------------


def test_no_table_missing_from_migration(migration_metadata):
    missing = sorted(set(Base.metadata.tables) - set(migration_metadata.tables))
    assert missing == [], f"ORM 有但迁移没建的表: {missing}"


def test_no_table_only_in_migration(migration_metadata):
    extra = sorted(set(migration_metadata.tables) - set(Base.metadata.tables))
    assert extra == [], f"迁移建了但 ORM 没有的表: {extra}"


def test_expected_core_tables_are_present(migration_metadata):
    """按真实表名断言（历史版本写成了不存在的 organizations）。"""
    for name in (
        "users",
        "orgs",
        "org_members",
        "chapter_assignments",
        "projects",
        "chapters",
        "chapter_bodies",
        "chapter_versions",
        "codex_entries",
        "codex_aliases",
        "codex_embedding_jobs",
        "consistency_runs",
        "consistency_claims",
        "document_summaries",
        "guard_issues",
        "guard_issue_evidence",
        "guard_resolutions",
        "entity_state_intervals",
        "story_events",
        "outbox_events",
        "idempotency_records",
    ):
        assert name in migration_metadata.tables, f"迁移缺少表 {name}"
        assert name in Base.metadata.tables, f"ORM 缺少表 {name}"


# --- 列级对等（逐表参数化，失败信息直接定位到表） -----------------------------


def pytest_generate_tests(metafunc):
    if "table_name" in metafunc.fixturenames:
        metadata, _ = build_migration_metadata()
        names = sorted(set(Base.metadata.tables) & set(metadata.tables))
        metafunc.parametrize("table_name", names)


def test_columns_match(table_name, migration_metadata):
    orm = Base.metadata.tables[table_name]
    mig = migration_metadata.tables[table_name]
    orm_cols = set(orm.columns.keys())
    mig_cols = set(mig.columns.keys())

    assert orm_cols - mig_cols == set(), (
        f"{table_name}: 迁移缺少列 {sorted(orm_cols - mig_cols)} —— "
        "生产查询会报 UndefinedColumn"
    )
    assert mig_cols - orm_cols == set(), (
        f"{table_name}: 迁移多出列 {sorted(mig_cols - orm_cols)} —— ORM 里没有对应字段"
    )


def test_column_types_match(table_name, migration_metadata):
    orm = Base.metadata.tables[table_name]
    mig = migration_metadata.tables[table_name]
    mismatches = []
    for name in sorted(set(orm.columns.keys()) & set(mig.columns.keys())):
        orm_type = str(orm.columns[name].type)
        mig_type = str(mig.columns[name].type)
        if orm_type != mig_type:
            mismatches.append(f"{name}: orm={orm_type} migration={mig_type}")
    assert mismatches == [], f"{table_name} 列类型不一致: {mismatches}"


def test_column_nullability_matches(table_name, migration_metadata):
    orm = Base.metadata.tables[table_name]
    mig = migration_metadata.tables[table_name]
    mismatches = []
    for name in sorted(set(orm.columns.keys()) & set(mig.columns.keys())):
        if orm.columns[name].nullable != mig.columns[name].nullable:
            mismatches.append(
                f"{name}: orm nullable={orm.columns[name].nullable} "
                f"migration nullable={mig.columns[name].nullable}"
            )
    assert mismatches == [], f"{table_name} nullable 不一致: {mismatches}"


def test_primary_keys_match(table_name, migration_metadata):
    orm = [c.name for c in Base.metadata.tables[table_name].primary_key.columns]
    mig = [c.name for c in migration_metadata.tables[table_name].primary_key.columns]
    assert orm == mig, f"{table_name} 主键不一致: orm={orm} migration={mig}"


def test_foreign_keys_match(table_name, migration_metadata):
    """外键与 ondelete 都要一致：漏掉 CASCADE 会让删除项目时留下孤儿行。"""
    orm = _foreign_keys(Base.metadata.tables[table_name])
    mig = _foreign_keys(migration_metadata.tables[table_name])
    assert orm == mig, f"{table_name} 外键不一致:\n  orm={orm}\n  migration={mig}"


def test_check_constraints_match(table_name, migration_metadata):
    """CHECK 是数据完整性的最后一道防线，缺一条就会写进非法状态。"""
    orm = _check_names(Base.metadata.tables[table_name])
    mig = _check_names(migration_metadata.tables[table_name])
    assert orm == mig, f"{table_name} CHECK 约束不一致: orm={orm} migration={mig}"


def test_unique_constraints_match(table_name, migration_metadata):
    orm = _unique_names(Base.metadata.tables[table_name])
    mig = _unique_names(migration_metadata.tables[table_name])
    assert orm == mig, f"{table_name} UNIQUE 约束不一致: orm={orm} migration={mig}"


def test_indexes_match(table_name, migration_metadata):
    orm = _index_names(Base.metadata.tables[table_name])
    mig = _index_names(migration_metadata.tables[table_name])
    assert orm == mig, f"{table_name} 索引不一致: orm={orm} migration={mig}"


# --- 一致性管道依赖的关键结构 -------------------------------------------------


def test_postgres_only_indexes_are_in_the_migration(migration_metadata):
    """这些索引在 SQLite 上建不出来，本地测试覆盖不到，只能靠迁移保证。"""
    from tests.conftest import POSTGRES_ONLY_INDEXES

    all_indexes = {
        index.name for table in migration_metadata.tables.values() for index in table.indexes
    }
    missing = sorted(POSTGRES_ONLY_INDEXES - all_indexes)
    assert missing == [], f"迁移缺少 PostgreSQL 专属索引: {missing}"


def test_claim_partial_unique_indexes_carry_their_where_clause(migration_metadata):
    """claim 的四个唯一键靠 partial index 按 source_kind 分区；
    丢了 where 会让 outline / codex claim 撞上 body 的唯一约束。"""
    table = migration_metadata.tables["consistency_claims"]
    by_name = {index.name: index for index in table.indexes}
    expected = {
        "uq_claim_body_source": "body",
        "uq_claim_outline_source": "outline",
        "uq_claim_codex_source": "codex",
        "uq_claim_resolution_source": "resolution",
    }
    for name, source_kind in expected.items():
        assert name in by_name, f"迁移缺少 {name}"
        index = by_name[name]
        assert index.unique, f"{name} 必须是唯一索引"
        where = index.dialect_options["postgresql"].get("where")
        assert where is not None, f"{name} 丢了 postgresql_where"
        assert source_kind in str(where), f"{name} 的 where 没有限定 source_kind={source_kind}"


def test_embedding_index_uses_hnsw(migration_metadata):
    """L3 向量召回依赖 HNSW；退化成顺序扫描会让检索超时。"""
    table = migration_metadata.tables["codex_entries"]
    index = next(i for i in table.indexes if i.name == "ix_codex_entries_embedding")
    assert index.dialect_options["postgresql"].get("using") == "hnsw"
    assert index.dialect_options["postgresql"].get("ops") == {
        "embedding": "halfvec_cosine_ops"
    }


def test_every_hnsw_migration_step_declares_an_operator_class(recorder):
    """PostgreSQL cannot build vector HNSW indexes without an explicit opclass."""
    hnsw_indexes = [
        index
        for index in recorder.created_indexes
        if index.dialect_options["postgresql"].get("using") == "hnsw"
    ]
    assert hnsw_indexes
    for index in hnsw_indexes:
        assert index.dialect_options["postgresql"].get("ops") in (
            {"embedding": "vector_cosine_ops"},
            {"embedding": "halfvec_cosine_ops"},
        )


def test_embedding_column_is_2048_dimension_halfvec(migration_metadata):
    column_type = migration_metadata.tables["codex_entries"].c.embedding.type

    assert str(column_type) == "HALFVEC(2048)"


def test_embedding_dimension_migration_invalidates_old_vectors(recorder):
    assert any(
        "UPDATE codex_entries SET embedding = NULL, embedding_text_hash = NULL" in sql
        for sql in recorder.executed_sql
    )


def test_alias_index_uses_gin(migration_metadata):
    table = migration_metadata.tables["codex_aliases"]
    index = next(i for i in table.indexes if i.name == "ix_codex_aliases_alias_gin")
    assert index.dialect_options["postgresql"].get("using") == "gin"
    assert index.dialect_options["postgresql"].get("ops") == {
        "alias": "gin_trgm_ops"
    }


def test_run_uniqueness_prevents_duplicate_pipelines(migration_metadata):
    """(chapter_id, body_rev, pipeline_version) 唯一，否则同一版本会跑出多个 run。"""
    table = migration_metadata.tables["consistency_runs"]
    keys = {
        tuple(c.name for c in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("chapter_id", "body_rev", "pipeline_version") in keys


def test_run_phase_columns_exist_with_their_checks(migration_metadata):
    """三个阶段列及其取值约束必须在迁移里，否则生产查询报 UndefinedColumn。

    完成判定完全依赖这三列：缺一列，`succeed_phase` 的 UPDATE 在 PostgreSQL 上直接
    失败，整条管道停在 scanning。
    """
    from db.models_consistency_extended import RUN_PHASE_COLUMNS, RUN_PHASE_STATES

    table = migration_metadata.tables["consistency_runs"]
    checks = {
        c.name: str(c.sqltext)
        for c in table.constraints
        if isinstance(c, CheckConstraint) and c.name
    }
    for column in RUN_PHASE_COLUMNS.values():
        assert column in table.columns, f"迁移缺少阶段列 {column}"
        assert table.columns[column].nullable is False, f"{column} 必须 NOT NULL"
        name = f"ck_consistency_run_{column}"
        assert name in checks, f"迁移缺少 {name}"
        for state in RUN_PHASE_STATES:
            assert f"'{state}'" in checks[name], f"{name} 不接受阶段状态 {state}"


def test_completed_run_requires_every_phase_to_have_succeeded(migration_metadata):
    """status='completed' 必须蕴含三阶段 succeeded —— 数据库兜底那条不变量。

    没有这条约束，任何绕过 succeed_phase 的写入（修复脚本、未来新任务）都能造出
    「显示完成、摘要其实没跑」的 run。
    """
    from db.models_consistency_extended import RUN_PHASE_COLUMNS

    table = migration_metadata.tables["consistency_runs"]
    check = next(
        c for c in table.constraints
        if isinstance(c, CheckConstraint) and c.name == "ck_consistency_run_completed_phases"
    )
    sql = str(check.sqltext)
    assert "completed" in sql
    for column in RUN_PHASE_COLUMNS.values():
        assert f"{column} = 'succeeded'" in sql, f"约束没有要求 {column} 成功"


def test_document_summary_uniqueness_key(migration_metadata):
    table = migration_metadata.tables["document_summaries"]
    keys = {
        tuple(c.name for c in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("owner_type", "owner_id", "source_rev", "summary_version") in keys


def test_guard_resolution_action_check_lists_every_offered_action(migration_metadata):
    """扫描器给 GuardIssue.actions 填的取值必须都能被 CHECK 接受。"""
    from services.rule_scanner import DEFAULT_ACTIONS, ISSUE_ACTIONS

    table = migration_metadata.tables["guard_resolutions"]
    check = next(
        c for c in table.constraints
        if isinstance(c, CheckConstraint) and c.name == "ck_resolution_action"
    )
    sql = str(check.sqltext)

    offered = set(DEFAULT_ACTIONS)
    for actions in ISSUE_ACTIONS.values():
        offered.update(actions)
    for action in sorted(offered):
        assert f"'{action}'" in sql, f"ck_resolution_action 不接受扫描器提供的动作 {action}"
