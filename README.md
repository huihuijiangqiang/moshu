# 墨枢 · Moshu

> 面向长篇网文作者的 AI 创作工作台：把章纲、设定、正文和一致性证据放在同一个作品空间里。

墨枢的重点不是只生成一段文字，而是让长篇创作可以持续推进：按章节生成和采纳正文，用可追溯的设定、时间线与证据维持前后文一致，在作者确认前标出冲突和待处理线索。

![墨枢长篇小说创作工作台](docs/assets/moshu-overview.png)

## 核心能力

- **写作台**：章节正文、章纲、AI 草稿、风格档和上下文预算在同一工作区协同。
- **结构规划**：在开书和持续创作阶段维护目标平台、核心卖点、长期承诺与章节场景卡片，不把定位和章纲锁死在一次性向导里。
- **作品记忆**：人物、地点、势力、物品、伏笔和时间线按作品隔离，支持持续修改。
- **一致性守卫**：展示冲突两端的证据，支持回到正文处理，不让模型静默改写作者设定。
- **自然化审查**：按章或选区提示模板化衔接、句式节奏和修饰堆叠风险，可结合作者风格档和人物声口生成受事实锁约束的候选，修改必须由作者逐条确认并保留来源记录。
- **漫剧分镜**：在独立改编版本中管理集、场景、镜头和人物视觉档案，镜头可维护景别、运镜、动作、对白、旁白与画面提示词。
- **长文本基础设施**：PostgreSQL/pgvector 负责持久化与检索，Redis/Celery 承担异步分析和生成任务；正文按确定性分块覆盖全文，摘要、设定和向量检索分层装配。

当前仓库包含 Vue 3 写作前端、FastAPI API、PostgreSQL/pgvector、Redis 与 Celery 异步任务，以及架构、计费和实现文档。后端当前 migration head 为 `033_payment_provider_workflow`。

### 长篇一致性保障

- 章节可保存作者确认的结构化时间范围，生成时同时注入本章和上一章时间锚点，减少日期回退。
- 作品定位保存目标平台、核心卖点、主角困境、首个兑现点和长线承诺的不可变版本，章节场景卡片把 POV、地点、目标、阻力、转折和钩子绑定到具体章节；两者由同一装配链进入预览和真实生成。
- AI 输出先进入候选草稿；上游 SSE 未收到完整结束标记时，草稿会标记为 `failed`，返回 `STREAM_INTERRUPTED`，不会进入正文。
- Guard 优先使用确定性规则，对抽取器提供的结构化账目执行现金/库存、计件工资和资源数量校验；LLM 只负责抽取和补充软性问题，不负责最终算术结论。
- 设定库使用 PostgreSQL/pgvector 检索，四层上下文预算上限为 25k token，正文、摘要、常驻设定和相关条目分层进入生成提示。
- 生成候选附带非阻断的规划覆盖报告，区分“规划已进入提示词”和“候选中找到字面证据”；语义兑现仍由作者判断。
- `server/scripts/evaluate_long_novel.py` 可读取不入库的私有长篇 checkpoint，统计完成度、摘要/事实/向量覆盖、表层声口漂移和 token；检索与冲突召回只在提供人工 gold 时计算。

## 系统截图

以下截图来自本地审核环境，展示作品库、写作台、设定库、一致性守卫、拆书分析和用量页面的实际界面。

| 作品库 | 写作台 |
| --- | --- |
| ![作品库](docs/assets/screenshots/shelf.png) | ![写作台](docs/assets/screenshots/writer.png) |

| 设定库 | 一致性守卫 |
| --- | --- |
| ![设定库](docs/assets/screenshots/codex.png) | ![一致性守卫](docs/assets/screenshots/guard.png) |

| 拆书分析 | 用量与计费 |
| --- | --- |
| ![拆书分析](docs/assets/screenshots/deconstruct.png) | ![用量与计费](docs/assets/screenshots/usage.png) |

## 仓库结构

```text
.
├── app/       Vue 3 + TypeScript + Tiptap 前端
├── server/    FastAPI + SQLAlchemy 后端与一致性任务
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

### 配置自己的大模型调用地址

墨枢使用 OpenAI 兼容协议调用文本模型。请根据使用场景选择一种配置方式，API
key 只保存在本机环境变量或账户加密配置中，不要写入 Git、README、Dockerfile 或
前端代码。

#### 部署者配置平台默认网关

复制 `server/.env.example` 为 `server/.env`，填写三档网关（不需要三档时可以让它们
指向同一个服务）：

```dotenv
MODEL_GATEWAY_MAIN_URL=https://api.example.com/v1/chat/completions
MODEL_GATEWAY_MAIN_KEY=your-api-key
MODEL_GATEWAY_CHEAP_URL=https://api.example.com/v1/chat/completions
MODEL_GATEWAY_CHEAP_KEY=your-api-key
MODEL_GATEWAY_PREMIUM_URL=https://api.example.com/v1/chat/completions
MODEL_GATEWAY_PREMIUM_KEY=your-api-key

# 生成和一致性分析使用的模型名
GENERATION_GATEWAY_TIER=main
GENERATION_MODEL=your-text-model
CONSISTENCY_GATEWAY_TIER=main
CONSISTENCY_SUMMARY_MODEL=your-text-model
CONSISTENCY_EXTRACTION_MODEL=your-text-model
```

网关地址应直接对应 `POST /chat/completions`，支持 `stream: true` 的 SSE 返回，
并返回 OpenAI 风格的 `choices[].delta.content`；服务端会发送 Bearer 鉴权。若网关
只提供 `/v1` 根地址，请在这里补上 `/chat/completions`。重启 API、worker 和
dispatcher 后配置生效：

```bash
docker compose up -d --build api worker dispatcher beat
curl http://localhost:8000/health/ready
```

#### 用户配置自己的服务（BYOK）

登录后打开全局菜单 **我的模型服务**（路由 `/model-settings`），填写：

- 服务名称：便于识别的名称；
- 基础地址：例如 `https://api.example.com/v1`，系统会自动拼接
  `/chat/completions`；也可以直接填写完整聊天端点；
- 模型名：供应商实际接受的模型 ID；
- API key：只在保存或更新时提交，界面只显示脱敏提示。

出于 SSRF 防护，用户自定义地址必须是可解析的公网 `https` 地址，不能包含账号密码、
query、fragment、`localhost`、内网 IP 或 Docker 服务名。点击“测试连接”前，供应商
还需要提供可访问的 `GET /models`（返回 401/403 会显示为鉴权失败）。保存后的 key
使用 `CREDENTIAL_ENCRYPTION_KEY` 加密存储，修改该密钥会使已保存的用户 key 无法解密；
生产环境应设置一个稳定、独立于 JWT 的随机值：

```dotenv
CREDENTIAL_ENCRYPTION_KEY=replace-with-a-long-random-secret
```

启用用户配置后，该用户的章节生成和行内生成优先走自己的服务；未启用或未配置时回退
到平台默认网关。用户自定义服务的用量会记录到该用户的模型配置台账，平台网关的用量
仍按平台套餐和积分规则结算。

#### 单独配置 Embedding 服务

设定库的向量检索可以使用独立的 OpenAI 兼容 embedding 端点。填写完整端点或 base
URL 均可，后者会自动补 `/embeddings`：

```dotenv
EMBEDDING_GATEWAY_URL=https://embedding.example.com/v1
EMBEDDING_GATEWAY_KEY=your-embedding-key
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_DIMENSIONS=2048
```

Embedding 响应必须包含与输入数量相同的 `data[].embedding` 数组，且维度为 2048；
当前 PostgreSQL schema 使用 `HALFVEC(2048)`，更换维度前必须先做数据库迁移。

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

首次启动或更新代码后建议显式执行一次迁移，再重建依赖 migration 的服务：

```bash
docker compose run --rm migration
docker compose up -d --build api worker dispatcher beat frontend
```

### 数据目录（Windows）

Compose 默认使用项目下的 `.docker-data` 目录作为开发兜底。部署前建议把数据放到非系统盘，
在项目根目录创建仅本机使用的 `.env`（不要提交），例如：

```dotenv
MOSHU_POSTGRES_DATA_DIR=<postgres-data-directory>
MOSHU_REDIS_DATA_DIR=<redis-data-directory>
```

这两个目录会以 bind mount 方式挂载，不使用 Docker Desktop 默认 named volume 位置。请按部署主机的操作系统填写实际目录，
目录需提前创建，并在 Docker Desktop 中共享对应位置。

### 微信与支付宝

服务端支持微信支付 API v3 和支付宝 OpenAPI 的预下单、查单、关单、退款、通知验签和幂等入账。用量页在渠道就绪后显示购买按钮、支付二维码、订单同步与退款入口；管理员负责发布积分商品。

真实收款必须在仅部署机可见的 `server/.env` 中配置商户身份、私钥、公钥或平台证书，以及公网 HTTPS 回调地址。缺少或无法读取这些配置时，接口会返回 `credentials_required` 或 `invalid_configuration`，不会接受未验签回调。示例字段见 `server/.env.example`，任何真实密钥和证书都不得提交 Git。

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
| 项目 | `/projects/:projectId/outline` | 卷纲、章纲、场景卡片、作品定位与节奏规划 |
| 项目 | `/projects/:projectId/timeline` | 可编辑故事事实、时间锚点和 Guard 冲突落点 |
| 项目 | `/projects/:projectId/codex` | 当前作品的人物、地点、势力、物品和伏笔设定 |
| 项目 | `/projects/:projectId/storyboard` | 漫剧改编版本、人物视觉档案、场景与静态分镜稿（暂不生成视频） |
| 项目 | `/projects/:projectId/write` | 章节选择、正文编辑、AI 生成和引用设定 |
| 项目 | `/projects/:projectId/guard` | 当前作品的一致性、伏笔和待确认设定处置 |
| 项目 | `/projects/:projectId/style` | 为当前作品选择或管理作者风格档 |
| 项目 | `/projects/:projectId/ai-ratio` | 按本章或全书复核文字来源，并逐条处理自然化审查建议 |
| 项目 | `/projects/:projectId/export` | 导出当前作品正文、设定、大纲和自查报告 |

主要创作流程是：创建或选择作品 → 明确作品定位 → 梳理大纲、场景与设定 → 写作 → 一致性与自然化审查 → 导出。定位、写作、大纲和设定允许反复往返；守卫问题必须能跳回对应正文或时间线。账户用量不属于任何作品。

当前实现状态与后端验收记录见 `server/docs/IMPLEMENTATION_STATUS.md`；架构约束见 `docs/architecture/`。

漫剧分镜在 mock 模式和真实 API 模式下使用同一套交互。真实 API 将改编版本、集、场景、
镜头和视觉档案持久化到 PostgreSQL，并按作品权限限制查看、编辑分镜和维护视觉档案。
当前阶段不包含实际视频、配音、字幕时间轴或合成任务；图片生成仍属于待接入能力。

最近一次后端回归记录：`1406 passed, 37 skipped`；本轮前端回归为 `160 passed`。后端被跳过的
测试需要显式配置真实 PostgreSQL/pgvector 集成环境；测试正文、模型 key、`.env` 和 Docker
数据卷均不提交 Git。

## 许可

本项目采用保留所有权利（All Rights Reserved）许可，详见 [`LICENSE`](LICENSE)。除版权所有者书面授权外，不得复制、修改、再发布、销售或将本项目用于生产部署。第三方依赖仍受其各自许可证约束。
