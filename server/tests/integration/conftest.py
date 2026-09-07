"""
真实 PostgreSQL 集成测试的夹具。

未设置 TEST_POSTGRES_URL 时全部 skip，绝不会伪装成通过；设置该变量后会连接
真实 PostgreSQL + pgvector，并在独立 schema 中执行。

这里覆盖的是 SQLite 单元测试**结构上无法验证**的部分：

1. 部分唯一索引（postgresql_where）—— SQLite 不支持，conftest 建表时被剔除；
2. pgvector 的 `<=>` 余弦距离与 HNSW 索引 —— SQLite 上 embedding 是 TEXT；
3. `INSERT ... ON CONFLICT` upsert —— SQLAlchemy 的 postgresql insert 方言专属；
4. GIN 索引；
5. CHECK 约束在并发/边界数据下的真实拒绝行为。

跑法：
    export TEST_POSTGRES_URL='postgresql+asyncpg://user:pass@localhost:5432/moshu_test'
    pytest tests/integration -m postgres
数据库需要预装 pgvector 扩展（CREATE EXTENSION vector）。每个 test 在独立
schema 上建表并在结束后 DROP SCHEMA CASCADE，不会污染既有数据。
"""
import os
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import db as _db_package  # noqa: F401  注册全部模型
from db import Base

POSTGRES_URL_ENV = "TEST_POSTGRES_URL"

#: 没有真实 PostgreSQL 时的统一 skip 原因（保持文案一致，便于在报告里识别）。
SKIP_REASON = (
    f"需要真实 PostgreSQL + pgvector；设置 {POSTGRES_URL_ENV} 后才会运行。"
    "未设置变量时用例会跳过。"
)

requires_postgres = pytest.mark.skipif(
    not os.environ.get(POSTGRES_URL_ENV), reason=SKIP_REASON
)


def postgres_url() -> str:
    url = os.environ.get(POSTGRES_URL_ENV)
    if not url:
        pytest.skip(SKIP_REASON)
    return url


@pytest_asyncio.fixture
async def pg_engine():
    """在独立 schema 上按 ORM 元数据建表；结束后整体删除。"""
    engine = create_async_engine(postgres_url(), echo=False, poolclass=None)
    schema = f"test_{uuid.uuid4().hex[:12]}"

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.execute(text(f'SET search_path TO "{schema}", public'))
        # Public may already contain the production tables. checkfirst=True would see
        # those visible names and skip creating the isolated schema's tables.
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, checkfirst=False))

    try:
        yield engine, schema
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_engine) -> AsyncGenerator[AsyncSession, None]:
    engine, schema = pg_engine
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await session.execute(text(f'SET search_path TO "{schema}", public'))
        try:
            yield session
        finally:
            await session.rollback()
