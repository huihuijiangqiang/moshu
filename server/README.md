# 墨枢后端

AI 网文写作平台后端服务

## 技术栈

- **Python 3.12+**
- **FastAPI** - 异步 Web 框架
- **SQLAlchemy 2.0** (async) - ORM
- **PostgreSQL 16** + **pgvector 0.7+** - 关系数据与 2048 维 halfvec 向量检索
- **Redis 7** - 缓存与任务队列
- **Celery** - 异步任务（守卫、摘要、风格抽取）
- **uv** - 依赖管理

## 项目结构

```
server/
├── api/              # FastAPI 路由（按功能分组）
│   ├── projects.py   # 项目管理
│   ├── chapters.py   # 章节读写（含乐观锁）
│   └── ...
├── db/               # 数据模型（42张表）
│   ├── models_core.py    # 骨架 7张
│   ├── models_codex.py   # 设定库 5张
│   ├── models_guard.py   # 守卫 2张
│   ├── models_usage.py   # 风格/用量/占比 4张
│   └── models_org.py     # 组织 3张
├── memory/           # ★ 四层上下文装配器（核心）
│   ├── assembler.py  # 装配逻辑与预算裁剪
│   └── tokenizer.py  # Token 计数
├── providers/        # Chat/Embedding 模型网关客户端
├── tasks/            # Celery 异步任务与 outbox dispatcher
├── services/         # 业务逻辑层
├── tests/            # 测试
├── config.py         # 配置管理
└── main.py           # FastAPI 入口
```

## 快速开始

### 1. 安装依赖

使用 `uv`（推荐）：

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 创建虚拟环境并安装依赖
cd server
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -e .
```

或使用 pip：

```bash
pip install -e .
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，分别填入生成模型网关和 embedding 网关的 URL/key
```

`EMBEDDING_GATEWAY_URL` 可以是 base URL 或完整 `/embeddings` 端点；URL 与 key
必须同时配置。当前数据库 schema 固定为 `HALFVEC(2048)`，更换不同维度的模型前
必须先新增数据库迁移，不能只改 `EMBEDDING_DIMENSIONS`。

### 3. 启动数据库

在仓库根目录只启动开发依赖：

```bash
docker compose up -d postgres redis
```

容器使用 `pgvector/pgvector:pg16` 和 `redis:7-alpine`，默认端口分别为 5432、6379，
并带健康检查与持久卷。

也可以一次启动完整产品环境：

```bash
docker compose up -d --build
```

该命令先执行 Alembic migration，再启动 API、Celery worker/dispatcher/beat 和前端
Nginx。前端监听 `5180`，API 监听 `8000`；本地密钥只从被 Git 忽略的 `server/.env`
注入容器。

Windows 上如果 Docker Desktop 尚未运行，先启动 Docker Desktop，再执行上述命令。
若 `docker version` 只能显示 Client 或报 `dockerDesktopLinuxEngine` 不存在，说明
daemon 尚未就绪；这不是应用迁移失败，不能用 SQLite 结果替代容器验收。

### 4. 运行迁移

```bash
# 应用迁移
alembic upgrade head
```

### 5. 启动开发服务器

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

访问 API 文档：http://localhost:8000/docs

容器探针：`GET /health` 仅检查 Web 进程存活；`GET /health/ready` 会实际探测
PostgreSQL、Redis 与 Alembic head，全部通过时返回 200，否则返回 503 和逐项状态。
响应包含 `BUILD_REVISION`，用于判断正在运行的实例是否与待验收提交一致。

## 已实现

### 数据模型（42张表）
- ✅ 核心 7张：users, projects, volumes, chapters, chapter_bodies, chapter_versions, project_notes
- ✅ 设定库 5张：codex_entries, codex_aliases, codex_refs, codex_relations, codex_state_changes
- ✅ Embedding 运维 1张：codex_embedding_jobs
- ✅ 守卫与一致性 13张：guard_issues, foreshadows 及 runs、claims、摘要、时间线、证据、处置表
- ✅ 组织权限 3张：orgs, org_members, chapter_assignments
- ✅ 审稿协作 2张：chapter_review_rounds, review_comments
- ✅ 认证与管理 3张：auth_sessions, system_settings, admin_audit_logs
- ✅ 风格/生成/用量 6张：style_profiles, generation_runs, generation_drafts, usage_logs, ratio_reports, user_model_configs
- ✅ 跨章编辑与人工计划 2张：text_replacement_runs, timeline_entries

### 核心模块
- ✅ **四层上下文装配器** (`memory/assembler.py`)
  - Layer 1: 常驻设定（按 id 排序保证字节稳定，命中 prompt cache）
  - Layer 2: 章纲名称/别名精确命中 → pgvector 向量兜底，排除常驻重复项
  - Layer 3: 更早卷摘要 + 当前卷最近 20 条有效章摘要（允许中间章节尚未生成摘要）
  - Layer 4: 最近两章已有正文，超预算时优先保留章末
  - 预算裁剪：25k 上限，layer4→3→2 顺序削减，layer1 永不削

- ✅ **真实生成链路** (`api/generate.py`, `services/generation.py`)
  - 版本化运行时写作 Skill：基础 → 题材 → 任务 → 场景 → 作者风格
  - `gpt-5.6-sol` 等具体模型由环境变量配置，代码不保存网关凭据
  - 章节/行内生成均使用带鉴权的 SSE，可中断并返回 Skill 与四层 token 报告
  - 成功运行写入 `generation_runs`，上游错误通过结构化 SSE 返回且不改正文

- ✅ **乐观锁保存** (`api/chapters.py`)
  - 带 `base_rev` 的 PUT 请求
  - 冲突时返回 409 + 双方内容
  - 永不静默覆盖

- ✅ **持续章纲后端** (`api/outlines.py`, `services/outlines.py`)
  - 独立章纲版本与历史快照
  - 已有正文时强制选择 `plan_only` / `mark_body_for_revision`
  - 修改章纲和处置标记都不写正文
  - transactional outbox 与两阶段 API 幂等基础设施
- ✅ **移动端只读与灵感速记** (`api/projects.py`)
  - 小屏正文强制只读，隐藏生成、恢复和全书替换等写操作
  - 速记按用户隔离，可选绑定当前章节并持久化
  - 备份只包含发起备份者自己的速记，恢复时重映射章节 ID

### API 端点
- ✅ `GET /projects/:id` - 获取项目详情
- ✅ `GET /projects/:id/chapters` - 章节列表（不含正文）
- ✅ `GET /chapters/:id` - 章节详情（含正文 + rev）
- ✅ `PUT /chapters/:id/body` - 保存章节（带乐观锁）
- ✅ `PUT /chapters/:id/outline` - 保存章纲（带独立乐观锁）
- ✅ `GET /chapters/:id/outline/revisions` - 章纲版本历史
- ✅ `POST /chapters/:id/body-revision/resolve` - 作者确认正文调整状态
- ✅ `GET /generate/context/:chapterId` - 预览四层上下文与自动选择的 Skill
- ✅ `POST /generate/chapter` - 按章纲流式生成候选整章
- ✅ `POST /generate/inline` - 续写/扩写/润色/语气/作者风格行内生成
- ✅ `GET/POST /projects/:id/notes`、`DELETE /projects/:id/notes/:noteId` - 私有灵感速记

## 当前边界

核心 MVP 已完成并由 `docs/IMPLEMENTATION_STATUS.md` 记录证据：认证与 refresh
session、管理员设置与审计、项目/工作室 RBAC、设定库 CRUD 与 embedding 回填、
持续章纲、版本化正文保存、四层上下文、SSE 生成、导出备份、用量结算、Guard
扫描与 LLM 仲裁、平台后台模型用量台账、移动端只读与私有速记均已接通。后端单元/功能
测试为 1193 passed，另有 36 个真实 PostgreSQL/pgvector 集成测试；前端测试为 147 passed。

仍需在生产数据上继续验证的事项：

- 正文到结构化 claim 与 LLM 仲裁的真实盲评质量（七类确定性规则已实现并通过结构化门禁）；
- 10/30/100 万字规模下的真实 PostgreSQL 检索、模型网络延迟和成本曲线；
- 模糊时间表达的语义规范化与跨章节锚点变更后的级联重算；
- Kubernetes manifests、集中式错误上报与日志平台属于工作室阶段，不是当前 MVP 部署门。

可重复的确定性规则/上下文 CPU 基准见 `scripts/benchmark_consistency.py`。

## 开发规范

### 代码风格
- 使用 `ruff` 格式化与 lint
- 类型注解：所有公开函数必须有类型标注
- Docstring：复杂逻辑必须写注释

### 数据库约束
- **chapter_bodies 独立存储**：列表查询永不误带正文
- **codex_refs 全量重建**：每次保存扫描 `content_json`，增量更新会漏删除
- **layer1 序列化按 id 排序**：保证字节稳定，命中 prompt cache

### API 设计
- 响应模型：使用 Pydantic，与前端 TS 类型对齐
- 错误处理：返回结构化错误，不返回堆栈
- SSE 注意：Nginx 必须 `proxy_buffering off`

## 测试

```bash
# 运行全部测试
pytest

# 覆盖率
pytest --cov=. --cov-report=html

# 守卫召回率评测
pytest tests/test_guard_recall.py
```

## 部署

### MVP 阶段（Docker Compose）
```bash
docker-compose up -d
```

### 工作室阶段（K8s）
当前不在 MVP 范围内。工作室阶段再补充 manifests、密钥管理、集中式日志和多副本
部署策略；MVP 使用根目录 `docker-compose.yml`，并以 `/health/ready` 作为就绪门禁。

## 参考文档

- [开发规划与技术选型](../开发规划与技术选型.dc.html) - 架构决策与排期
- [需求文档与技术选型](../需求文档与技术选型.dc.html) - 功能需求与验收标准
- [HANDOFF.md](../HANDOFF.md) - 前端交接文档

## 许可

内部项目，未开源
