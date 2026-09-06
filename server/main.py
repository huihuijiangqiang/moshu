"""
FastAPI 应用入口
"""

from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

try:  # Keep health/liveness endpoints importable in minimal test images.
    import redis.asyncio as redis
except ModuleNotFoundError:  # pragma: no cover - production dependencies include redis
    redis = None

from api import (
    admin,
    auth,
    chapters,
    codex,
    consistency,
    deconstruct,
    exports,
    generate,
    model_configs,
    orgs,
    outlines,
    projects,
    provenance,
    reviews,
    styles,
    text_replacement,
    usage,
)
from config import settings
from db.session import engine


def _migration_head() -> str:
    config = AlembicConfig(str(Path(__file__).with_name("alembic.ini")))
    return ScriptDirectory.from_config(config).get_current_head()


MIGRATION_HEAD = _migration_head()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    print("Moshu server starting...")
    yield
    # 关闭时
    print("Moshu server stopped")


app = FastAPI(
    title="墨枢 API",
    description="AI 网文写作平台后端",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """健康检查"""
    return {
        "status": "ok",
        "service": "moshu-server",
        "version": app.version,
        "revision": settings.build_revision,
    }


@app.get("/health")
async def health():
    """健康检查端点"""
    return {"status": "healthy", "revision": settings.build_revision}


@app.get("/health/ready")
async def readiness():
    """依赖就绪检查，用于容器探针和发布验收。

    ``/health`` 只表示 Web 进程存活；此端点会实际探测 PostgreSQL 与 Redis，
    并在任一依赖不可用时返回 503，避免流量被导向尚未完成迁移或无法排队任务的实例。
    错误只返回分类后的状态，不泄露连接串、凭据或异常堆栈。
    """
    checks: dict[str, str] = {}
    try:
        async with engine.connect() as connection:
            migration_result = await connection.execute(text("SELECT version_num FROM alembic_version"))
        checks["postgres"] = "ok"
        checks["migrations"] = (
            "ok" if migration_result.scalar_one_or_none() == MIGRATION_HEAD else "outdated"
        )
    except Exception:
        checks["postgres"] = "failed"
        checks["migrations"] = "unknown"

    if redis is None:
        checks["redis"] = "failed"
    else:
        client = None
        try:
            client = redis.from_url(settings.redis_url, decode_responses=True)
            await client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "failed"
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    # A failed close must not turn a useful 503 into a 500.
                    pass

    ready = all(value == "ok" for value in checks.values())
    payload = {
        "status": "ready" if ready else "not_ready",
        "revision": settings.build_revision,
        "checks": checks,
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)


# 已实现路由
app.include_router(auth.router, prefix="/auth", tags=["认证"])
app.include_router(exports.router, prefix="/projects", tags=["导出与备份"])
app.include_router(projects.router, prefix="/projects", tags=["项目"])
app.include_router(chapters.router, prefix="/chapters", tags=["章节"])
app.include_router(outlines.router, prefix="/chapters", tags=["章纲"])
app.include_router(consistency.router, prefix="/consistency", tags=["一致性"])
app.include_router(deconstruct.router, prefix="/analysis", tags=["拆书分析"])
app.include_router(codex.router, prefix="/codex", tags=["设定库"])
app.include_router(generate.router, prefix="/generate", tags=["生成"])
app.include_router(model_configs.router, prefix="/account", tags=["模型配置"])
app.include_router(admin.router, prefix="/admin", tags=["管理"])
app.include_router(orgs.router, prefix="/orgs", tags=["组织"])
app.include_router(usage.router, prefix="/usage", tags=["用量"])
app.include_router(styles.router, tags=["风格档"])
app.include_router(provenance.router, tags=["AI 来源"])
app.include_router(text_replacement.router, prefix="/projects", tags=["全书校订"])
app.include_router(reviews.router, prefix="/reviews", tags=["章节审稿"])
