# 墨枢后端

AI 网文写作平台后端服务

## 技术栈

- **Python 3.12+**
- **FastAPI** - 异步 Web 框架
- **SQLAlchemy 2.0** (async) - ORM
- **PostgreSQL 16** + **pgvector** - 关系数据与向量检索
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
├── db/               # 数据模型（19张表）
│   ├── models_core.py    # 骨架 6张
│   ├── models_codex.py   # 设定库 4张
│   ├── models_guard.py   # 守卫 2张
│   ├── models_usage.py   # 风格/用量/占比 4张
│   └── models_org.py     # 组织 3张
├── memory/           # ★ 四层上下文装配器（核心）
│   ├── assembler.py  # 装配逻辑与预算裁剪
│   └── tokenizer.py  # Token 计数
├── guard/            # 一致性守卫（待实现）
├── gateway/          # 模型网关（待实现）
├── tasks/            # Celery 任务（待实现）
├── services/         # 业务逻辑层（待实现）
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
# 编辑 .env，填入数据库连接、Redis URL、模型网关密钥等
```

### 3. 启动数据库

使用 Docker Compose（TODO：补充 docker-compose.yml）：

```bash
docker-compose up -d postgres redis
```

### 4. 运行迁移

```bash
# 初始化 Alembic（首次）
alembic init alembic

# 生成迁移
alembic revision --autogenerate -m "Initial schema"

# 应用迁移
alembic upgrade head
```

### 5. 启动开发服务器

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

访问 API 文档：http://localhost:8000/docs

## 已实现

### 数据模型（19张表）
- ✅ 骨架 6张：users, projects, volumes, chapters, chapter_bodies, chapter_versions
- ✅ 设定库 4张：codex_entries, codex_aliases, codex_refs, codex_relations
- ✅ 守卫 2张：guard_issues, foreshadows
- ✅ 风格/用量/占比 4张：style_profiles, generation_runs, usage_logs, ratio_reports
- ✅ 组织权限 3张：orgs, org_members, chapter_assignments（MVP 建表不开功能）

### 核心模块
- ✅ **四层上下文装配器** (`memory/assembler.py`)
  - Layer 1: 常驻设定（按 id 排序保证字节稳定，命中 prompt cache）
  - Layer 2: 检索条目（实体抽取 → 精确命中 → 向量兜底，待完善）
  - Layer 3: 前情摘要（待完善）
  - Layer 4: 相邻原文（待完善）
  - 预算裁剪：25k 上限，layer4→3→2 顺序削减，layer1 永不削

- ✅ **乐观锁保存** (`api/chapters.py`)
  - 带 `base_rev` 的 PUT 请求
  - 冲突时返回 409 + 双方内容
  - 永不静默覆盖

### API 端点
- ✅ `GET /projects/:id` - 获取项目详情
- ✅ `GET /projects/:id/chapters` - 章节列表（不含正文）
- ✅ `GET /chapters/:id` - 章节详情（含正文 + rev）
- ✅ `PUT /chapters/:id/body` - 保存章节（带乐观锁）

## 待实现（优先级排序）

按《开发规划与技术选型》文档的排期：

### Week 1-2: 骨架跑通
- [ ] 补全 17 张表的 Alembic 迁移
- [ ] 认证接口：`POST /auth/code` + `/auth/verify`
- [ ] Docker Compose 完整配置
- [ ] 守卫 spike：构造 20 处矛盾测试稿，测规则前置覆盖率

### Week 3-4: 设定库 + 网关
- [ ] Codex CRUD 全套接口
- [ ] 改名跟随：更新 `codex_refs` 后返回受影响章节
- [ ] `codex_refs` 全量重建（扫描 `content_json` 的 `CodexRef` 节点）
- [ ] 模型网关（`gateway/`）：路由表 + 回退链 + token 记账
- [ ] 实体抽取器（`guard/extractor.py`）

### Week 5-6: 生成链路
- [ ] ★ `POST /generate/chapter` SSE 接口
- [ ] 开书向导四步：`POST /generate/wizard/:step`
- [ ] 行内 AI：`POST /generate/inline`
- [ ] Celery 摘要链路：`tasks/summarize.py`
- [ ] 完善 layer2/3/4 装配逻辑

### Week 7-8: 一致性守卫（MVP 成败点）
- [ ] ★ 冲突判定两阶段：规则前置 + LLM 判定
- [ ] `tasks/guard_scan.py` 全链路
- [ ] 守卫接口：`GET /projects/:id/guard` + `POST /guard/:id/resolve`
- [ ] 伏笔倒计时：`guard/foreshadow.py`
- [ ] 评测集接入 CI：跑到召回 ≥14/20、误报 ≤20%

### Week 9-10: 收口
- [ ] 导出三格式：`POST /export` + `GET /export/:taskId`
- [ ] 用量与订阅：`GET /usage`
- [ ] 性能压测：首屏 <2s / 切章 <200ms
- [ ] 错误上报与日志

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
TODO：补充 K8s manifests

## 参考文档

- [开发规划与技术选型](../开发规划与技术选型.dc.html) - 架构决策与排期
- [需求文档与技术选型](../需求文档与技术选型.dc.html) - 功能需求与验收标准
- [HANDOFF.md](../HANDOFF.md) - 前端交接文档

## 许可

内部项目，未开源
