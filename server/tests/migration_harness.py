"""从 Alembic 迁移脚本重建 MetaData，用于和 ORM 模型做结构比对。

做法：把 alembic 的 `op` 代理换成一个记录器，真正执行一次 upgrade()，
把 create_table / create_index 的参数原样喂给 sa.Table / sa.Index。
这样比对的是真实的 Column 类型、nullable、主外键、约束与索引，
而不是对迁移文件做字符串匹配 —— 字符串匹配通不过任何重构。
"""
import importlib.util
from pathlib import Path
from typing import Any

import sqlalchemy as sa

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


class _OpRecorder:
    """替代 alembic.op：把 DDL 调用重放成 SQLAlchemy 对象。"""

    def __init__(self, metadata: sa.MetaData):
        self.metadata = metadata
        self.executed_sql: list[str] = []
        self.created_indexes: list[sa.Index] = []
        self.dropped_tables: list[str] = []

    # --- alembic op API 子集 ---

    def create_table(self, table_name: str, *columns: Any, **kw: Any) -> sa.Table:
        kw.pop("schema", None)
        return sa.Table(table_name, self.metadata, *columns, **kw)

    def create_index(
        self,
        index_name: str,
        table_name: str,
        columns: list,
        unique: bool = False,
        **kw: Any,
    ) -> sa.Index:
        kw.pop("schema", None)
        table = self.metadata.tables[table_name]
        # 迁移里索引列可以是列名字符串，也可以是 sa.text(...) 表达式
        resolved = [table.c[col] if isinstance(col, str) else col for col in columns]
        index = sa.Index(index_name, *resolved, unique=unique, _table=table, **kw)
        self.created_indexes.append(index)
        return index

    def add_column(self, table_name: str, column: sa.Column, **kw: Any) -> None:
        kw.pop("schema", None)
        self.metadata.tables[table_name].append_column(column)

    def create_foreign_key(
        self,
        constraint_name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        remote_cols: list[str],
        **kw: Any,
    ) -> None:
        source = self.metadata.tables[source_table]
        remote = [f"{referent_table}.{column}" for column in remote_cols]
        source.append_constraint(
            sa.ForeignKeyConstraint(local_cols, remote, name=constraint_name, **kw)
        )

    def create_check_constraint(
        self,
        constraint_name: str,
        table_name: str,
        condition: str,
        **kw: Any,
    ) -> None:
        self.metadata.tables[table_name].append_constraint(
            sa.CheckConstraint(condition, name=constraint_name, **kw)
        )

    def drop_constraint(
        self,
        constraint_name: str,
        table_name: str,
        **kw: Any,
    ) -> None:
        kw.pop("type_", None)
        table = self.metadata.tables[table_name]
        constraint = next(
            (item for item in table.constraints if item.name == constraint_name),
            None,
        )
        if constraint is not None:
            table.constraints.discard(constraint)

    def drop_table(self, table_name: str, **kw: Any) -> None:
        self.dropped_tables.append(table_name)

    def drop_column(self, table_name: str, column_name: str, **kw: Any) -> None:
        kw.pop("schema", None)
        table = self.metadata.tables[table_name]
        table._columns.remove(table.c[column_name])

    def drop_index(self, index_name: str, **kw: Any) -> None:
        table_name = kw.get("table_name")
        tables = (
            [self.metadata.tables[table_name]]
            if table_name
            else self.metadata.tables.values()
        )
        for table in tables:
            index = next((item for item in table.indexes if item.name == index_name), None)
            if index is not None:
                table.indexes.discard(index)
                return

    def alter_column(self, table_name: str, column_name: str, **kw: Any) -> None:
        type_ = kw.get("type_")
        if type_ is not None:
            self.metadata.tables[table_name].columns[column_name].type = type_

    def execute(self, sql: Any, **kw: Any) -> None:
        self.executed_sql.append(str(sql))

    def f(self, name: str) -> str:
        """alembic 的命名约定包装器；比对时按字面名处理。"""
        return name


def migration_files() -> list[Path]:
    return sorted(path for path in MIGRATIONS_DIR.glob("*.py") if not path.name.startswith("__"))


def load_migration_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"_migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_migration_metadata() -> tuple[sa.MetaData, _OpRecorder]:
    """按 down_revision 顺序重放所有迁移的 upgrade()。"""
    modules = [load_migration_module(path) for path in migration_files()]

    by_revision = {module.revision: module for module in modules}
    predecessors = {module.revision: module.down_revision for module in modules}

    roots = [rev for rev, parent in predecessors.items() if parent is None]
    if len(roots) != 1:
        raise AssertionError(f"expected exactly one root migration, got {roots}")

    ordered = []
    current = roots[0]
    children = {parent: rev for rev, parent in predecessors.items() if parent is not None}
    while current is not None:
        ordered.append(by_revision[current])
        current = children.get(current)

    if len(ordered) != len(modules):
        raise AssertionError("migration chain is not linear; parity check needs a single chain")

    metadata = sa.MetaData()
    recorder = _OpRecorder(metadata)
    for module in ordered:
        original_op = module.op
        module.op = recorder
        try:
            module.upgrade()
        finally:
            module.op = original_op
    return metadata, recorder
