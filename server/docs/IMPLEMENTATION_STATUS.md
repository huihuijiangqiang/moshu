# 墨枢一致性后端实现状态报告

## 执行摘要

本文档记录墨枢一致性后端在 `worktree-moshu-consistency-backend-v2` 分支的真实实现状态。
所有声明基于实际代码与测试结果，不夸大、不省略已知缺口。

**关键事实**：
- ✅ 30 张表完整 Alembic baseline，pgvector extension/HALFVEC 列已在迁移中定义
- ✅ 809 个单元/功能测试通过（SQLite in-memory，mock embedding/LLM）
- ⚠️ 36 个集成测试全部 SKIP（本机无真实 PostgreSQL + pgvector，**未在真实数据库验证**）
- ⚠️ Codex embedding 回填的持久失败可见性尚未实现
- ⚠️ 评测夹具仅 10 个 smoke cases，硬编码 100% 指标不代表实际质量

---

## 已完成模块

### 1. 数据模型（30 张表，100% Alembic 覆盖）

#### 核心骨架 (6 张)
- `users` - 用户账号
- `projects` - 项目
- `volumes` - 卷
- `chapters` - 章节元信息
- `chapter_bodies` - 章节正文（独立存储）
- `chapter_versions` - 章节版本历史

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

#### 组织与协作 (3 张，MVP 建表不开功能)
- `orgs` - 组织
- `org_members` - 组织成员
- `chapter_assignments` - 章节分工

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
- ⚠️ **真实 PostgreSQL + pgvector 未验证**：36 个集成测试因本机无真实数据库而跳过，
  pgvector `<=>` 余弦距离、部分唯一索引的并发去重、HNSW 索引性能等**未在真实环境验证**

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
- ⚠️ **真实 PostgreSQL + pgvector 未验证**：余弦距离召回准确性、HNSW 索引性能未在真实环境测试

---

### 3. API 端点（全部需认证 + 项目权限）

#### 认证 (`api/auth.py`)
- ✅ JWT bearer 认证
- ✅ `get_current_user`：解码 token，查库验证
- ✅ `verify_project_access`：owner 或 org 成员校验
- ✅ `ProjectAccessChecker` 依赖类

#### 项目 (`api/projects.py`)
- ✅ `GET /projects/{id}` - 项目详情
- ✅ `GET /projects/{id}/chapters` - 章节列表（不含正文）

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
- ✅ 任务路由配置
- ✅ 结果过期与序列化配置

---

### 5. 测试覆盖

#### 单元测试（809 passed，SQLite in-memory，mock providers）

**全量测试结果**：809 passed, 36 skipped, 4 warnings

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

#### 集成测试（36 tests，全部 SKIP）
- ⚠️ `tests/integration/` 下 36 个测试**在本地从未运行过**
- ⚠️ 需要真实 PostgreSQL + pgvector（设置 `TEST_POSTGRES_URL` 后才会运行）
- 覆盖内容（未验证）：
  - 部分唯一索引（`postgresql_where`）的并发 upsert 去重
  - pgvector `<=>` 余弦距离与 HNSW 索引
  - `INSERT ... ON CONFLICT` upsert 语义
  - GIN 索引
  - CHECK 约束在并发/边界数据下的真实拒绝行为
- **不能宣称这些测试通过** —— 它们从未在真实数据库上跑过

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

### 2. 集成测试未验证
**状态**：全部 SKIP

36 个集成测试因本机无真实 PostgreSQL + pgvector 而跳过，以下能力**未在真实数据库验证**：
- 部分唯一索引的并发 upsert 去重
- pgvector 余弦距离召回准确性
- PostgreSQL-specific upsert 语义
- GIN 索引性能
- CHECK 约束在边界数据下的拒绝行为

**风险**：SQLite 单元测试与真实 PostgreSQL 行为可能存在差异，生产部署前需补全集成测试验证。

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

### 1. PostgreSQL 集成测试从未运行
**描述**：36 个 `tests/integration/` 测试因本机无真实数据库而跳过。

**风险**：SQLite 单元测试无法验证 pgvector、部分唯一索引、PostgreSQL-specific upsert 等行为。

**优先级**：高。生产部署前必须在真实 PostgreSQL + pgvector 上跑通集成测试。

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
1. **补全集成测试验证**
   - 搭建本地 PostgreSQL + pgvector 测试环境
   - 设置 `TEST_POSTGRES_URL` 跑通 36 个集成测试
   - 修复发现的 SQLite/PostgreSQL 行为差异

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

### API 端点（7 个文件）
- `server/api/auth.py` - 认证与授权
- `server/api/projects.py` - 项目管理
- `server/api/chapters.py` - 章节读写
- `server/api/outlines.py` - 章纲管理
- `server/api/codex.py` - 设定库 CRUD
- `server/api/consistency.py` - 一致性状态查询
- `server/main.py` - FastAPI 入口

### 异步任务（3 个文件）
- `server/celery_app.py` - Celery 配置
- `server/tasks/consistency.py` - 一致性任务
- `server/tasks/codex.py` - Codex 回填任务

### 测试（809 passed, 36 skipped）
- `server/tests/` - 单元/功能测试（809 passed）
- `server/tests/integration/` - 集成测试（36 skipped，需真实 PostgreSQL）

### 文档（1 个文件）
- `server/docs/IMPLEMENTATION_STATUS.md` - **本文档**（唯一当前事实来源）

---

## 总结

墨枢一致性后端已完成核心数据模型、服务层、API 端点和异步任务的实现，809 个单元/功能
测试在 SQLite in-memory + mock providers 环境下通过。30 张表完整 Alembic baseline，
pgvector extension 与 Vector 列已在迁移中定义，代码质量经 ruff 验证。

**关键限制**：
1. 36 个集成测试因本机无真实 PostgreSQL + pgvector 而跳过，**不能宣称这些测试通过**，
   pgvector 余弦距离、部分唯一索引等**未在真实数据库验证**
2. Codex embedding 回填的持久失败可见性尚未实现，无 dead-letter 表与 GET 状态端点
3. 评测夹具仅 10 个 smoke cases，硬编码 100% 指标**不代表实际质量**
4. 时间锚点仅支持 ISO-8601 绝对时间，**不支持**「第 N 天」「N 年后」等自然语言表达
5. 增量影响集、LLM 仲裁未实现

生产部署前**必须**补全集成测试验证、扩充评测数据集、实现 LLM 仲裁。当前状态为功能完整
的 MVP，但距离生产就绪的质量标准（召回率 ≥70%、误报率 ≤20%）仍有差距。
