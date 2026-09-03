# 墨枢一致性后端实现状态报告

## 执行摘要

本文档记录墨枢一致性后端在 `worktree-moshu-consistency-backend-v2` 分支的真实实现状态。
所有声明基于实际代码与测试结果，不夸大、不省略已知缺口。

**关键事实**：
- ✅ 34 张表完整 Alembic baseline，增量迁移已到 `012_content_lifecycle`
- ✅ 1001 个单元/功能测试通过（SQLite in-memory，mock embedding/LLM）
- ✅ 前端 73 个测试、TypeScript 类型检查和生产构建通过
- ✅ 36 个集成测试已在本机真实 PostgreSQL + pgvector 环境通过
- ✅ 已完成真实账号认证、作品创建、作品归档、分卷与章节增删改排、回收站和章纲编辑闭环
- ✅ Refresh session 持久化轮换、防重放、注销即时吊销，系统管理员与项目 RBAC 已接通
- ✅ 工作室成员管理、角色调整和作品共享已有真实 API 与 UI
- ✅ TXT/Markdown/DOCX/EPUB、分章 ZIP、完整 JSON 备份与非覆盖恢复已接通真实数据库
- ✅ 作者生成已接通真实用量台账、原子额度预留、按实际 token 结算、失败退款和过期预留回收
- ✅ 风格档已接通用户隔离 CRUD、真实六维抽取、作品绑定、生成提示与用量结算
- ✅ AI 来源账本已接通真实生成 run、段落指纹校验、编辑分类与采纳字数回写
- ✅ Docker Compose 已接通 migration、API、前端、PostgreSQL、Redis、Celery worker/dispatcher/beat 与 transactional outbox
- ✅ `/health/ready` 会实际探测 PostgreSQL、Redis 与 Alembic head，并以 503 暴露未就绪依赖
- ✅ Guard 已接入项目扫描、运行状态、真实告警证据与乐观锁处置
- ✅ 确定性 Guard 告警已接入有依据的 LLM 二次复核；失败保留规则告警且不自动替作者判误报
- ✅ Codex embedding 回填具有持久任务状态、失败次数、最后错误、耗尽标记与重试入口
- ✅ 设定库页面已接通真实新建、编辑、忽略候选和安全删除；人物档案与通用关键事实分表单维护
- ✅ 三条确定性规则已由真实 `RuleScanner` 跑过 120 正例、60 hard negatives、20 easy negatives，
  recall / 证据定位 / hard-negative precision 均为 100%
- ⚠️ 上述结构化 claim 评测不覆盖正文抽取、LLM 仲裁质量和 P1 其余四类规则，不能据此宣称全链路生产就绪

### 性能基准（可重复）

已加入 `server/scripts/benchmark_consistency.py`，用于在固定合成负载下比较确定性
规则 CPU 路径和上下文 HTML 转文本/分词开销。运行方式：

```powershell
cd server
..\.venv\Scripts\python.exe scripts\benchmark_consistency.py --sizes 10000 30000 100000 --repeats 5
```

脚本输出 JSON，包含 claim 数、规则扫描中位数/p95、上下文处理耗时和 tracemalloc
峰值内存。它明确不伪造网络指标：正文抽取、摘要、embedding、LLM 仲裁以及
PostgreSQL/pgvector 检索必须在真实部署上单独压测；该脚本只覆盖 RuleScanner 的
确定性规则和 ContextAssembler 的纯 CPU 热点。

---

## 已完成模块

### 1. 数据模型（34 张表，100% Alembic 覆盖）

#### 核心骨架 (6 张)
- `users` - 用户账号
- `projects` - 项目
- `volumes` - 卷
- `chapters` - 章节元信息
- `chapter_bodies` - 章节正文（独立存储）
- `chapter_versions` - 章节版本历史

`003_product_workflows.py` 在 baseline 之上补充账号密码字段、作品灵感/简介/故事骨架、
卷纲，以及章节章纲备注与修改时间，支持当前真实产品流程。
`012_content_lifecycle.py` 为卷和章节增加软删除时间与索引；正文读取、生成、记忆组装、
一致性扫描、来源分析和导出均排除回收站内容。

#### 设定库 (4 张)
- `codex_entries` - 设定条目（HALFVEC(2048) embedding 列）
- `codex_aliases` - 条目别名
- `codex_refs` - 章节对设定的引用
- `codex_relations` - 设定条目间关系

#### Embedding 运维状态 (1 张)
- `codex_embedding_jobs` - 每作品回填状态、尝试次数、剩余欠账、最后错误与 dead letter

#### 一致性基础设施 (4 张)
- `chapter_outline_states` - 章纲状态
- `chapter_outline_revisions` - 章纲版本快照
- `outbox_events` - Transactional outbox 事件
- `idempotency_records` - API 幂等记录

#### 一致性扩展 (8 张)
- `consistency_runs` - 一致性检查运行记录（三阶段状态机）
- `document_summaries` - 章节/卷摘要
- `consistency_claims` - 结构化事实声称（4 个部分唯一索引）
- `story_events` - 故事事件时间线
- `entity_state_intervals` - 实体状态区间
- `guard_issues` - 一致性告警（fingerprint 去重，issue_rev 乐观锁）
- `guard_issue_evidence` - 告警证据锚点
- `guard_resolutions` - 告警处置记录

#### 组织与协作 (3 张，成员与作品共享功能已开放)
- `orgs` - 组织
- `org_members` - 组织成员
- `chapter_assignments` - 章节分工

#### 认证与管理 (3 张)
- `auth_sessions` - 可吊销登录会话与 refresh token 轮换状态
- `system_settings` - 注册开关和新账号默认套餐/额度
- `admin_audit_logs` - 管理员修改审计记录

#### 风格/用量/占比 (5 张)
- `style_profiles` - 风格档案
- `generation_runs` - 生成任务记录
- `usage_logs` - 模型用量日志
- `ratio_reports` - 用量占比报告
- `foreshadows` - 伏笔倒计时

**Alembic 与 pgvector 状态**：
- ✅ **代码与迁移已实现**：`001_initial.py` 建立 baseline，
  `002_embedding_halfvec_2048.py` 清理旧向量并迁移到 HALFVEC(2048)，以
  `halfvec_cosine_ops` 重建 HNSW 索引；upgrade/downgrade 均会要求重新回填向量
- ✅ `alembic upgrade head --sql` 与 `alembic downgrade -1 --sql` 语法验证通过
- ✅ 36 个集成测试已在本机真实 PostgreSQL + pgvector 运行通过；当前审核数据库已真实执行
  `004_auth_admin_rbac -> 005_usage_ledger -> 006_usage_reservation_expiry -> 007_style_profiles -> 008_embedding_job_visibility -> 009_embedding_dispatch_attempts -> 010_claim_temporal_evidence -> 011_guard_issue_arbitration` 并到达当时的 head；`012_content_lifecycle` 已通过迁移 parity 测试，待本批 Docker 重建时应用到审核库

---

### 2. 核心服务层

#### Codex 设定库服务 (`services/codex.py`, `services/codex_embedding.py`)
- ✅ CRUD：create_entry, update_entry, add_alias, remove_alias
- ✅ Unicode NFC 规范化别名匹配
- ✅ 可检索文本变更判据（无变化不重算 embedding）
- ✅ 两段式事务：标脏先提交，网关后补向量
- ✅ 网关失败降级 `deferred`，不阻塞作者写入
- ✅ `refresh_embedding_if_stale` 幂等补向量
- ✅ `queued/running/retrying/succeeded/dead_letter` 状态持久化，记录失败次数、最后错误与耗尽时间
- ✅ 作者可查询当前新鲜/待补条目数，并在耗尽后明确重新入队
- ✅ worker 执行失败与 broker 重新投递次数独立计数；beat 自动回收发布窗口中断的过期 `queued` 作业

#### 章纲服务 (`services/outlines.py`)
- ✅ 独立章纲版本控制（与正文解耦）
- ✅ 已有正文时强制选择 `body_policy`：`plan_only` / `mark_body_for_revision`
- ✅ 修改章纲不写正文（不变量测试覆盖）
- ✅ Transactional outbox 事件生成

#### 正文服务 (`services/body.py`)
- ✅ 计算 content_hash（SHA-256 标准化 JSON）
- ✅ 提取 paragraph IDs（稳定 ProseMirror `pid`）
- ✅ 提取 CodexRef（节点级与标记级全量重建）
- ✅ 严格 `base_rev` 乐观锁校验
- ✅ 幂等保存（哈希匹配时不创建新版本）
- ✅ Transactional outbox 事件
- ✅ 保存后按真实生成段落指纹重算 `GenerationRun.accepted_words`；段落删除后自动回落

#### 前端正文保存保护 (`app/src/composables/use-autosave.ts`)
- ✅ 每章独立待保存队列；快速切章不会让后一章覆盖前一章的待同步正文
- ✅ IndexedDB 草稿启动恢复与显式取舍；服务端成功后清理已同步草稿
- ✅ 409 双版本解析与处置：采用云端，或基于最新 server_rev 保留本地再保存
- ✅ 网络失败保留本地草稿并提供手工重试；离线恢复后自动续传
- ✅ 编辑器正文同步回项目 store，切章返回不会重新灌入旧内容
- ✅ 版本历史抽屉按需加载历史正文，提供段落级差异、完整纯文本预览与二次确认恢复
- ✅ 恢复前强制保存当前章；离线、保存失败或冲突时禁止恢复，恢复结果作为新 head 且不改写旧快照

#### 一致性服务 (`services/consistency.py`)
- ✅ Claim fingerprint 计算（规范化主谓宾去重）
- ✅ 实体别名解析（Unicode NFC + 首尾 trim）
- ✅ Claim upsert 幂等（版本绑定，旧版本标 `superseded`）
- ✅ 三条确定性规则实现：
  1. `check_alive_conflict` - 生死冲突（时间线区间重叠）
  2. `check_ownership_conflict` - 物品归属冲突（同时刻不同归属）
  3. `check_knowledge_boundary` - 知情边界违规（先用后知）

#### RuleScanner (`services/rule_scanner.py`)
- ✅ `compute_issue_fingerprint`（SHA-256 issue_type + 排序证据键）
- ✅ `scan_chapter` 完整链路：加载 claims → 跑规则 → upsert issues → 标记 stale
- ✅ 章节级增量影响集：按新旧实体 ID、未解析主体与谓词族召回关联事实，超过 500 键自动退回全项目扫描
- ✅ 扫描返回 `claims_scanned` 与 `scan_scope`，可观察增量扫描是否降级
- ✅ Timeline-aware：`story_order` 为 NULL 时跳过需要时序的规则
- ✅ Issue 生命周期：fingerprint 去重、`issue_rev` 乐观锁、stale 标记
- ✅ 证据锚点写入（expected/actual GuardIssueEvidence）
- ✅ 不变证据重扫幂等（fingerprint 已知且证据未变时不写入，7af84ac）
- ✅ 新告警与实质证据变化标记 `pending`；相同证据保留既有仲裁且不增加 `issue_rev`
- ✅ 作者标记的 `false_positive` 不会因重扫或证据变化重新触发模型仲裁

#### LLM 冲突仲裁 (`services/arbitration.py`)
- ✅ 在 RuleScanner 已持久化确定性告警后异步执行，不阻塞规则告警生效
- ✅ 只从 claim 绑定的不可变 `ChapterVersion` 与 `P<n>` 锚点取证，单条原文最多 600 字
- ✅ 每批最多 20 个 case，复用 ConsistencyProvider 的 SSE、有限重试和模型配置
- ✅ 严格结构化结果：`supported` / `unsupported` / `uncertain`，置信度与理由持久化
- ✅ 模型调用前释放数据库事务；网关失败、畸形响应或漏项显式记为 `failed`
- ✅ fail-open：任何模型结论都不自动关闭规则告警，`unsupported` 只向作者提示“可能误报”
- ✅ 回写按 `run_id`、`pending` 和作者误报状态复核并加行锁；旧证据晚到结果不会覆盖新扫描

#### 时间线服务 (`services/timeline.py`)
- ✅ `parse_absolute_anchor`：解析 ISO-8601 形状的绝对时间
- ✅ `parse_relative_offset`：确定性解析分钟/小时/时辰/日/周的前后偏移（含中文数字和小数）
- ✅ `assign_story_orders`：从确认的全局锚点分配 story_order，支持当前批次链式引用和跨章节引用
- ✅ `is_globally_anchored`：四条件校验（order_basis, confidence, timeline_id, 可解析值）
- ✅ 时间原文、事件标签、关系、依据与置信度随 claim 持久化；后章可引用前章事件
- ⚠️ **保守限制**：
  - 仅支持 ISO-8601 形状的绝对时间（`2024-01-15`, `2024-01-15T10:30:00`）
  - 支持 `三日后`、`两小时前`、`半个时辰后` 等精确相对表达；`次日`、`过几日`、`一月后` 等日历相关或模糊时间不推断
  - **不支持** LLM 辅助的模糊时间表达规范化
  - 相对锚点必须同一时间线、精确唯一引用已确认事件、方向与原文一致且置信度至少 0.7；否则保持 NULL
  - 前序锚点后续改写时，既有下游相对 claim 不会主动级联重算，需通过项目重扫/管道升级重新抽取
  - 叙述局部序号（`narration_local`）与未知（`unknown`）保持 story_order=NULL

#### Embedding Provider (`services/embedding.py`)
- ✅ `GatewayEmbeddingProvider`：调用独立 OpenAI-compatible embedding 网关
- ✅ `embed_text` / `embed_batch`（doubao-embedding-vision, 2048 维）
- ✅ 异常分类：`EmbeddingProviderError` 用于重试判断
- ✅ httpx 超时与错误处理

#### 一致性模型 Provider (`providers/consistency.py`)
- ✅ 摘要与事实抽取统一使用 Chat Completions SSE，严格要求 `[DONE]`
- ✅ 聚合 `delta.content` 与 usage，支持 keepalive、任意网络字节分片和 error 帧
- ✅ timeout/transport error、HTTP 408/429/5xx 与半截流有限指数退避重试
- ✅ `reasoning_effort`、请求超时和重试参数均可由环境变量配置
- ✅ JSON 标量 `object_value` 规范化，单个布尔/数字值不会拖垮整章抽取
- ✅ claims 逐条校验；单条畸形输出隔离，整批畸形仍显式失败
- ✅ 真实首章链路验证：4699 可见字符、53 条 claims、2048 维 embedding
- ✅ 仲裁按 20 条分批，忽略陌生 case_id、漏项显式失败，并将故事原文声明为不可信 DATA

#### RAG 检索 (`services/retrieval.py`)
- ✅ **代码已实现**：`retrieve_similar_entities_l3` 使用 pgvector cosine distance (`<=>`)
- ✅ 距离阈值过滤，ORDER BY distance 高效最近邻
- ✅ pgvector 余弦距离与 HNSW 相关集成测试已在真实 PostgreSQL 环境通过

#### 导出与备份 (`services/exporting.py`)
- ✅ 服务端全量读取章节正文，不依赖前端是否打开过章节
- ✅ TXT / Markdown 单文件与分章 ZIP；DOCX / EPUB 标准容器
- ✅ 完整 JSON 备份包含卷、正文 rev、正文版本、章纲历史、设定别名与关系
- ✅ 备份主动排除 embedding；恢复时重建 ID 并创建新作品，不覆盖原稿
- ✅ 服务端与浏览器均限制 100 MB 备份，并区分文件、格式、权限与服务错误
- ✅ 真实 `p1` 验收：32 章、34,638 字、24 条设定，TXT/DOCX/EPUB/1.45 MB 备份均生成成功

#### 用量与额度 (`services/usage.py`)
- ✅ 生成前锁定用户行并按最大输出预留积分，并发请求不能透支同一余额
- ✅ 生成完成后使用网关真实 prompt/cached/completion token 结算并退回差额
- ✅ 网关失败、内部错误和浏览器中断释放预留；进程崩溃遗留预留在一小时后惰性回收
- ✅ 每月额度按 `quota_resets_at` 惰性重置，不依赖单点定时任务
- ✅ 基础/高级输入输出费率及缓存折算比例可由管理员配置，历史记录保留计价快照
- ✅ 风格抽取按真实 prompt/cached/completion token 结算；失败释放预留额度
- ⚠️ 自动一致性抽取、摘要和 embedding 是平台后台任务，目前不扣作者积分，也尚未进入统一成本台账

#### 风格指纹 (`services/style_profiles.py`)
- ✅ 样文按用户隔离存储，上限 50 万字符，任何 API 响应均不返回样文原文
- ✅ 抽取时均匀采样开头、中段和结尾，最多 3 万字符，不会只截断书稿尾部
- ✅ 5,000 字以下拒绝抽取；5,000–49,999 字标记低置信度；5 万字以上为标准置信度
- ✅ 句式节奏、对白习惯、描写密度、意象感官、章末钩子、惯用/禁用表达六维结构化输出
- ✅ 样文在系统提示中被明确标为不可信数据；生成提示只读取统计指纹，不读取样文原文
- ✅ 真实网关验收：5,940 字样文抽取为 `ready`，实际结算 13 积分

#### AI 内容来源 (`services/provenance.py`)
- ✅ 生成完成时只保存原始段落指纹，不复制正文原文
- ✅ 来源标记必须匹配同作品、同章节的真实 `GenerationRun` 及服务端原始指纹
- ✅ 原文指纹未变归为 `ai-raw`，作者修改后归为 `ai-edited`，无可信标记归为 `human`
- ✅ 同一生成段落指纹不可超出原始出现次数重复认领；跨章与伪造 run 均按手写处理
- ✅ 全书范围只返回聚合，避免把整本正文通过报告端点一次性下发
- ✅ 全书统计一次预取生成记录，无按章节查询 run 的 N+1

---

### 3. API 端点（全部需认证 + 项目权限）

#### 认证 (`api/auth.py`)
- ✅ `POST /auth/register` - 邮箱密码注册
- ✅ `POST /auth/login` - 邮箱密码登录
- ✅ `POST /auth/refresh` - 持久 session 内轮换 refresh JWT，旧 token 重放返回 401
- ✅ `POST /auth/logout` / `POST /auth/logout-all` - 吊销当前/全部会话，access token 即时失效
- ✅ `GET /auth/me` - 查询当前账号
- ✅ PBKDF2-SHA256 密码存储，access/refresh token 类型隔离
- ✅ 禁用账号即时拒绝既有 access token、登录和 refresh

#### 管理员与协作 (`api/admin.py`, `api/orgs.py`)
- ✅ `GET /admin/overview`、`GET/PATCH /admin/users`、`GET/PATCH /admin/settings`
- ✅ `admin` / `super_admin` 系统角色边界；只有超级管理员可调整系统角色
- ✅ 管理端不返回 API key，只返回模型名称与“是否配置”状态
- ✅ 工作室创建、成员列表/添加/改角色/移除；禁止移除或降级最后一个 owner
- ✅ owner / lead / writer / editor / viewer 动作级权限矩阵
- ✅ 只有作品 owner 且同时是工作室 owner 时才能把作品挂入工作室
- ✅ 前端 `/admin` 和 `/projects/:projectId/access` 已在真实 PostgreSQL 环境验收

#### 导出与备份 (`api/exports.py`)
- ✅ `GET /projects/{id}/export` - TXT/Markdown/DOCX/EPUB 与分章 ZIP
- ✅ `GET /projects/{id}/backup` - 下载可移植的完整 JSON 备份
- ✅ `POST /projects/restore-backup` - 校验备份并恢复为当前用户的新作品
- ✅ 所有下载均执行项目 `export` 动作权限检查，跨租户请求返回 403

#### 用量 (`api/usage.py`)
- ✅ `GET /usage/summary` - 当前真实余额、本期已结算用量、按功能聚合、14 天序列及最近 20 笔
- ✅ 仅返回当前认证用户数据；released/reserved 与上月记录不计入本期实际消费
- ✅ 余额不足在模型请求前返回 402，包含本次最大需求和当前余额

#### AI 来源 (`api/provenance.py`)
- ✅ `GET /projects/{id}/provenance?scope=chapter&chapter_id=...` - 本章段落级来源账本
- ✅ `GET /projects/{id}/provenance?scope=book` - 全书来源聚合，不返回正文段落
- ✅ 只依据系统实际生成记录，不提供不可解释的“疑似 AI 句式检测”

#### 风格档 (`api/styles.py`)
- ✅ `GET/POST /styles`、`GET/PATCH/DELETE /styles/{id}` - 用户隔离 CRUD
- ✅ `POST /styles/{id}/extract` - 真实模型抽取，显式 pending/processing/ready/failed 状态
- ✅ `PUT /projects/{id}/style-profile` - 仅作品 owner 可绑定自己的 ready 风格档或解绑
- ✅ 每个用户只有一个默认档；删除默认档会晋升替代档，删除已绑定档由外键自动解绑作品

#### 项目 (`api/projects.py`)
- ✅ `POST /projects` - 创建作品、分卷、首章和初始设定条目
- ✅ `GET/PATCH /projects/{id}` - 项目详情与书名、题材、状态、每日目标设置
- ✅ `GET /projects/{id}/chapters` - 章节列表（含章纲状态，不含正文）
- ✅ `POST /projects/{id}/chapters` - 在指定位置插入章节并重排全局序号
- ✅ 卷新建、编辑、整体重排与软删除；非空卷删除前必须明确章节接收卷
- ✅ 章节卷内排序、跨卷移动与软删除；禁止删除作品最后一个有效卷或章节
- ✅ `GET /projects/{id}/trash` 及卷/章恢复、永久删除端点；永久删除非空卷会被拒绝
- ✅ 回收站章节不会被读取、保存、生成、扫描、用于 RAG/长文本上下文或导出

#### 章节 (`api/chapters.py`)
- ✅ `GET /chapters/{id}` - 章节详情（含正文 + rev）
- ✅ `GET /chapters/{id}/versions` - 轻量版本元数据列表（默认最近 50 条，不返回完整正文）
- ✅ `GET /chapters/{id}/versions/{rev}` - 按需读取单个历史正文
- ✅ `POST /chapters/{id}/versions/{rev}/restore` - 带乐观锁与幂等键恢复为新 head，保留全部历史
- ✅ `PUT /chapters/{id}/body` - 保存正文（乐观锁 + Idempotency-Key）
  - ✅ 409 冲突响应（双方内容）
  - ✅ 422 校验失败
  - ✅ 202 异步入队
  - ✅ 调用 `services.body.save_chapter_body`

#### 章纲 (`api/outlines.py`)
- ✅ `PUT /chapters/{id}/outline` - 保存章纲（独立乐观锁）
- ✅ `GET /chapters/{id}/outline/revisions` - 章纲历史
- ✅ `POST /chapters/{id}/body-revision/resolve` - 确认正文调整状态

#### 设定库 (`api/codex.py`)
- ✅ `POST /codex/{project_id}/entries` - 创建条目
- ✅ `PATCH /codex/{project_id}/entries/{entry_id}` - 原子更新条目与全部别名，最多触发一次 embedding
- ✅ `DELETE /codex/{project_id}/entries/{entry_id}` - 删除候选/未引用条目；已确认且被正文引用时返回 409
- ✅ `POST /codex/{project_id}/entries/{entry_id}/aliases` - 添加别名
- ✅ `DELETE /codex/{project_id}/entries/{entry_id}/aliases` - 删除别名
- ✅ `POST /codex/{project_id}/backfill-embeddings` - 同步回填向量（受项目权限保护）
- ✅ `GET /codex/{project_id}/embedding-status` - 持久查询索引健康度与 dead letter
- ✅ `POST /codex/{project_id}/embedding-backfill` - 幂等入队或重新执行耗尽任务

#### 一致性状态 (`api/consistency.py`, prefix `/consistency`)
- ✅ `GET /consistency/status/{chapter_id}/{body_rev}` - 章节版本一致性状态（三阶段）
- ✅ `POST /consistency/scan` - 触发手工一致性扫描
- ✅ `GET /consistency/issues/{project_id}` - 项目告警列表（可按 chapter_id/status 过滤）
- ✅ `GET /consistency/issues/{project_id}/{issue_id}` - 告警详情
- ✅ `POST /consistency/issues/{project_id}/{issue_id}/resolve` - 提交处置（issue_rev 乐观锁）
- ✅ `POST /consistency/projects/{project_id}/scan` - 扫描项目当前全部章节版本
- ✅ `GET /consistency/projects/{project_id}/overview` - Guard 聚合状态、最新运行与告警证据
- ✅ 手工重扫会恢复失败、完成或超过 31 分钟未更新的丢失运行；硬时限内的活跃运行不会重复排队
- ✅ 列表与详情返回模型复核状态、置信度和理由；详情额外返回模型、版本、错误和复核时间

---

### 4. Celery 异步任务

#### 任务定义 (`tasks/consistency.py`)
- ✅ `process_body_saved` - 创建 ConsistencyRun，调度后续任务
- ✅ `extract_claims` - LLM 结构化抽取，生成 embedding，保存 claims
- ✅ `generate_summary` - LLM 生成摘要，带 embedding
- ✅ `scan_rules` - 运行 RuleScanner，更新 run 状态
- ✅ `arbitrate_issues` - 扫描成功后对新增/变更告警做有依据的二次复核，失败降级但保留规则结果
- ✅ `dispatch_outbox` - 租约批量领取，按 topic 路由，标记 sent/failed

#### Codex 回填任务 (`tasks/codex.py`)
- ✅ `backfill_codex_embeddings_task` - 项目级批量 embedding 回填
- ✅ `recover_stale_embedding_jobs` - 每分钟回收发布窗口中断的过期排队任务，独立走 outbox worker
- ✅ 指数退避重试：`autoretry_for`, `retry_backoff`, `max_retries=5`
- ✅ 逐批提交：`commit_each_batch=True`，重试幂等不重复烧配额
- ✅ httpx.HTTPError 纳入 RETRYABLE_ERRORS
- ✅ 每次尝试先落 `running`，失败落 `retrying`，第 6 次失败落 `dead_letter`
- ✅ Web 进程通过项目 Celery app 显式发往 Redis，避免绑定默认 AMQP broker
- ✅ worker 执行尝试与 broker 恢复投递独立计数；恢复连续失败 5 次才进入 `dead_letter`

#### Celery 配置 (`celery_app.py`)
- ✅ Redis broker + result backend
- ✅ Docker Compose 模型 worker（并发 2）、独立 outbox dispatcher（并发 1）与 beat
- ✅ beat 每 2 秒批量派发 outbox；独立队列避免长模型任务阻塞保存事件
- ✅ 显式任务导入，避免错误的 Django 风格 `tasks.tasks` 自动发现
- ✅ late ack + worker lost 重投；长章节按分块和网关重试设置有限的 30 分钟上限
- ✅ 软超时会写入 `*_timeout` 失败状态，不会把 run 永久留在运行中
- ✅ `chapter.body_saved` / `consistency.manual_scan` 路由；章纲事件确认消费
- ✅ 抽取后并行执行摘要与 `scan_rules -> arbitrate_issues`，仲裁不占用数据库长事务
- ✅ PostgreSQL dead-letter 保留非空 `available_at`，失败事务可正常提交

---

### 5. 测试覆盖

#### 单元测试（1001 passed，SQLite in-memory，mock providers）

**全量测试结果**：1001 passed, 36 skipped（未设置集成测试 URL 时）, 0 warnings；前端 73 passed

主要测试覆盖（不逐文件列举测试数量，以实际 pytest 结果为准）：
- ✅ Codex 设定库：页面与 API 完整 CRUD、引用删除保护、原子别名替换、可检索文本判据、两段式事务、deferred 降级、httpx 错误重试
- ✅ 章纲服务：独立版本控制、body_policy 约束、不变量测试、outbox 事件
- ✅ 正文服务：content_hash 幂等性、paragraph ID 提取、CodexRef 提取
- ✅ 一致性服务：Claim fingerprint、规则逻辑、hard negative 案例
- ✅ RuleScanner：三条规则检测、timeline-aware 跳过、stale 标记、fingerprint 去重
- ✅ 认证授权：JWT 解码、项目权限、Idempotency-Key 必需性
- ✅ Alembic 迁移：34 张表、pgvector extension、部分唯一索引、downgrade 完整性
- ✅ 时间锚点：ISO-8601、确定性相对时长、跨章事件引用、源锚点与事件标签原文校验
- ✅ 增量影响集：新旧实体重绑定、未解析主体、谓词族闭包、全项目安全降级与扫描遥测
- ✅ LLM 仲裁：不可变版本取证、600 字截断、20 条分批、陌生/缺失/畸形响应、失败降级、事务释放与前端映射
- ✅ 项目、卷、章节、章纲 CRUD，跨卷排序、软删除/恢复/永久删除、乐观锁冲突、Outbox 与幂等性
- ✅ Foreshadow 伏笔倒计时、用量统计、风格档案
- ✅ 风格档跨租户隔离、默认唯一、抽取成功/失败、失败退款、owner 绑定、删除自动解绑与提示隐私
- ✅ AI 来源：真实 run/段落指纹校验、编辑后分类、重复与跨章节伪造防护、采纳字数回落
- ✅ **持久失败契约测试**：`test_documentation_reflects_persistent_dead_letter_visibility`
  守住 embedding 重试耗尽后仍可查询、可重试的任务契约

#### 评测框架 (`tests/consistency/fixtures_eval.py`, `test_eval.py`)
- ✅ 版本化 `rule-eval-v1`：120 正例 + 60 hard negatives + 20 easy negatives
- ✅ 评测会把 400 条 claim 写进真实 ORM，调用公开的 `RuleScanner.scan_chapter()`，再读取持久化 issue/evidence；
  不再依据 `expected_conflict` 假算检测结果
- ✅ 指标计算：overall recall、三规则 macro recall、证据两端 `evidence_recall`、hard/easy-negative precision、额外告警数
- ✅ 当前结果：recall 100% (120/120)、macro recall 100%、证据定位 100% (120/120)、
  hard-negative precision 100% (60/60)、easy-negative precision 100% (20/20)、额外告警 0
- ⚠️ 这是结构化 claim 层的合成边界评测，只覆盖生死、归属、知情边界三条 P0 规则；
  尚未覆盖正文到 claim 的抽取误差、真实小说盲评、LLM 仲裁判断和 P1 其余四类规则

#### 集成测试（36 tests，真实 PostgreSQL + pgvector 已通过）
- ✅ Docker PostgreSQL + pgvector 环境已执行 36 个测试并全部通过
- ✅ 审核数据库已执行 `011_guard_issue_arbitration` 到 Alembic head
- 覆盖内容：
  - 部分唯一索引（`postgresql_where`）的并发 upsert 去重
  - pgvector `<=>` 余弦距离与 HNSW 索引
  - `INSERT ... ON CONFLICT` upsert 语义
  - GIN 索引
  - CHECK 约束在并发/边界数据下的真实拒绝行为
- 默认 SQLite 全量命令未设置 `TEST_POSTGRES_URL` 时仍会显示这些用例 skipped；真实数据库结果单独记录。

---

## 已知缺口与限制

### 1. Codex Embedding 回填失败可见性
**状态**：已完成

**已有**：
- ✅ 指数退避重试（5 次，最大间隔 600s）
- ✅ httpx.HTTPError 纳入重试异常
- ✅ `remaining_count` 在 POST 响应中返回
- ✅ `codex_embedding_jobs` 持久区分首次待补、运行、重试、成功和永久失败
- ✅ `GET /codex/{project_id}/embedding-status` 返回当前欠账、失败次数、最后错误与耗尽时间
- ✅ `POST /codex/{project_id}/embedding-backfill` 防重复入队，并允许 dead letter 明确重试
- ✅ Web 进程若在状态提交后、broker 发布前退出，beat 会在 120 秒租约后重新投递
- ✅ 恢复投递与模型执行分别计数，避免 broker 故障污染作者看到的模型重试次数
- ✅ 真实 Docker 验收：临时作品 `pending -> queued -> ready`，1 次尝试后 1/1 条向量就绪并清理

### 2. 用户可见核心流程
**状态**：首轮闭环已完成

真实认证、作品创建、分卷章纲、章节插入、正文保存保护、Guard、设定库、导出、备份恢复、
风格档、AI 来源占比和作者生成用量均已接通真实 API。生产就绪仍受后述评测规模、LLM 仲裁、
长文本压测和后台模型成本台账限制，不能仅凭页面可用宣称全功能完工。

设定库当前允许作者直接新建和编辑人物/势力/地点/物品/力量体系/伏笔，人物字段按内核、
约束和人物弧维护，其他类型按“标签：内容”的关键事实维护。编辑时保留关系等未开放字段；
待确认抽取候选可直接忽略，已确认且已有正文引用的条目禁止删除，避免丢失显式引用语义。

### 3. 全链路评测覆盖仍不足
**状态**：三条确定性规则的结构化门禁已完成；真实正文与完整七规则未完成

**当前规模**：
- 正例：120 个（alive / ownership / knowledge boundary 各 40）
- Hard negatives：60 个（每条规则 20 个，覆盖自然状态变化、不同时间线、未知顺序、合法转移、不同知识对象）
- Easy negatives：20 个
- **总计：200 个结构化 claim 案例，真实执行 scanner 并核对持久化证据**

**架构要求**：
- 首版上线集：≥100 正例 + ≥50 hard negatives
- 数量门槛：已达到
- 七类规则覆盖：3/7；真实正文盲评：未达到

**当前 100% 指标的局限**：
- 指标来自真实 scanner 输出，不再硬编码，但输入仍是人工构造的结构化 claim
- 夹具不是正文，无法衡量 LLM 是否能从隐含、否定、转述和长距离上下文中正确抽取 claim
- 尚未实现/评测时间、能力、地理、伏笔四类 P1 规则
- 尚未对 LLM 仲裁做人工金标盲评
- **不能据此宣称生产就绪的召回率/精度**

**后续需要**：
1. 实现时间、能力、地理、伏笔四类规则并为七类规则保持 macro recall 门禁
2. 从有授权的真实小说/项目输出建立正文 -> claim -> issue 的人工金标 dev/holdout
3. 对 LLM 仲裁的 supported / unsupported / uncertain 建立独立混淆矩阵和失败率
4. 把真实 PostgreSQL 评测与 10/30/100 万字性能指标接入 CI/定期任务

### 4. 时间锚点解析限制
**状态**：确定性链已完成，模糊语义仍保守降级

**当前实现**：
- ✅ ISO-8601 形状绝对时间解析（`2024-01-15`, `2024-01-15T10:30:00`）
- ✅ `temporal_anchor_text` 自身的确定性解析要求
- ✅ `三日后`、`两小时前`、`半个时辰后` 等确定性相对偏移；月/年不按固定天数伪换算
- ✅ 同批次链式引用与跨章节事件引用；事件标签及时间证据持久化到 claim
- ✅ 歧义、跨时间线、低置信度、方向冲突和模糊时长保持 `story_order=NULL`

**保守限制**：
- ⚠️ **不支持** LLM 辅助的语义理解时间线
- ⚠️ `次日`、`过几日`、`年关前后` 等无法确定换算的表达不猜测
- ⚠️ 前序锚点改写后，下游既有 claim 需通过项目重扫/管道升级重新抽取，不主动级联重算

**后续需要**：
1. LLM 辅助时间表达规范化
2. 模糊时间跨度的区间表达
3. 锚点依赖图与下游自动失效/重算

### 5. 增量影响集
**状态**：首版已完成

**当前行为**：从被修改章节的 accepted + superseded 历史恢复影响键，按 subject/object 实体 ID
召回全项目关联 claim；未解析实体按规范化主体文本和 predicate family 召回，
`uses_knowledge`/`acquires_knowledge` 做闭包。缺少可靠影响键或超过 500 键时退回全项目扫描，
扫描结果返回 `claims_scanned` 与 `scan_scope`。

**后续优化**：加入时间线区间裁剪和基于真实长篇分布的阈值调优；当前安全策略优先避免漏检。

### 6. LLM 结构化仲裁
**状态**：首版已完成

**当前实现**：确定性规则先落告警，随后模型只根据不可变章节快照、claim 锚点和结构化证据给出
`supported` / `unsupported` / `uncertain` 建议。模型失败、畸形或漏项均记为 `failed`，规则告警仍保持开放。

**保守限制**：
1. 模型建议不参与 issue fingerprint、`issue_rev` 或自动处置
2. `unsupported` 不会自动标记误报，最终决定仍由作者提交
3. 仲裁、自动摘要/抽取和 embedding 尚未进入统一后台成本台账
4. 真实大规模误报改善程度仍需扩充评测集验证

---

## 提交历史（本轮 worktree）

本 worktree 在分支 `worktree-moshu-consistency-backend-v2` 上，基于主仓库已有的 30 张表
baseline，增量实现 Codex embedding 回填、时间锚点解析、issue 生命周期、run 状态机、文档修正等功能。

### Group 1: Issue 生命周期幂等强化
- `7af84ac` - fix: make a rescan of unchanged evidence touch nothing at all
  - 不变证据重扫幂等：fingerprint 已知且证据未变时不写入新行

### Group 2: ConsistencyRun 三阶段状态机
- `d241c54` - feat: add three-phase state machine for parallel summary/scan branches
  - 独立 extract/summary/scan 三条支线状态
  - 支持并行支线：摘要与扫描可独立成功/失败

### Group 3: 时间锚点解析与验证
- `d459c72` - feat: validate source anchors against chunk paragraph positions
  - 源锚点位置校验：paragraph_index 必须在 chunk 范围内
- `391197f` - fix: require deterministic parsing of temporal_anchor_text itself
  - `temporal_anchor_text` 自身必须确定性可解析

### Group 4: Codex Embedding 回填逻辑与文档修正
- `493e95c` - feat: add httpx.HTTPError retry handling and fix stale/deferred status reporting
  - httpx.HTTPError 纳入 RETRYABLE_ERRORS
  - 修正 `_settle_embedding()` 始终检查真实 embedding 哈希
  - 新增 4 个测试覆盖 httpx 错误与 deferred 状态
- `9baf42f` - docs: clarify remaining_count is a snapshot, not continuous dead-letter visibility
  - 修正 `tasks/codex.py`, `api/codex.py`, `services/codex.py` 误导性表述
  - 明确 `remaining_count` 是瞬时快照，非持续可查询
  - 新增 `test_documentation_accurately_reflects_missing_dead_letter_visibility` 守住诚实表述

### Group 5: 实现状态文档与事实修正
- `2e53f9a` - docs: comprehensive implementation status report with accurate limitations
  - 创建本文档初版，存在事实错误（API 路径、时间锚点能力、提交分组）
- `42a577a` - docs: fix implementation status report factual errors and supersede old reports
  - 修正 API 路径、时间锚点能力、提交分组
  - 为 5 个历史报告添加 SUPERSEDED banner
  - 区分「代码已实现」与「真实 PostgreSQL 未验证」

---

## 技术债务

### 1. 异步运行链路的模型吞吐仍受上游网关约束
**描述**：worker/dispatcher/beat、outbox 与 Guard 已形成真实闭环；10 万字样本实跑发现
网关会返回 502/524 和并发限制，当前以模型 worker 并发 2、独立 outbox 队列、SSE 重试、
有限长任务时限和失败可见性处理。

**风险**：大项目首次全书扫描仍可能较慢或需要手工重扫失败章节，但不会静默卡在 outbox。

**优先级**：高。继续记录真实耗时与失败率，再决定是否拆分更细任务或增加网关容量。

### 2. 全链路评测覆盖不足
**描述**：三条确定性规则已有 200 个真实 scanner 合成案例，但正文抽取、LLM 仲裁和完整七规则没有达到盲评门禁。

**风险**：结构化 claim 层 100% 不能代表真实正文端到端召回率与误报率。

**优先级**：高。补齐四类规则并建立真实正文 dev/holdout，不降低现有 120/60 结构化门禁。

### 3. 增量影响集尚未按时间区间裁剪
**描述**：实体、谓词族和旧版事实的影响闭包已实现；同一实体在很长时间线上的全部相关事实仍会加载。

**风险**：避免了无关实体的全项目扫描，但超长作品的高频核心人物仍可能形成较大影响集。

**优先级**：中。用 10/30/100 万字压测确定阈值后，再加入不会漏检的时间区间裁剪。

### 4. LLM 仲裁质量尚缺规模化评测
**描述**：仲裁链路、证据约束和失败降级已实现，但目前没有足够真实小说 case 证明其能把误报率降到目标。

**风险**：模型建议可能偏保守或在复杂叙事中给出 `uncertain`，不能用少量 smoke case 宣称质量达标。

**优先级**：高。与确定性规则一起纳入 100+ 正例、50+ hard negatives 的盲评。

---

## 下一步优先级

### 立即行动（阻塞生产部署）
1. **真实风格档与 AI 来源追踪**
   - ✅ 风格档 CRUD、样文抽取状态、作品绑定与生成接入已经完成
   - ✅ AI 插入段落、之后的人工修改与可解释来源统计已经完成

2. **扩充评测数据集**
   - ✅ 三条确定性规则已达到 120 正例 + 60 hard negatives，真实执行 scanner
   - ✅ 已设 overall/macro recall ≥70%、证据定位 ≥90%、hard-negative precision ≥80% 门禁
   - 补齐时间、能力、地理、伏笔并建立真实小说正文 dev/holdout

### 短期优先级（1-2 周）
1. **实现 Codex Embedding 持久失败可见性**
   - ✅ 增加 `codex_embedding_jobs` 表
   - ✅ 增加 GET 状态与 POST 重新入队端点
   - ✅ 更新 `backfill_codex_embeddings_task` 记录每次尝试和耗尽失败

2. **验证 LLM 结构化仲裁质量**
   - ✅ 规则后置二次复核、结构化结果、失败降级和前端提示已经完成
   - 用扩充评测集统计各 verdict 的准确率、覆盖率与失败率
   - 将自动一致性、摘要和 embedding 纳入后台成本台账

3. **补齐创作工作流 P0 缺口**
   - ✅ 正文版本历史浏览、段落级对比与指定版本恢复已完成
   - AI 多候选草稿持久化，关闭页面后仍可继续比较与采纳
   - 全书查找替换，带范围、预览、撤销和设定名安全检查

### 中期优先级（3-4 周）
1. **增量影响集优化**
   - ✅ 按实体与 predicate family 构造影响集，只扫描相关 claims
   - 加入不会漏检的时间线区间裁剪，并用长文本压测校准 500 键降级阈值

2. **时间锚点 LLM 辅助**
   - 模糊时间表达规范化
   - 已持久化锚点的依赖图与下游自动重算

3. **竞品常见的深度规划与审稿能力**
   - 多剧情线时间板、人物出场与视角统计、设定随章节变化的状态历史
   - 资料与正文并排、批注/审稿流程、Prompt Preview 与用户自带模型配置
   - 关系图、地图、日历、出版排版和平台发布数据属于后续增强，不阻塞核心写作闭环

---

## 文件清单

### 数据模型（7 个文件）
- `server/db/models_core.py` - 核心骨架 6 张表
- `server/db/models_codex.py` - 设定库 4 张表
- `server/db/models_guard.py` - 守卫 2 张表
- `server/db/models_consistency.py` - 一致性基础 4 张表
- `server/db/models_consistency_extended.py` - 一致性扩展 8 张表
- `server/db/models_usage.py` - 风格/用量 4 张表
- `server/db/models_org.py` - 组织 3 张表
- `server/db/models_admin.py` - 会话、运行设置与审计 3 张表

### 服务层（13 个文件）
- `server/services/codex.py` - 设定库 CRUD
- `server/services/codex_embedding.py` - Embedding 生命周期
- `server/services/outlines.py` - 章纲服务
- `server/services/body.py` - 正文保存
- `server/services/consistency.py` - 一致性服务
- `server/services/rule_scanner.py` - 规则扫描器
- `server/services/arbitration.py` - 有依据的 LLM 冲突仲裁与失败降级
- `server/services/timeline.py` - 时间线服务
- `server/services/retrieval.py` - RAG 检索
- `server/services/embedding.py` - Embedding provider
- `server/services/outbox.py` - Outbox 服务
- `server/services/idempotency.py` - 幂等服务
- `server/services/usage.py` - 用量预留、结算、退款与月度额度
- `server/services/style_profiles.py` - 风格样文采样、网关抽取与六维结果校验

### API 端点（12 个文件）
- `server/api/auth.py` - 认证与授权
- `server/api/projects.py` - 项目管理
- `server/api/chapters.py` - 章节读写
- `server/api/outlines.py` - 章纲管理
- `server/api/codex.py` - 设定库 CRUD
- `server/api/consistency.py` - 一致性状态查询
- `server/api/admin.py` - 系统管理、账号与运行设置
- `server/api/orgs.py` - 工作室成员与作品共享
- `server/api/exports.py` - 全量导出、备份与恢复
- `server/api/usage.py` - 真实余额、聚合和逐笔用量
- `server/api/styles.py` - 风格档 CRUD、抽取与作品绑定
- `server/main.py` - FastAPI 入口

### 异步任务（3 个文件）
- `server/celery_app.py` - Celery 配置
- `server/tasks/consistency.py` - 一致性任务
- `server/tasks/codex.py` - Codex 回填任务

### 测试（1001 passed；另有 36 个真实 PostgreSQL 测试通过）
- `server/tests/` - 单元/功能测试（1001 passed）
- `app/src/**/*.spec.ts` - 前端测试（73 passed）
- `server/tests/integration/` - 集成测试（36 passed，需设置真实 PostgreSQL URL）

### 文档（1 个文件）
- `server/docs/IMPLEMENTATION_STATUS.md` - **本文档**（唯一当前事实来源）

---

## 总结

墨枢一致性后端已完成核心数据模型、服务层、API 端点和异步任务定义，1001 个单元/功能
测试在 SQLite in-memory + mock providers 环境下通过，另有 36 个集成测试在真实
PostgreSQL + pgvector 环境通过。真实认证、可吊销会话、管理员、工作室 RBAC、作品创建、
作品归档、分卷与章节生命周期、章纲、正文版本历史与恢复、全量导出、非覆盖备份恢复、风格指纹、AI 来源账本与作者生成用量台账已经接通，前端 73 个测试与生产构建通过。

**关键限制**：
1. 自动一致性与 embedding 后台模型成本尚未进入统一台账
2. 三条确定性规则的 120/60 结构化评测门禁已完成，但真实正文盲评、其余四类规则、模糊时间区间与锚点依赖级联尚未完成
3. 当前 100% 指标来自真实 scanner 而非硬编码，但输入仍是合成 claim，**不代表正文抽取和 LLM 仲裁的端到端质量**
4. 模糊时间语义理解与影响集时间区间裁剪未实现；仲裁只提供建议，不自动处置
5. 真实长文本扫描吞吐受上游模型网关稳定性和并发限制影响

当前是“核心一致性能力 + 首轮真实产品流程”，不是功能完整 MVP。生产部署前仍需完成上述产品闭环、
扩充评测集并验证长文本规模下的质量和性能。
