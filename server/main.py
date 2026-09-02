"""
FastAPI 应用入口
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import admin, auth, chapters, codex, consistency, exports, generate, orgs, outlines, projects, usage
from config import settings


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
    return {"status": "ok", "service": "moshu-server"}


@app.get("/health")
async def health():
    """健康检查端点"""
    return {"status": "healthy"}


# 已实现路由
app.include_router(auth.router, prefix="/auth", tags=["认证"])
app.include_router(exports.router, prefix="/projects", tags=["导出与备份"])
app.include_router(projects.router, prefix="/projects", tags=["项目"])
app.include_router(chapters.router, prefix="/chapters", tags=["章节"])
app.include_router(outlines.router, prefix="/chapters", tags=["章纲"])
app.include_router(consistency.router, prefix="/consistency", tags=["一致性"])
app.include_router(codex.router, prefix="/codex", tags=["设定库"])
app.include_router(generate.router, prefix="/generate", tags=["生成"])
app.include_router(admin.router, prefix="/admin", tags=["管理"])
app.include_router(orgs.router, prefix="/orgs", tags=["组织"])
app.include_router(usage.router, prefix="/usage", tags=["用量"])

# TODO: 挂载后续路由
# from api import guard
# app.include_router(guard.router, prefix="/guard", tags=["守卫"])
