"""
共享测试夹具。

方言策略（重要）：
生产库是 PostgreSQL，模型里用了 JSONB / pgvector.Vector / 部分唯一索引 /
GIN / HNSW —— 这些在 SQLite 上无法编译。本地没有 PostgreSQL，所以单元测试
跑在 SQLite 上，做法是：

1. 为 JSONB / Vector 注册 SQLite 编译规则（JSON / TEXT）；
2. 把 Base.metadata 复制一份再删掉 PostgreSQL 专属索引，
   不去改动全局 Base.metadata（否则会污染 Alembic 与生产代码）。

被排除的 PostgreSQL 专属对象（部分唯一索引、pgvector 距离查询、
ON CONFLICT upsert）无法在 SQLite 上验证，只能靠真实 PostgreSQL 集成测试覆盖，
相关用例集中在 tests/integration/ 并默认 skip —— 它们没有跑过。
"""
import os

# config.Settings 有若干无默认值的必填字段；在导入任何应用模块之前先兜底，
# 保证没有 .env 的环境（CI）也能收集测试。
_ENV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "JWT_SECRET_KEY": "test-secret-key-for-unit-tests-only",
    "MODEL_GATEWAY_CHEAP_URL": "http://gateway.invalid/v1/chat/completions",
    "MODEL_GATEWAY_CHEAP_KEY": "test-cheap-key",
    "MODEL_GATEWAY_MAIN_URL": "http://gateway.invalid/v1/chat/completions",
    "MODEL_GATEWAY_MAIN_KEY": "test-main-key",
    "MODEL_GATEWAY_PREMIUM_URL": "http://gateway.invalid/v1/chat/completions",
    "MODEL_GATEWAY_PREMIUM_KEY": "test-premium-key",
    "S3_ENDPOINT": "http://s3.invalid",
    "S3_ACCESS_KEY": "test-access",
    "S3_SECRET_KEY": "test-secret",
}
for _key, _value in _ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)

from collections.abc import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from pgvector.sqlalchemy import Vector  # noqa: E402
from sqlalchemy import MetaData, event  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import db as _db_package  # noqa: E402,F401  确保全部模型都注册到 Base.metadata
from db import Base  # noqa: E402


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    """SQLite 没有 JSONB，用 JSON 承载。"""
    return "JSON"


@compiles(Vector, "sqlite")
def _compile_vector_sqlite(type_, compiler, **kw):
    """SQLite 没有 pgvector；存成 TEXT，仅用于建表，不做向量检索。"""
    return "TEXT"


#: 无法在 SQLite 上创建、因此只能由 PostgreSQL 集成测试覆盖的索引。
POSTGRES_ONLY_INDEXES = frozenset(
    {
        "uq_claim_body_source",
        "uq_claim_outline_source",
        "uq_claim_codex_source",
        "uq_claim_resolution_source",
        "ix_codex_aliases_alias_gin",
        "ix_codex_entries_embedding",
    }
)


def build_sqlite_metadata() -> MetaData:
    """复制 Base.metadata 并剔除 PostgreSQL 专属索引（不改动原 metadata）。"""
    sqlite_metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(sqlite_metadata)

    for table in sqlite_metadata.tables.values():
        for index in list(table.indexes):
            options = index.dialect_options.get("postgresql", {})
            if options.get("where") is not None or options.get("using") is not None:
                table.indexes.discard(index)
    return sqlite_metadata


@pytest_asyncio.fixture
async def async_db_engine():
    """内存 SQLite 引擎；StaticPool 让同一连接在整个测试内复用。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(build_sqlite_metadata().create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def async_db_session(async_db_engine) -> AsyncGenerator[AsyncSession, None]:
    """函数级数据库会话。"""
    session_maker = async_sessionmaker(async_db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest_asyncio.fixture
async def app_client(async_db_session) -> AsyncGenerator[object, None]:
    """真实 ASGI 客户端，get_db 覆盖为测试会话（不发真实网络请求）。"""
    from httpx import ASGITransport, AsyncClient

    from db.session import get_db
    from main import app

    async def _override_get_db():
        yield async_db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


def make_access_token(user_id: str) -> str:
    """用生产同一套 secret/algorithm 签发真实 JWT。"""
    from jose import jwt

    from config import settings

    return jwt.encode({"sub": user_id}, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@pytest.fixture
def auth_headers():
    """为指定用户生成 Bearer 头。"""

    def _headers(user_id: str, **extra):
        headers = {"Authorization": f"Bearer {make_access_token(user_id)}"}
        headers.update(extra)
        return headers

    return _headers


@pytest.fixture
def make_user():
    """按真实 User 字段构造用户（name/plan/quota_*，没有 username/password）。"""
    from db.models_core import User

    def _make(user_id: str, **overrides):
        fields = {
            "id": user_id,
            "name": overrides.pop("name", user_id),
            "email": overrides.pop("email", f"{user_id}@example.test"),
            "plan": overrides.pop("plan", "free"),
            "quota_remaining": overrides.pop("quota_remaining", 1000),
            "quota_total": overrides.pop("quota_total", 1000),
        }
        fields.update(overrides)
        return User(**fields)

    return _make


@pytest.fixture
def make_project():
    """按真实 Project 字段构造项目（title，不是 name）。"""
    from db.models_core import Project

    def _make(project_id: str, owner_id: str, **overrides):
        fields = {
            "id": project_id,
            "owner_id": owner_id,
            "title": overrides.pop("title", f"Project {project_id}"),
            "status": overrides.pop("status", "ongoing"),
            "target_words_daily": overrides.pop("target_words_daily", 3000),
        }
        fields.update(overrides)
        return Project(**fields)

    return _make


@pytest.fixture
def make_chapter():
    """按真实 Chapter 字段构造章节（无 status 字段；outline 是 JSONB 列表）。"""
    from db.models_core import Chapter

    def _make(chapter_id: str, project_id: str, idx: int = 1024, **overrides):
        fields = {
            "id": chapter_id,
            "project_id": project_id,
            "volume_id": overrides.pop("volume_id", None),
            "title": overrides.pop("title", f"Chapter {chapter_id}"),
            "idx": idx,
            "words": overrides.pop("words", 0),
            "outline": overrides.pop("outline", []),
        }
        fields.update(overrides)
        return Chapter(**fields)

    return _make


@pytest.fixture
def make_claim():
    """按真实 ConsistencyClaim 字段构造 claim。

    必填：project_id、subject_text、predicate、object_type、source_kind、
    extractor_version、fingerprint。source_kind='body' 时受
    ck_claim_source_versioning 约束，必须同时给 chapter_id 与 body_rev。
    """
    from db.models_consistency_extended import ConsistencyClaim

    def _make(**overrides):
        fields = {
            "project_id": "proj_test",
            "subject_text": "角色A",
            "predicate": "alive",
            "object_type": "scalar",
            "object_value": "true",
            "polarity": "positive",
            "certainty": "explicit",
            "source_kind": "body",
            "extractor_version": "1.0.0",
            "status": "accepted",
        }
        fields.update(overrides)
        if fields["source_kind"] == "body":
            fields.setdefault("chapter_id", "ch_test")
            fields.setdefault("body_rev", 1)
        fields.setdefault(
            "fingerprint",
            f"fp_{fields['subject_text']}_{fields['predicate']}_{fields.get('object_value')}",
        )
        return ConsistencyClaim(**fields)

    return _make


@pytest.fixture
def make_run():
    """按真实 ConsistencyRun 字段构造 run（pipeline_version 与 trigger 都是必填）。"""
    from db.models_consistency_extended import ConsistencyRun

    def _make(**overrides):
        fields = {
            "project_id": "proj_test",
            "chapter_id": "ch_test",
            "body_rev": 1,
            "pipeline_version": "1.0.0",
            "status": "pending",
            "trigger": "body_save",
        }
        fields.update(overrides)
        return ConsistencyRun(**fields)

    return _make
