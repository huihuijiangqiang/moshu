# 墨枢一致性后端实现状态报告

## 执行摘要

本文档记录墨枢一致性后端在 `worktree-moshu-consistency-backend-v2` 分支的真实实现状态。
所有声明基于实际代码与测试结果，不夸大、不省略已知缺口。

**关键事实**：
- ✅ 30 张表完整 Alembic baseline，`004_auth_admin_rbac` 再增加 3 张安全与管理表
- ✅ 893 个单元/功能测试通过（SQLite in-memory，mock embedding/LLM）
- ✅ 前端 51 个测试、TypeScript 类型检查和生产构建通过
- ✅ 36 个集成测试已在本机真实 PostgreSQL + pgvector 环境通过
- ✅ 已完成真实账号认证、作品创建、分卷章纲编辑与章节插入的首轮产品闭环
- ✅ Refresh session 持久化轮换、防重放、注销即时吊销，系统管理员与项目 RBAC 已接通
- ✅ 工作室成员管理、角色调整和作品共享已有真实 API 与 UI
- ✅ Docker Compose 已接通 PostgreSQL、Redis、Celery worker/dispatcher/beat 与 transactional outbox
- ✅ Guard 已接入项目扫描、运行状态、真实告警证据与乐观锁处置
- ⚠️ Codex embedding 回填的持久失败可见性尚未实现
- ⚠️ 评测夹具仅 10 个 smoke cases，硬编码 100% 指标不代表实际质量

---

## 已完成模块

### 1. 数据模型（33 张表，100% Alembic 覆盖）

#### 核心骨架 (6 张)
- `users` - 用户账号
- `projects` - 项目
- `volumes` - 卷
- `chapters` - 章节元信息
- `chapter_bodies` - 章节正文（独立存储）
- `chapter_versions` - 章节版本历史

`003_product_workflows.py` 在 baseline 之上补充账号密码字段、作品灵感/简介/故事骨架、
卷纲，以及章节章纲备注与修改时间，支持当前真实产品流程。

#### 设定库 (4 张)
- `codex_entries` - 设定条目（HALFVEC(2048) embedding 列）
- `codex_aliases` - 条目别名
- `codex_refs` - 章节对设定的引用
- `codex_relations` - 设定条目间关系

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
  `003_product_workflows -> 004_auth_admin_rbac` 并到达 head

---

### 2. 核心服务层

#### Codex 设定库服务 (`services/codex.py`, `services/codex_embedding.py`)
- ✅ CRUD：create_entry, update_entry, add_alias, remove_alias
- ✅ Unicode NFC 规范化别名匹配
- ✅ 可检索文本变更判据（无变化不重算 embedding）
- ✅ 两段式事务：标脏先提交，网关后补向量
- ✅ 网关失败降级 `deferred`，不阻塞作者写入
- ✅ `refresh_embedding_if_stale` 幂等补向量
- ✅ **已知缺口**：无持久 dead-letter 状态表、无失败次数/最后错误/耗尽标记，
  `remaining_count` 仅为瞬时快照，无独立 GET 状态端点持续监控永久失败

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

#### 前端正文保存保护 (`app/src/composables/use-autosave.ts`)
- ✅ 每章独立待保存队列；快速切章不会让后一章覆盖前一章的待同步正文
- ✅ IndexedDB 草稿启动恢复与显式取舍；服务端成功后清理已同步草稿
- ✅ 409 双版本解析与处置：采用云端，或基于最新 server_rev 保留本地再保存
- ✅ 网络失败保留本地草稿并提供手工重试；离线恢复后自动续传
- ✅ 编辑器正文同步回项目 store，切章返回不会重新灌入旧内容

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
- ✅ Timeline-aware：`story_order` 为 NULL 时跳过需要时序的规则
- ✅ Issue 生命周期：fingerprint 去重、`issue_rev` 乐观锁、stale 标记
- ✅ 证据锚点写入（expected/actual GuardIssueEvidence）
- ✅ 不变证据重扫幂等（fingerprint 已知且证据未变时不写入，7af84ac）

#### 时间线服务 (`services/timeline.py`)
- ✅ `parse_absolute_anchor`：解析 ISO-8601 形状的绝对时间
- ✅ `assign_story_orders`：从确认的全局锚点分配 story_order
- ✅ `is_globally_anchored`：四条件校验（order_basis, confidence, timeline_id, 可解析值）
- ⚠️ **MVP 保守限制**：
  - 仅支持 ISO-8601 形状的绝对时间（`2024-01-15`, `2024-01-15T10:30:00`）
  - **不支持**「第 N 天」「N 年后」等自然语言相对表达
  - **不支持** LLM 辅助的模糊时间表达规范化
  - 相对锚点（`relative_to_anchor`）保留 `temporal_relation`/`temporal_relation_ref` 作为证据，
    但不分配 story_order —— 相对解析链未实现，保持 NULL 进待确认
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

#### RAG 检索 (`services/retrieval.py`)
- ✅ **代码已实现**：`retrieve_similar_entities_l3` 使用 pgvector cosine distance (`<=>`)
- ✅ 距离阈值过滤，ORDER BY distance 高效最近邻
- ✅ pgvector 余弦距离与 HNSW 相关集成测试已在真实 PostgreSQL 环境通过

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

#### 项目 (`api/projects.py`)
- ✅ `POST /projects` - 创建作品、分卷、首章和初始设定条目
- ✅ `GET /projects/{id}` - 项目详情
- ✅ `GET /projects/{id}/chapters` - 章节列表（含章纲状态，不含正文）
- ✅ `POST /projects/{id}/chapters` - 在指定位置插入章节并重排全局序号

#### 章节 (`api/chapters.py`)
- ✅ `GET /chapters/{id}` - 章节详情（含正文 + rev）
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
- ✅ `PATCH /codex/{project_id}/entries/{entry_id}` - 更新条目
- ✅ `POST /codex/{project_id}/entries/{entry_id}/aliases` - 添加别名
- ✅ `DELETE /codex/{project_id}/entries/{entry_id}/aliases` - 删除别名
- ✅ `POST /codex/{project_id}/backfill-embeddings` - 同步回填向量（受项目权限保护）
  - ⚠️ `remaining_count` 是本次响应的瞬时快照，非持续可查询状态

#### 一致性状态 (`api/consistency.py`, prefix `/consistency`)
- ✅ `GET /consistency/status/{chapter_id}/{body_rev}` - 章节版本一致性状态（三阶段）
- ✅ `POST /consistency/scan` - 触发手工一致性扫描
- ✅ `GET /consistency/issues/{project_id}` - 项目告警列表（可按 chapter_id/status 过滤）
- ✅ `GET /consistency/issues/{project_id}/{issue_id}` - 告警详情
- ✅ `POST /consistency/issues/{project_id}/{issue_id}/resolve` - 提交处置（issue_rev 乐观锁）
- ✅ `POST /consistency/projects/{project_id}/scan` - 扫描项目当前全部章节版本
- ✅ `GET /consistency/projects/{project_id}/overview` - Guard 聚合状态、最新运行与告警证据
- ✅ 手工重扫会恢复失败、完成或超过 31 分钟未更新的丢失运行；硬时限内的活跃运行不会重复排队

---

### 4. Celery 异步任务

#### 任务定义 (`tasks/consistency.py`)
- ✅ `process_body_saved` - 创建 ConsistencyRun，调度后续任务
- ✅ `extract_claims` - LLM 结构化抽取，生成 embedding，保存 claims
- ✅ `generate_summary` - LLM 生成摘要，带 embedding
- ✅ `scan_rules` - 运行 RuleScanner，更新 run 状态
- ✅ `dispatch_outbox` - 租约批量领取，按 topic 路由，标记 sent/failed

#### Codex 回填任务 (`tasks/codex.py`)
- ✅ `backfill_codex_embeddings_task` - 项目级批量 embedding 回填
- ✅ 指数退避重试：`autoretry_for`, `retry_backoff`, `max_retries=5`
- ✅ 逐批提交：`commit_each_batch=True`，重试幂等不重复烧配额
- ✅ httpx.HTTPError 纳入 RETRYABLE_ERRORS
- ⚠️ **已知缺口**：耗尽重试后数据留 stale，但无持久失败状态或独立查询接口

#### Celery 配置 (`celery_app.py`)
- ✅ Redis broker + result backend
- ✅ Docker Compose 模型 worker（并发 2）、独立 outbox dispatcher（并发 1）与 beat
- ✅ beat 每 2 秒批量派发 outbox；独立队列避免长模型任务阻塞保存事件
- ✅ 显式任务导入，避免错误的 Django 风格 `tasks.tasks` 自动发现
- ✅ late ack + worker lost 重投；长章节按分块和网关重试设置有限的 30 分钟上限
- ✅ 软超时会写入 `*_timeout` 失败状态，不会把 run 永久留在运行中
- ✅ `chapter.body_saved` / `consistency.manual_scan` 路由；章纲事件确认消费
- ✅ PostgreSQL dead-letter 保留非空 `available_at`，失败事务可正常提交

---

### 5. 测试覆盖

#### 单元测试（893 passed，SQLite in-memory，mock providers）

**全量测试结果**：893 passed, 36 skipped（未设置集成测试 URL 时）, 4 warnings；前端 51 passed

主要测试覆盖（不逐文件列举测试数量，以实际 pytest 结果为准）：
- ✅ Codex 设定库：CRUD、别名规范化、可检索文本判据、两段式事务、deferred 降级、httpx 错误重试
- ✅ 章纲服务：独立版本控制、body_policy 约束、不变量测试、outbox 事件
- ✅ 正文服务：content_hash 幂等性、paragraph ID 提取、CodexRef 提取
- ✅ 一致性服务：Claim fingerprint、规则逻辑、hard negative 案例
- ✅ RuleScanner：三条规则检测、timeline-aware 跳过、stale 标记、fingerprint 去重
- ✅ 认证授权：JWT 解码、项目权限、Idempotency-Key 必需性
- ✅ Alembic 迁移：30 张表、pgvector extension、部分唯一索引、downgrade 完整性
- ✅ 时间锚点：ISO-8601 解析、源锚点位置校验、temporal_anchor_text 确定性要求
- ✅ 项目、章节、章纲 CRUD、乐观锁冲突、Outbox 与幂等性
- ✅ Foreshadow 伏笔倒计时、用量统计、风格档案
- ✅ **文档断言测试**：`test_documentation_accurately_reflects_missing_dead_letter_visibility`
  守住「瞬时快照」「非持续可查询」等准确表述

#### 评测框架 (`tests/consistency/fixtures_eval.py`, `test_eval.py`)
- ✅ 10 个 smoke cases：3 正例 + 5 hard negatives + 2 easy negatives
- ✅ 指标计算：recall, false_positive_rate, hard_negative_precision
- ✅ Smoke test 结果：recall 100%, FPR 0%, hard negative precision 100%
- ⚠️ **硬编码指标不代表实际质量**：夹具仅 10 个合成案例，规则逻辑为简化概念验证版本，
  距离真实小说场景的规模化评测（≥100 正例 + ≥50 hard negatives）差距巨大

#### 集成测试（36 tests，真实 PostgreSQL + pgvector 已通过）
- ✅ Docker PostgreSQL + pgvector 环境已执行 36 个测试并全部通过
- ✅ 审核数据库已执行 `004_auth_admin_rbac` 到 Alembic head
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
**状态**：部分实现

**已有**：
- ✅ 指数退避重试（5 次，最大间隔 600s）
- ✅ httpx.HTTPError 纳入重试异常
- ✅ `remaining_count` 在 POST 响应中返回

**缺失**：
- ❌ 无独立 dead-letter 状态表
- ❌ 无失败次数、最后错误、耗尽标记
- ❌ 无只读 GET 项目状态端点
- ❌ `remaining_count` 仅为本次成功响应的瞬时快照，无法持续查询
- ❌ 无法区分「首次待补」与「永久失败」

**影响**：任务耗尽重试后，数据留在 stale 状态，但无法通过 API 持续监控失败可见性。
架构 8「永久失败在项目状态接口可见」尚未满足。

**后续需要**：
1. 增加 `codex_backfill_failures` 表：记录失败次数、最后错误、耗尽时间戳
2. 增加 `GET /codex/{project_id}/embedding-status` 端点：返回持久失败状态
3. 文档断言测试守住诚实表述，防止未实现功能被误导性宣称

### 2. 产品功能仍有占位实现
**状态**：进行中

真实认证、作品创建、分卷章纲、章节插入、正文保存保护和 Guard 运行闭环已经接通；以下用户可见页面仍有 mock 或静态展示：
- 导出、文风、AI 占比、用量页面尚未全部接入真实后端

**风险**：当前不能称为功能完整 MVP，也不能把所有页面展示视为真实数据。

### 3. 评测数据集规模不足
**状态**：概念验证

**当前规模**：
- 正例：3 个（alive, ownership, knowledge boundary）
- Hard negatives：5 个
- Easy negatives：2 个
- **总计：10 个合成案例**

**架构要求**：
- 首版上线集：≥100 正例 + ≥50 hard negatives
- 当前进度：3% (正例) + 10% (hard negatives)

**硬编码 100% 指标的局限**：
- 规则逻辑为简化概念验证版本
- 夹具为合成数据，非真实小说场景
- 未覆盖边界情况（时间临界、部分信息、条件限定）
- **不能据此宣称生产就绪的召回率/精度**

**后续需要**：
1. 扩充至 100+ 正例，每条规则 10-15 个案例
2. 每个正例配 2-3 个 hard negatives
3. 覆盖真实小说场景的边界情况
4. 接入 CI，跑到召回 ≥70% 且误报 ≤20% 的可接受阈值

### 4. 时间锚点解析限制
**状态**：MVP 保守限制

**当前实现**：
- ✅ ISO-8601 形状绝对时间解析（`2024-01-15`, `2024-01-15T10:30:00`）
- ✅ `temporal_anchor_text` 自身的确定性解析要求
- ✅ 相对锚点证据保留（`temporal_relation`, `temporal_relation_ref`），但不分配 story_order

**保守限制**：
- ⚠️ **不支持**「第 N 天」「N 年后」等自然语言相对表达（`parse_absolute_anchor` 仅接受 ISO 形状）
- ⚠️ **不支持** LLM 辅助的语义理解时间线
- ⚠️ 相对锚点解析链未实现：`relative_to_anchor` 类型的 claim 保留证据但不分配 story_order，
  无法自动推断 `base_anchor` 链或解析相对偏移

**后续需要**：
1. LLM 辅助时间表达规范化
2. 相对锚点解析链实现（`base_anchor` 追溯与偏移计算）
3. 模糊时间跨度的区间表达

### 5. 增量影响集未实现
**状态**：未开始

**需求**：修改一章后，确定哪些 claims 和 issues 受影响，按实体、predicate、时间线区间构造影响集。

**当前行为**：每次扫描加载全部 claims，效率低。

**后续需要**：
1. 按 `entity_id` + `predicate` 索引构建影响集查询
2. 按时间线区间（`story_order` 范围）过滤受影响 claims
3. 只扫描影响集内的 claims 与 issues

### 6. LLM 结构化仲裁未实现
**状态**：未开始

**当前实现**：仅确定性规则前置（alive_conflict, ownership_conflict, knowledge_boundary）。

**架构要求**：规则前置 + LLM 二次仲裁（消歧、补充上下文、置信度）。

**后续需要**：
1. `providers/llm.py` 增加 `arbitrate_conflict` 方法
2. RuleScanner 输出传递给 LLM 仲裁
3. LLM 返回结构化判决与置信度
4. 低置信度 issue 标记为 `needs_review`

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

### 2. 评测数据集规模不足
**描述**：仅 10 个 smoke cases，硬编码 100% 指标。

**风险**：无法代表真实场景的召回率与误报率。

**优先级**：高。扩充至 100+ 正例 + 50+ hard negatives，接入 CI。

### 3. Codex Embedding 持久失败可见性缺失
**描述**：无 dead-letter 表、无 GET 状态端点。

**风险**：任务耗尽重试后，失败对用户不可见。

**优先级**：中。影响运维可观测性，但不阻塞基础功能。

### 4. 增量影响集未实现
**描述**：每次扫描加载全部 claims。

**风险**：性能瓶颈，随 claims 增长扫描耗时线性增长。

**优先级**：中。优化点，不影响正确性。

### 5. LLM 仲裁未实现
**描述**：仅确定性规则，无 LLM 二次判决。

**风险**：误报率可能高于架构要求（≤20%）。

**优先级**：高。架构明确要求 LLM 仲裁。

---

## 下一步优先级

### 立即行动（阻塞生产部署）
1. **真实导出与备份恢复**
   - TXT/Markdown/DOCX/EPUB 全量导出，校验章节数、字数、顺序和 revision
   - 可校验备份包与恢复预检，避免误覆盖现有作品

2. **扩充评测数据集**
   - 扩充至 100+ 正例 + 50+ hard negatives
   - 接入 CI，设定召回率 ≥70%、误报率 ≤20% 的通过阈值
   - 用真实小说场景替换合成案例

### 短期优先级（1-2 周）
1. **实现 Codex Embedding 持久失败可见性**
   - 增加 `codex_backfill_failures` 表
   - 增加 `GET /codex/{project_id}/embedding-status` 端点
   - 更新 `backfill_codex_embeddings_task` 记录耗尽失败

2. **实现 LLM 结构化仲裁**
   - `providers/llm.py` 增加 `arbitrate_conflict` 方法
   - RuleScanner 输出传递给 LLM
   - 低置信度 issue 标记为 `needs_review`

### 中期优先级（3-4 周）
1. **增量影响集优化**
   - 按实体/predicate/时间线区间构造影响集
   - 只扫描受影响 claims

2. **时间锚点 LLM 辅助**
   - 模糊时间表达规范化
   - 自动锚点链推断

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

### 服务层（11 个文件）
- `server/services/codex.py` - 设定库 CRUD
- `server/services/codex_embedding.py` - Embedding 生命周期
- `server/services/outlines.py` - 章纲服务
- `server/services/body.py` - 正文保存
- `server/services/consistency.py` - 一致性服务
- `server/services/rule_scanner.py` - 规则扫描器
- `server/services/timeline.py` - 时间线服务
- `server/services/retrieval.py` - RAG 检索
- `server/services/embedding.py` - Embedding provider
- `server/services/outbox.py` - Outbox 服务
- `server/services/idempotency.py` - 幂等服务

### API 端点（9 个文件）
- `server/api/auth.py` - 认证与授权
- `server/api/projects.py` - 项目管理
- `server/api/chapters.py` - 章节读写
- `server/api/outlines.py` - 章纲管理
- `server/api/codex.py` - 设定库 CRUD
- `server/api/consistency.py` - 一致性状态查询
- `server/api/admin.py` - 系统管理、账号与运行设置
- `server/api/orgs.py` - 工作室成员与作品共享
- `server/main.py` - FastAPI 入口

### 异步任务（3 个文件）
- `server/celery_app.py` - Celery 配置
- `server/tasks/consistency.py` - 一致性任务
- `server/tasks/codex.py` - Codex 回填任务

### 测试（893 passed；另有 36 个真实 PostgreSQL 测试通过）
- `server/tests/` - 单元/功能测试（893 passed）
- `app/src/**/*.spec.ts` - 前端测试（51 passed）
- `server/tests/integration/` - 集成测试（36 passed，需设置真实 PostgreSQL URL）

### 文档（1 个文件）
- `server/docs/IMPLEMENTATION_STATUS.md` - **本文档**（唯一当前事实来源）

---

## 总结

墨枢一致性后端已完成核心数据模型、服务层、API 端点和异步任务定义，893 个单元/功能
测试在 SQLite in-memory + mock providers 环境下通过，另有 36 个集成测试在真实
PostgreSQL + pgvector 环境通过。真实认证、可吊销会话、管理员、工作室 RBAC、作品创建、
分卷章纲与章节插入已经接通，前端 51 个测试与生产构建通过。

**关键限制**：
1. 导出、文风、AI 占比、用量仍有 mock 或静态实现
2. Codex embedding 回填的持久失败可见性尚未实现
3. 评测夹具仅 10 个 smoke cases，硬编码 100% 指标**不代表实际质量**
4. 时间锚点自然语言解析、增量影响集、LLM 仲裁未实现
5. 真实长文本扫描吞吐受上游模型网关稳定性和并发限制影响

当前是“核心一致性能力 + 首轮真实产品流程”，不是功能完整 MVP。生产部署前仍需完成上述产品闭环、
扩充评测集并验证长文本规模下的质量和性能。
