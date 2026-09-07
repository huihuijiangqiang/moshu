# 墨枢

墨枢是面向长篇网文创作的 AI 写作平台。项目以一键成章为入口，重点解决长程设定记忆、一致性检查、伏笔追踪和作者风格保真。

![墨枢长篇小说创作工作台](docs/assets/moshu-overview.png)

当前仓库包含 Vue 3 写作前端、FastAPI API、PostgreSQL/pgvector、Redis 与 Celery 异步任务，以及必要的架构与实现文档。

## 仓库结构

```text
.
├── app/       Vue 3 + TypeScript + Tiptap 前端
├── server/    FastAPI + SQLAlchemy 后端骨架
├── docs/      架构与产品技术文档
├── _ds/       设计系统资源
└── LICENSE    项目使用许可
```

## 前端启动

```bash
cd app
cp .env.example .env
npm install
npm run dev
```

默认启用 mock API，可在没有后端的情况下浏览全部产品界面。测试和类型检查命令：

```bash
npm test
npm run typecheck
```

## 后端启动

后端要求 Python 3.12+，详细准备步骤见 `server/README.md`。

```bash
cd server
uv venv
uv pip install -e ".[dev]"
cp .env.example .env
uvicorn main:app --reload --port 8000
```

## 完整环境启动

准备好仅保存在本机的 `server/.env` 后，可在仓库根目录一次启动迁移、API、前端与异步任务：

```bash
docker compose up -d --build
```

- 前端：http://localhost:5180
- API 文档：http://localhost:8000/docs
- 就绪探针：http://localhost:8000/health/ready

`migration` 会在 API 和 worker 启动前执行 `alembic upgrade head`。就绪探针同时检查
PostgreSQL、Redis 与数据库迁移版本；任一项不满足就返回 503。

### 数据目录（Windows）

Compose 默认使用项目下的 `.docker-data` 目录作为开发兜底。部署前建议把数据放到非系统盘，
在项目根目录创建仅本机使用的 `.env`（不要提交），例如：

```dotenv
MOSHU_POSTGRES_DATA_DIR=D:/moshu-data/postgres
MOSHU_REDIS_DATA_DIR=D:/moshu-data/redis
```

这两个目录会以 bind mount 方式挂载，不使用 Docker Desktop 默认 named volume 位置。目录需提前创建，
并在 Docker Desktop 中共享所在磁盘。

## 核心约束

- 一章一文档，章节列表接口不返回正文。
- 单次生成上下文不超过 25k token。
- AI 流式输出先进入 `aiDraft`，采纳后才成为正文。
- 正文中的设定引用保存条目 ID，不保存显示名称。
- MVP 阶段不引入 Yjs；多人协作留到工作室阶段。

## 页面信息架构

页面分为全局层与项目层，项目层路由必须携带 `projectId`，禁止从作品库进入后继续读取上一部作品的数据。

| 层级 | 页面 | 职责 |
| --- | --- | --- |
| 全局 | `/` | 浏览、筛选和进入作品，不承载单部作品数据 |
| 全局 | `/projects/new` | 创建作品，确认骨架后进入该作品写作台 |
| 全局 | `/usage` | 账户套餐、积分与模型用量 |
| 项目 | `/projects/:projectId/outline` | 卷纲、章纲、时间线与节奏规划 |
| 项目 | `/projects/:projectId/codex` | 当前作品的人物、地点、势力、物品和伏笔设定 |
| 项目 | `/projects/:projectId/write` | 章节选择、正文编辑、AI 生成和引用设定 |
| 项目 | `/projects/:projectId/guard` | 当前作品的一致性、伏笔和待确认设定处置 |
| 项目 | `/projects/:projectId/style` | 为当前作品选择或管理作者风格档 |
| 项目 | `/projects/:projectId/ai-ratio` | 按本章或全书复核文字来源与疑似 AI 句式 |
| 项目 | `/projects/:projectId/export` | 导出当前作品正文、设定、大纲和自查报告 |

主要创作流程是：创建或选择作品 → 梳理大纲与设定 → 写作 → 一致性与 AI 痕迹复核 → 导出。写作、大纲和设定允许反复往返；守卫问题必须能跳回对应正文或时间线。账户用量不属于任何作品。

当前实现状态与后端验收记录见 `server/docs/IMPLEMENTATION_STATUS.md`；架构约束见 `docs/architecture/`。

## 许可

本项目采用保留所有权利（All Rights Reserved）许可，详见 [`LICENSE`](LICENSE)。除版权所有者书面授权外，不得复制、修改、再发布、销售或将本项目用于生产部署。第三方依赖仍受其各自许可证约束。
