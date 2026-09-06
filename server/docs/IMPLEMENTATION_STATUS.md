# 墨枢一致性后端实现状态报告

## 执行摘要

本文档记录墨枢一致性后端在 `worktree-moshu-consistency-backend-v2` 分支的真实实现状态。
所有声明基于实际代码与测试结果，不夸大、不省略已知缺口。

**关键事实**：
- ✅ 41 张表完整 Alembic baseline，增量迁移已到 `022_user_model_configs`
- ✅ 1161 个单元/功能测试通过（SQLite in-memory，mock embedding/LLM）
- ✅ 前端 122 个测试、TypeScript 类型检查和生产构建通过
- ✅ 36 个集成测试已在本机真实 PostgreSQL + pgvector 环境通过
- ✅ 已完成真实账号认证、作品创建、作品归档、分卷与章节增删改排、回收站和章纲编辑闭环
- ✅ Refresh session 持久化轮换、防重放、注销即时吊销，系统管理员与项目 RBAC 已接通
- ✅ 工作室成员管理、角色调整和作品共享已有真实 API 与 UI
- ✅ TXT/Markdown/DOCX/EPUB、分章 ZIP、完整 JSON 备份与非覆盖恢复已接通真实数据库
- ✅ 作者生成已接通真实用量台账、原子额度预留、按实际 token 结算、失败退款和过期预留回收
- ✅ 自动事实抽取、摘要、冲突复核和 embedding 已进入统一平台成本台账，不扣作者积分
- ✅ 风格档已接通用户隔离 CRUD、真实六维抽取、作品绑定、生成提示与用量结算
- ✅ AI 来源账本已接通真实生成 run、段落指纹校验、编辑分类与采纳字数回写
- ✅ AI 生成候选独立持久化，成功、中断和失败输出均可在刷新后恢复、预览、采纳或舍弃
- ✅ 全书查找替换支持本章/本卷/全书范围、逐处预览确认、设定名风险提示、原子提交和整批撤销
- ✅ Docker Compose 已接通 migration、API、前端、PostgreSQL、Redis、Celery worker/dispatcher/beat 与 transactional outbox
- ✅ `/health/ready` 会实际探测 PostgreSQL、Redis 与 Alembic head，并以 503 暴露未就绪依赖
- ✅ 后端镜像内置 tiktoken `cl100k_base` 缓存，API/worker 冷启动不依赖公共网络下载
- ✅ Guard 已接入项目扫描、运行状态、真实告警证据与乐观锁处置
- ✅ 模糊时间作者校对已接通确认/撤销、乐观锁、审计历史、级联 reflow 与下游复检
- ✅ 多剧情线时间板已接通真实 API，支持故事/章节顺序对照、支线泳道、待校对事件和章节跳转
- ✅ 作者计划事件支持新建、编辑、软删除、章节绑定、公历时间与幻想历序号，并与抽取事件合并展示
- ✅ 确定性 Guard 告警已接入有依据的 LLM 二次复核；失败保留规则告警且不自动替作者判误报
- ✅ Codex embedding 回填具有持久任务状态、失败次数、最后错误、耗尽标记与重试入口
- ✅ 设定库页面已接通真实新建、编辑、忽略候选和安全删除；人物档案与通用关键事实分表单维护
- ✅ 设定状态沿革已接通章节锚点、作者增改删、抽取事实合并、乐观锁、项目隔离和生成时的未来状态防泄露
- ✅ 七类确定性规则已由真实 `RuleScanner` 跑过 280 正例、140 hard negatives、20 easy negatives，
  recall / 证据定位 / hard-negative precision 均为 100%
- ⚠️ 上述结构化评测不覆盖正文抽取和 LLM 仲裁的真实盲评质量，不能据此宣称全链路生产就绪
- ⚠️ 本轮 Docker Desktop daemon 未启动，未重复执行 PostgreSQL/Redis 容器验收；文档中的 36 项真实 PostgreSQL 结果来自此前已完成的本机验收

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

### 1. 数据模型（41 张表，100% Alembic 覆盖）

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
`019_chapter_pov.py` 为章节增加作者指定 POV 与独立乐观锁；POV 只能引用同作品已确认人物，
人物被章节用作 POV 时不能直接删除。

#### 设定库 (5 张)
- `codex_entries` - 设定条目（HALFVEC(2048) embedding 列）
- `codex_aliases` - 条目别名
- `codex_refs` - 章节对设定的引用
- `codex_relations` - 设定条目间关系
- `codex_state_changes` - 作者维护的逐章状态变化、软删除状态与乐观版本

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

#### 审稿协作 (2 张)
- `chapter_review_rounds` - 绑定不可变正文版本的章节审稿轮次与决定
- `review_comments` - 绑定提交版本段落 `pid` 的批注、选中文本证据与处理状态

#### 认证与管理 (3 张)
- `auth_sessions` - 可吊销登录会话与 refresh token 轮换状态
- `system_settings` - 注册开关和新账号默认套餐/额度
- `admin_audit_logs` - 管理员修改审计记录

#### 用户模型配置 (1 张)
- `user_model_configs` - 用户级 OpenAI 兼容生成服务、加密 API Key、连接状态与乐观版本

#### 风格/生成/用量/占比 (6 张)
- `style_profiles` - 风格档案
- `generation_runs` - 生成任务记录
- `generation_drafts` - 与正式正文隔离的 AI 生成候选及生命周期
- `usage_logs` - 模型用量日志
- `ratio_reports` - 用量占比报告
- `foreshadows` - 伏笔倒计时

#### 跨章编辑操作 (1 张)
- `text_replacement_runs` - 全书查找替换的范围、版本边界、影响章节与撤销状态

#### 作者人工计划 (1 张)
- `timeline_entries` - 不污染模型抽取事实的可编辑时间事件、章节绑定与乐观版本

**Alembic 与 pgvector 状态**：
- ✅ **代码与迁移已实现**：`001_initial.py` 建立 baseline，
  `002_embedding_halfvec_2048.py` 清理旧向量并迁移到 HALFVEC(2048)，以
  `halfvec_cosine_ops` 重建 HNSW 索引；upgrade/downgrade 均会要求重新回填向量
- ✅ `alembic upgrade head --sql` 与 `alembic downgrade -1 --sql` 语法验证通过
- ✅ 36 个集成测试已在本机真实 PostgreSQL + pgvector 运行通过；此前审核数据库已真实执行至
  `018_timeline_entries`。`019_chapter_pov` 至 `022_user_model_configs` 的 upgrade/downgrade SQL 已生成验证，
  但本轮 Docker daemon 未启动，尚未在真实 PostgreSQL 重复执行；部署后以 readiness 返回的 Alembic head 为准

---

### 2. 核心服务层

#### Codex 设定库服务 (`services/codex.py`, `services/codex_embedding.py`, `services/codex_states.py`)
- ✅ CRUD：create_entry, update_entry, add_alias, remove_alias
- ✅ Unicode NFC 规范化别名匹配
- ✅ 可检索文本变更判据（无变化不重算 embedding）
- ✅ 两段式事务：标脏先提交，网关后补向量
- ✅ 网关失败降级 `deferred`，不阻塞作者写入
- ✅ `refresh_embedding_if_stale` 幂等补向量
- ✅ `queued/running/retrying/succeeded/dead_letter` 状态持久化，记录失败次数、最后错误与耗尽时间
- ✅ 作者可查询当前新鲜/待补条目数，并在耗尽后明确重新入队
- ✅ worker 执行失败与 broker 重新投递次数独立计数；beat 自动回收发布窗口中断的过期 `queued` 作业
- ✅ 人物出场统计按实体 ID 合并正文显式引用、已接受正文抽取事实、作者 POV 与导入 legacy 章节引用
- ✅ 人物档案展示出场/POV 章数、POV 字数、首末出场、断档和逐章可审计来源；章节可直接跳回写作台
- ✅ 作者状态记录绑定同作品有效章节，支持乐观锁更新、软删除和同章同状态项去重
- ✅ 状态沿革合并作者记录与已接受的正文/章纲/处置 claim；模型抽取项只读，作者记录可维护
- ✅ 生成目标章节只注入该章及之前每个状态项的最新作者值，resident/retrieved 两层均阻止未来状态泄露
- ✅ 设定档案提供章节边注式状态时间轴、来源标识、章节跳转和增改删；快速切换条目有请求序列保护
- ⚠️ 未标记为 CodexRef、未被抽取接受且未指定 POV 的纯文本姓名不会计入统计，避免同名与改名造成误报

#### 章纲服务 (`services/outlines.py`)
- ✅ 独立章纲版本控制（与正文解耦）
- ✅ 已有正文时强制选择 `body_policy`：`plan_only` / `mark_body_for_revision`
- ✅ 修改章纲不写正文（不变量测试覆盖）
- ✅ Transactional outbox 事件生成

#### 开书向导 (`api/projects.py`, `services/wizard_planning.py`, `app/src/views/WizardView.vue`)
- ✅ `POST /projects/wizard/plan` 使用平台模型生成严格校验的书名、主角、核心机制、故事总述、卷纲与恰好前三章章纲
- ✅ 规划请求需要登录，模型失败返回可重试的 502；完整 usage 进入平台运营台账，不扣作者积分
- ✅ 创建作品可携带已编辑的前三章章纲，一次性持久化为真实章节；旧客户端无章纲时保留兼容默认章
- ✅ 前端第 3 步展示真实生成状态、失败重试和前三章章纲编辑；本地草稿包含模型结果，创建前仍可继续修改
- ⚠️ 向导只生成前三章章纲，不自动生成正文；正文必须在写作台进入候选草稿流程后由作者审核采纳

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
- ✅ 编辑器可显示多个 AI 候选块，但自动保存会剔除未采纳块；只有显式采纳后才进入正文、版本历史和一致性管道
- ✅ 写作台章节树按固定行高虚拟化，300 章测试下只挂载视口邻近节点；支持当前章自动定位、筛选重置及方向键/Home/End/Enter 导航
- ✅ 写作台右栏可在正文不卸载的前提下查阅本章/全库资料，支持搜索、原地展开、写作约束、当前章有效状态与完整档案跳转

#### AI 生成候选 (`api/generate.py`, `app/src/components/layout/AiSidePanel.vue`)
- ✅ 生成前 Prompt Preview：复用实际生成装配链路，展示模型/档位、技能版本、四层上下文、token 预算与最终 system/user 消息；只读且不调用模型、不预留或扣除额度，响应不包含网关凭据
- ✅ 每次生成在独立 `generation_drafts` 行中保存候选，流式输出每 1000 字符 checkpoint
- ✅ 生成成功、上游失败和浏览器中断均保留已完成文本；失败/中断仍登记真实 run 与来源指纹，但失败退款
- ✅ 列表只返回 160 字摘要，完整候选按需读取；读、采纳和舍弃均要求作品正文编辑权限
- ✅ `streaming -> ready/failed -> accepted/rejected` 状态机，采纳与舍弃加行锁且重复请求幂等
- ✅ 写作台“候选”页支持刷新恢复、逐条预览、放回正文检查和舍弃；服务端不直接改正文，继续沿用正文乐观锁与自动保存
- ✅ 未采纳候选不会进入正文版本、Codex、摘要、RAG、Guard、来源占比或导出
- ✅ 用户可配置自己的 OpenAI 兼容正文生成服务；Prompt Preview 与真实生成复用同一路由，停用/删除后立即回退平台模型
- ✅ 自带 API Key 使用 AES-GCM 加密并绑定用户/配置 ID，API、提示词预览、错误和对象 repr 均不返回明文；密钥轮换使用乐观锁
- ✅ 自定义地址只接受无凭据/查询参数的公网 HTTPS，并在调用前复查 DNS 解析结果；生产部署仍需以网络出口策略防御 DNS rebinding

#### 章节审稿与段落批注 (`api/reviews.py`, `services/reviews.py`, `app/src/components/editor/ReviewPanel.vue`)
- ✅ 作者可提交当前已保存正文版本；同一章节同时只保留一个待审轮次
- ✅ 编辑/主编可在提交快照的稳定段落 `pid` 上添加、修改批注，服务端校验段落与选中文本确实存在
- ✅ 批注保留提交版本、段落摘录和选中文本；作者后续改稿不会让历史意见漂移
- ✅ 开放批注阻止批准；打回必须有批注或明确说明；作者可标记意见已处理后再提交新版本
- ✅ owner/lead/editor 具备审稿权限，writer 可提交正文审稿，viewer 只读；所有写操作使用乐观锁
- ✅ 写作台右栏增加审稿页签，支持轮次状态、版本标记、段落定位、窄屏抽屉和快速切章响应保护
- ✅ TipTap 段落节点持久化 `data-paragraph-id`，重复/缺失锚点在保存前自动修复

#### 全书校订 (`api/text_replacement.py`, `app/src/components/editor/TextReplacementDrawer.vue`)
- ✅ 按本章、本卷或全书检索正文文本节点，最多返回 5000 处结果；回收站章节不参与
- ✅ 每处命中可单独勾选并打开原文段落，替换前显示校样式前后对照
- ✅ 执行前锁定范围内正文并校验预览版本；任一正文变化则整批拒绝，不会只替换一部分
- ✅ 替换保持段落 `pid`、格式节点、AI 来源属性与 CodexRef 结构，写入继续复用正文版本和 outbox 管道
- ✅ 查找内容涉及已确认设定名或别名时必须显式确认；正文替换不会冒充设定库改名
- ✅ 每批操作持久记录受影响章节及前后版本，可原子撤销；章节后续已有修改时拒绝覆盖
- ✅ 打开工具前先排空所有章节的本地待保存队列，离线、保存失败或冲突状态禁止执行

#### 一致性服务 (`services/consistency.py`)
- ✅ Claim fingerprint 计算（规范化主谓宾去重）
- ✅ 实体别名解析（Unicode NFC + 首尾 trim）
- ✅ Claim upsert 幂等（版本绑定，旧版本标 `superseded`）
- ✅ 七类确定性规则实现：
  1. `check_alive_conflict` - 生死冲突（时间线区间重叠）
  2. `check_ownership_conflict` - 物品归属冲突（同时刻不同归属）
  3. `check_knowledge_boundary` - 知情边界违规（先用后知）
  4. `timeline_conflict` - 同一具名事件的可靠时间锚点冲突
  5. `ability_boundary` - 同一时间线内先使用、后获得能力
  6. `location_conflict` - 同一可靠时刻或明确重叠区间的双重地点
  7. `foreshadow_overdue` - 项目进度超过预计回收章且伏笔未回收

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

#### 时间线服务 (`services/timeline.py`, `services/temporal_decisions.py`)
- ✅ `parse_absolute_anchor`：解析 ISO-8601 形状的绝对时间
- ✅ `parse_relative_offset`：确定性解析分钟/小时/时辰/日/周的前后偏移（含中文数字和小数）
- ✅ `assign_story_orders`：从确认的全局锚点分配 story_order，支持当前批次链式引用和跨章节引用
- ✅ `is_globally_anchored`：四条件校验（order_basis, confidence, timeline_id, 可解析值）
- ✅ 时间原文、事件标签、关系、依据与置信度随 claim 持久化；后章可引用前章事件
- ✅ 模糊区间支持作者在解析范围内定点、撤销和再次修改；版本冲突返回当前版本
- ✅ 作者决定由项目锁与 claim 行锁保护，重抽取/reflow 保留，审计历史保留最近 20 次
- ✅ 确认后自动级联计算下游 `story_order`、持久化影响集并排队复检受影响章节
- ✅ 作者确认的精确时间进入生成常驻上下文，并受常驻层剩余 token 预算约束
- ⚠️ **保守限制**：
  - 仅支持 ISO-8601 形状的绝对时间（`2024-01-15`, `2024-01-15T10:30:00`）
  - 支持精确相对表达，并将 `次日`、`过几日`、`一月后`、时段后缀等规范化为可审计区间
  - 模糊区间不会直接生成 story_order，只有作者在确定性区间内确认后才参与硬规则
  - 相对锚点必须同一时间线、精确唯一引用已确认事件、方向与原文一致且置信度至少 0.7；否则保持 NULL
  - 前序锚点后续改写时会自动级联 reflow 已有 claim；若正文证据本身变化，仍需重新抽取该章
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
- ✅ 自动事实抽取、章节摘要、冲突复核、设定写入和后台回填的 embedding 调用写入统一 `usage_logs`
- ✅ 平台事件有唯一 ID，任务重放不会重复入账；多次模型调用与网关重试次数可审计
- ✅ 网关返回 usage 时保存真实 prompt/cached/completion token；缺失时明确标记为估算
- ✅ 平台调用始终为 0 作者积分，作者 `/usage/summary` 主动排除，管理员可查看 1-90 天汇总和明细
- ✅ 用户自带模型不预留或扣除平台积分，但生成 run 与 token 用量仍进入个人台账并明确标记 `user_key`

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

#### 用户模型服务 (`api/model_configs.py`)
- ✅ `GET/PUT/DELETE /account/model-config` - 用户隔离读取、保存/轮换、停用与删除
- ✅ `POST /account/model-config/test` - 只返回分类连接状态，不回显上游响应或密钥
- ✅ 前端 `/model-settings` 提供连接信息、密钥掩码、测试、停用和双击确认删除；写作台预览显示实际模型来源

#### 管理员与协作 (`api/admin.py`, `api/orgs.py`)
- ✅ `GET /admin/overview`、`GET/PATCH /admin/users`、`GET/PATCH /admin/settings`
- ✅ `GET /admin/platform-usage` - 平台模型调用按能力汇总、token 构成和最近明细
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

#### 全书校订 (`api/text_replacement.py`)
- ✅ `POST /projects/{id}/text-replacements/preview` - 按范围返回章节级和逐处命中，不执行写入
- ✅ `POST /projects/{id}/text-replacements` - 幂等、权限保护、预览版本校验后的原子批量替换
- ✅ `POST /projects/{id}/text-replacements/{run_id}/undo` - 仅在所有正文仍位于替换后版本时整批撤销

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

#### 单元测试（1139 passed，SQLite in-memory，mock providers）

**全量测试结果**：1139 passed, 37 skipped（未设置集成测试 URL 时）, 0 warnings；前端 120 passed

主要测试覆盖（不逐文件列举测试数量，以实际 pytest 结果为准）：
- ✅ Codex 设定库：页面与 API 完整 CRUD、引用删除保护、原子别名替换、可检索文本判据、两段式事务、deferred 降级、httpx 错误重试
- ✅ 人物追踪：跨作品隔离、POV 乐观锁、来源去重、删除保护、前端响应防串位和 760px 无横向溢出
- ✅ 章纲服务：独立版本控制、body_policy 约束、不变量测试、outbox 事件
- ✅ 正文服务：content_hash 幂等性、paragraph ID 提取、CodexRef 提取
- ✅ 一致性服务：Claim fingerprint、规则逻辑、hard negative 案例
- ✅ RuleScanner：七类规则检测、timeline-aware 跳过、stale 标记、fingerprint 去重
- ✅ 认证授权：JWT 解码、项目权限、Idempotency-Key 必需性
- ✅ Alembic 迁移：41 张表、pgvector extension、部分唯一索引、downgrade 完整性
- ✅ 时间锚点：ISO-8601、确定性相对时长、跨章事件引用、源锚点与事件标签原文校验
- ✅ 增量影响集：新旧实体重绑定、未解析主体、谓词族闭包、全项目安全降级与扫描遥测
- ✅ LLM 仲裁：不可变版本取证、600 字截断、20 条分批、陌生/缺失/畸形响应、失败降级、事务释放与前端映射
- ✅ 项目、卷、章节、章纲 CRUD，跨卷排序、软删除/恢复/永久删除、乐观锁冲突、Outbox 与幂等性
- ✅ Foreshadow 可选埋设/预计/实际回收章、逾期守卫联动、用量统计、风格档案
- ✅ 风格档跨租户隔离、默认唯一、抽取成功/失败、失败退款、owner 绑定、删除自动解绑与提示隐私
- ✅ AI 来源：真实 run/段落指纹校验、编辑后分类、重复与跨章节伪造防护、采纳字数回落
- ✅ **持久失败契约测试**：`test_documentation_reflects_persistent_dead_letter_visibility`
  守住 embedding 重试耗尽后仍可查询、可重试的任务契约

#### 评测框架 (`tests/consistency/fixtures_eval.py`, `test_eval.py`)
- ✅ 版本化 `rule-eval-v2`：280 正例 + 140 hard negatives + 20 easy negatives
- ✅ 评测会把 claim 与伏笔生命周期写进真实 ORM，调用公开的 `RuleScanner.scan_chapter()`，再读取持久化 issue/evidence；
  不再依据 `expected_conflict` 假算检测结果
- ✅ 指标计算：overall recall、七规则 macro recall、证据 `evidence_recall`、hard/easy-negative precision、额外告警数
- ✅ 当前结果：recall 100% (280/280)、macro recall 100%、证据定位 100% (280/280)、
  hard-negative precision 100% (140/140)、easy-negative precision 100% (20/20)、额外告警 0
- ⚠️ 这是结构化 claim/生命周期层的合成边界评测；尚未覆盖正文到 claim 的抽取误差、
  有授权真实小说盲评和 LLM 仲裁判断质量

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
风格档、AI 来源占比、作者生成用量和平台后台模型台账均已接通真实 API。生产就绪仍受后述
评测规模、LLM 仲裁质量和真实长文本压测限制，不能仅凭页面可用宣称全功能完工。

设定库当前允许作者直接新建和编辑人物/势力/地点/物品/力量体系/伏笔，人物字段按内核、
约束和人物弧维护，其他类型按“标签：内容”的关键事实维护。编辑时保留关系等未开放字段；
待确认抽取候选可直接忽略，已确认且已有正文引用的条目禁止删除，避免丢失显式引用语义。

### 3. 全链路评测覆盖仍不足
**状态**：七条确定性规则的结构化门禁已完成；真实正文盲评未完成

**当前规模**：
- 正例：280 个（七类规则各 40）
- Hard negatives：140 个（每条规则 20 个，覆盖合法状态变化、不同时间线、未知顺序、合法转移与未到期/已回收伏笔）
- Easy negatives：20 个
- **总计：440 个结构化 claim/生命周期案例，真实执行 scanner 并核对持久化证据**

**架构要求**：
- 首版上线集：≥100 正例 + ≥50 hard negatives
- 数量门槛：已达到
- 七类规则覆盖：7/7；真实正文盲评：未达到

**当前 100% 指标的局限**：
- 指标来自真实 scanner 输出，不再硬编码，但输入仍是人工构造的结构化 claim
- 夹具不是正文，无法衡量 LLM 是否能从隐含、否定、转述和长距离上下文中正确抽取 claim
- 尚未对 LLM 仲裁做人工金标盲评
- **不能据此宣称生产就绪的召回率/精度**

**后续需要**：
1. 从有授权的真实小说/项目输出建立正文 -> claim -> issue 的人工金标 dev/holdout
2. 对 LLM 仲裁的 supported / unsupported / uncertain 建立独立混淆矩阵和失败率
3. 把真实 PostgreSQL 评测与 10/30/100 万字性能指标接入 CI/定期任务

### 4. 时间锚点与依赖重算
**状态**：确定性链、模糊区间记录、作者确认和项目级级联重算已完成

**当前实现**：
- ✅ ISO-8601 形状绝对时间解析（`2024-01-15`, `2024-01-15T10:30:00`）
- ✅ `temporal_anchor_text` 自身的确定性解析要求
- ✅ `三日后`、`两小时前`、`半个时辰后`、`次日`、`同日` 等确定性相对偏移
- ✅ 月/年、`过几日` 和日内时段保存上下界，不按单个固定秒数伪精确化
- ✅ 同批次链式引用与跨章节事件引用；事件标签及时间证据持久化到 claim
- ✅ 项目级 reflow API、抽取后的自动 reflow、传递依赖影响集与规则级下游复检
- ✅ Guard 作者校对台显示解析区间，支持小时精度确认、修改和撤销
- ✅ 独立 `MANAGE_TIMELINE` 权限开放给 owner/lead/writer/editor，viewer 保持只读
- ✅ 确认/撤销使用乐观版本、项目锁和 claim 行锁；409 返回当前版本供客户端刷新
- ✅ 重抽取和 reflow 不丢作者决定；决定历史保留最近 20 次且确认结果进入生成提示词
- ✅ 歧义、循环、跨时间线、低置信度、方向冲突和模糊时长保持 `story_order=NULL`
- ✅ 多剧情线只读投影按 `timeline_id` 分泳道，区分已定位、待确认、歧义、循环和未定位状态
- ✅ 前端可切换故事发生顺序/章节编排顺序；待确认事件精确跳转到 Guard 作者校对项
- ✅ 独立 `timeline_entries` 保存作者人工计划，不污染模型抽取 claim；支持无章节事件、乐观锁更新与归档
- ✅ 公历开始时间自动换算故事坐标，幻想历可使用原文时间和自定义序号；时间板合并展示抽取与计划事件
- ✅ 事件编辑抽屉支持新建、修改和移除；密集事件自动分配纵向轨道，移动端无页面级横向溢出

**保守限制**：
- ⚠️ `年关前后` 等需要世界历法或上下文推理的表达仍不猜测
- ⚠️ “年关前后”等无法确定解析区间的表达仍不会进入作者定点流程
- ⚠️ 项目 reflow 只复检已有结构化 claim，不重新调用抽取模型

**后续需要**：
1. 公历/月历网格视图（人工计划已可编辑，但当前仍以故事时间尺展示，不是传统月历）
2. 基于实际长篇分布的时间区间影响集裁剪
3. 真实 PostgreSQL 双事务同时确认的持续集成测试

### 5. 增量影响集
**状态**：首版已完成

**当前行为**：从被修改章节的 accepted + superseded 历史恢复影响键，按 subject/object 实体 ID
召回全项目关联 claim；未解析实体按规范化主体文本和 predicate family 召回，
`uses_knowledge`/`acquires_knowledge` 做闭包。时间引用沿
`temporal_relation_ref -> temporal_event_ref` 做传递闭包。缺少可靠影响键或超过 500 键时
退回全项目扫描，扫描结果返回 `claims_scanned` 与 `scan_scope`。

**后续优化**：加入时间线区间裁剪和基于真实长篇分布的阈值调优；当前安全策略优先避免漏检。

### 6. LLM 结构化仲裁
**状态**：首版已完成

**当前实现**：确定性规则先落告警，随后模型只根据不可变章节快照、claim 锚点和结构化证据给出
`supported` / `unsupported` / `uncertain` 建议。模型失败、畸形或漏项均记为 `failed`，规则告警仍保持开放。

**保守限制**：
1. 模型建议不参与 issue fingerprint、`issue_rev` 或自动处置
2. `unsupported` 不会自动标记误报，最终决定仍由作者提交
3. 真实大规模误报改善程度仍需扩充评测集验证

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
**描述**：七条确定性规则已有 440 个真实 scanner 合成案例，但正文抽取和 LLM 仲裁没有达到真实盲评门禁。

**风险**：结构化 claim 层 100% 不能代表真实正文端到端召回率与误报率。

**优先级**：高。建立有授权真实正文 dev/holdout，不降低现有 280/140 结构化门禁。

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
   - ✅ 七条确定性规则已达到 280 正例 + 140 hard negatives，真实执行 scanner
   - ✅ 已设 overall/macro recall ≥70%、证据定位 ≥90%、hard-negative precision ≥80% 门禁
   - 建立有授权真实小说正文 dev/holdout

### 短期优先级（1-2 周）
1. **实现 Codex Embedding 持久失败可见性**
   - ✅ 增加 `codex_embedding_jobs` 表
   - ✅ 增加 GET 状态与 POST 重新入队端点
   - ✅ 更新 `backfill_codex_embeddings_task` 记录每次尝试和耗尽失败

2. **验证 LLM 结构化仲裁质量**
   - ✅ 规则后置二次复核、结构化结果、失败降级和前端提示已经完成
   - 用扩充评测集统计各 verdict 的准确率、覆盖率与失败率
   - ✅ 自动一致性、摘要、仲裁和 embedding 已纳入后台成本台账

3. **补齐创作工作流 P0 缺口**
   - ✅ 正文版本历史浏览、段落级对比与指定版本恢复已完成
   - ✅ AI 多候选草稿持久化，关闭页面后仍可继续预览、比较与采纳
   - ✅ 全书查找替换，带范围、逐处预览、原子提交、整批撤销和设定名安全检查

### 中期优先级（3-4 周）
1. **增量影响集优化**
   - ✅ 按实体与 predicate family 构造影响集，只扫描相关 claims
   - 加入不会漏检的时间线区间裁剪，并用长文本压测校准 500 键降级阈值

2. **时间锚点 LLM 辅助**
   - ✅ 模糊时间表达确定性规范化：精确表达保留单点，月/年/“过几日”/时段表达保存可审计区间
   - ✅ claim 持久化 `temporal_resolution`，记录规范化结果与依赖状态（resolved/ambiguous/unresolved）
   - ✅ 当前批次与跨章节锚点依赖图诊断，检测歧义引用和循环依赖；模糊区间仍需人工确认后才进入硬规则
   - ✅ 项目级批量 reflow API、抽取后自动级联重算、幂等下游规则复检和 Guard 页面入口
   - ✅ Guard 作者校对台、确认/撤销 API、独立权限、审计历史和生成上下文注入

3. **竞品常见的深度规划与审稿能力**
   - ✅ 多剧情线时间板
   - ✅ 人物出场与视角统计：大纲指定 POV、人物档案汇总和逐章来源轨迹已接通
   - ✅ 设定随章节变化的状态历史：作者记录与已接受抽取事实合并展示，按目标章裁剪后进入生成上下文
   - ✅ 资料与正文并排：右栏支持本章/全库搜索、原地档案、写作约束和当前章有效状态
   - ✅ 批注/审稿流程（章节版本绑定、段落锚点、打回/批准与协作权限）
   - ✅ Prompt Preview（已接入 `/generate/preview` 与写作台预览抽屉）
   - ✅ 用户自带模型配置（加密存储、连接测试、生成路由、零平台积分台账与前端设置页）
   - 关系图、地图、日历、出版排版和平台发布数据属于后续增强，不阻塞核心写作闭环

---

## 文件清单

### 数据模型（12 个文件）
- `server/db/models_core.py` - 核心骨架 6 张表
- `server/db/models_codex.py` - 设定库 5 张表
- `server/db/models_guard.py` - 守卫 2 张表
- `server/db/models_consistency.py` - 一致性基础 4 张表
- `server/db/models_consistency_extended.py` - 一致性扩展 8 张表
- `server/db/models_usage.py` - 风格/生成/用量 5 张表
- `server/db/models_org.py` - 组织 3 张表
- `server/db/models_admin.py` - 会话、运行设置与审计 3 张表
- `server/db/models_editing.py` - 跨章节原子编辑操作 1 张表
- `server/db/models_timeline.py` - 作者人工计划事件 1 张表
- `server/db/models_review.py` - 审稿轮次与段落批注 2 张表
- `server/db/models_model_config.py` - 用户自带模型配置 1 张表

### 服务层（18 个文件）
- `server/services/codex.py` - 设定库 CRUD
- `server/services/codex_embedding.py` - Embedding 生命周期
- `server/services/outlines.py` - 章纲服务
- `server/services/body.py` - 正文保存
- `server/services/consistency.py` - 一致性服务
- `server/services/rule_scanner.py` - 规则扫描器
- `server/services/arbitration.py` - 有依据的 LLM 冲突仲裁与失败降级
- `server/services/timeline.py` - 时间线服务
- `server/services/timeline_entries.py` - 作者人工计划 CRUD、章节归属校验与乐观锁
- `server/services/retrieval.py` - RAG 检索
- `server/services/embedding.py` - Embedding provider
- `server/services/outbox.py` - Outbox 服务
- `server/services/idempotency.py` - 幂等服务
- `server/services/usage.py` - 用量预留、结算、退款与月度额度
- `server/services/style_profiles.py` - 风格样文采样、网关抽取与六维结果校验
- `server/services/text_replacement.py` - 保持正文结构的逐处查找替换
- `server/services/reviews.py` - 章节审稿轮次、段落批注与乐观锁决定
- `server/services/model_configs.py` - 用户模型凭据加密、地址验证、DNS 检查与连接探测

### API 端点（15 个文件）
- `server/api/auth.py` - 认证与授权
- `server/api/projects.py` - 项目管理
- `server/api/chapters.py` - 章节读写
- `server/api/outlines.py` - 章纲管理
- `server/api/codex.py` - 设定库 CRUD
- `server/api/consistency.py` - 一致性状态、时间板与人工计划管理
- `server/api/admin.py` - 系统管理、账号与运行设置
- `server/api/orgs.py` - 工作室成员与作品共享
- `server/api/exports.py` - 全量导出、备份与恢复
- `server/api/usage.py` - 真实余额、聚合和逐笔用量
- `server/api/styles.py` - 风格档 CRUD、抽取与作品绑定
- `server/api/text_replacement.py` - 全书校订预览、执行与整批撤销
- `server/api/reviews.py` - 章节审稿、段落批注、处理和决定
- `server/api/model_configs.py` - 用户级模型配置、测试、停用和删除
- `server/main.py` - FastAPI 入口

### 异步任务（3 个文件）
- `server/celery_app.py` - Celery 配置
- `server/tasks/consistency.py` - 一致性任务
- `server/tasks/codex.py` - Codex 回填任务

### 测试（1139 passed；另有 36 个真实 PostgreSQL 测试通过）
- `server/tests/` - 单元/功能测试（1139 passed）
- `app/src/**/*.spec.ts` - 前端测试（120 passed）
- `server/tests/integration/` - 集成测试（36 passed，需设置真实 PostgreSQL URL）

### 文档（1 个文件）
- `server/docs/IMPLEMENTATION_STATUS.md` - **本文档**（唯一当前事实来源）

---

## 总结

墨枢一致性后端已完成核心数据模型、服务层、API 端点和异步任务定义，1139 个单元/功能
测试在 SQLite in-memory + mock providers 环境下通过，另有 36 个集成测试在真实
PostgreSQL + pgvector 环境通过。真实认证、可吊销会话、管理员、工作室 RBAC、作品创建、
作品归档、分卷与章节生命周期、虚拟化章节导航、章纲、章节 POV、人物出场轨迹、逐章设定状态沿革、资料与正文并排、故事/章节双序时间板、作者人工计划事件、正文版本历史与恢复、AI 多候选草稿、全书查找替换、章节审稿与段落批注、全量导出、非覆盖备份恢复、风格指纹、AI 来源账本、作者生成用量与平台模型成本台账已经接通，前端 120 个测试与生产构建通过。

**关键限制**：
1. 七条确定性规则的 280/140 结构化评测门禁、模糊区间人工确认和多剧情线时间板已完成，但真实正文盲评仍需补充
2. 当前 100% 指标来自真实 scanner 而非硬编码，但输入仍是合成 claim，**不代表正文抽取和 LLM 仲裁的端到端质量**
3. 模糊时间已完成规范化区间、作者确认、项目级 reflow 与依赖复检；影响集时间区间裁剪仍未实现，仲裁只提供建议，不自动处置
4. 真实长文本扫描吞吐受上游模型网关稳定性和并发限制影响
5. 人物出场统计只接受实体 ID 可追溯来源，不对正文姓名做模糊全文计数；本轮 Docker daemon 未启动，`019`/`020` 尚未重复执行真实 PostgreSQL 验收

当前是“核心一致性能力 + 首轮真实产品流程”，不是功能完整 MVP。生产部署前仍需完成上述产品闭环、
扩充评测集并验证长文本规模下的质量和性能。
