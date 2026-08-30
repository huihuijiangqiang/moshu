# 小说长文本一致性后端架构

状态：提案（P0 实现前冻结核心契约）

适用范围：章节大纲持续修改、章节保存、长期记忆、一致性守卫

产品指标：Guard 召回率 >= 70%，误报率 <= 20%，证据定位准确率 >= 90%

## 1. 目标与边界

墨枢不是把整本小说塞进一次模型调用，而是把可确认的设定、正文中出现的事实、故事时间和证据位置保存为可增量维护的数据。模型只负责抽取候选事实和处理规则无法判定的歧义；作者始终拥有最终解释权。

必须满足：

- 80 万字作品仍按固定上下文预算生成，不能依赖不断扩大的 prompt。
- 修改一章后只重算受影响的数据和告警，并能识别“修改早期章节影响后文”的情况。
- 每条告警至少包含两端证据或一端证据加一条明确设定；无证据不展示。
- 正文、章纲、抽取结果、告警均绑定明确版本，异步任务不得把旧结果写回新版本。
- 所有设定更新、冲突处置和正文修改均需作者确认。

明确不做：

- 不采用整本书全文 prompt、纯向量 RAG 或只靠长上下文模型。
- 不在 MVP 引入 Neo4j、GraphRAG 全套索引或通用 agent memory。
- 不允许模型自行修改“长期记忆”或把抽取候选静默升级成作者设定。
- 不允许大纲变更自动重写、覆盖或清空正文。

## 2. 外部方案取舍

| 方案 | 借鉴 | 不采用 |
| --- | --- | --- |
| Microsoft GraphRAG | entity / relation / claim 抽取，多粒度摘要，离线可重建索引 | 社区发现和全局图查询成本高，偏静态语料，不适合作为每次保存后的主链路 |
| Graphiti | episode 增量摄入、事实失效、双时态思想、全文/向量/图的混合召回 | MVP 不引入独立图数据库；小说的章节顺序与故事时间用 PostgreSQL 足够表达 |
| Letta / MemGPT | 核心记忆与历史记录分层、所有状态持久化 | agent 自行编辑核心记忆会越过作者确认，不能成为权威事实来源 |
| SillyTavern World Info | 别名/关键词精确触发、递归关联、独立条目、上下文预算与优先级 | 关键词注入只改善生成，不等于事实校验；概率触发不用于一致性守卫 |
| Lost in the Middle | 相关证据应结构化检索并放在清晰位置 | 不能因为模型支持长上下文就取消检索、摘要和证据排序 |

结论：使用 PostgreSQL 的结构化事实与时态状态作为真相层，全文和向量只负责找候选，LLM 只仲裁不确定候选。

## 3. 权威层级

同一事实冲突时按以下优先级解释，但仍保留全部来源：

1. 作者确认的设定库事实或作者对告警的明确处置。
2. 已采纳正文中的直接陈述。
3. 作者确认的章纲计划。
4. 模型从正文抽取、尚未确认的候选事实。
5. 模型生成的摘要。

摘要永远不是权威证据。章纲表达“计划发生什么”，正文表达“实际写了什么”，两者不能合并成同一版本号或同一事实状态。

## 4. 数据模型

### 4.1 章节计划与正文版本

#### `chapter_outline_revisions`

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | bigint | 主键 |
| `chapter_id` | varchar(32) | 外键，索引 |
| `revision` | int | 从 1 递增，`unique(chapter_id, revision)` |
| `title` | varchar(200) | 保存后的章名 |
| `nodes` | jsonb | 字符串数组；服务端 trim 并移除空节点 |
| `note` | text | 章纲补充 |
| `body_policy` | varchar(32) | `plan_only` / `mark_body_for_revision` |
| `body_rev_at_change` | int nullable | 做出选择时的正文版本 |
| `created_by` | varchar(32) | 作者 id |
| `created_at` | timestamptz | 服务端时间 |

`chapters` 增加：

- `outline_note text not null default ''`
- `outline_revision int not null default 0`
- `body_needs_revision bool not null default false`
- `body_revision_marked_outline_rev int nullable`
- `body_revision_marked_body_rev int nullable`

`chapter_bodies.rev` 只表示正文版本。`chapter_versions` 继续保存正文快照，并增加 `content_hash` 以便幂等判断。正文中的段落必须具有稳定 `pid`。

### 4.2 摄入运行与摘要

#### `consistency_runs`

记录一次指定章节正文版本的处理状态：`id`、`project_id`、`chapter_id`、`body_rev`、`pipeline_version`、`status`、`trigger`、`started_at`、`finished_at`、`error_code`。唯一键为 `(chapter_id, body_rev, pipeline_version)`。

#### `document_summaries`

版本化保存 `chapter` / `volume` 摘要：`owner_id`、`source_rev`、`summary_version`、`content`、`covered_chapter_from/to`、`model_id`、`created_at`。生成上下文只能读取与当前正文版本匹配的最新成功摘要；摘要失败时允许使用旧摘要，但必须标记 stale。

### 4.3 Claim、事件和状态

#### `consistency_claims`

每一条 claim 是“某来源声称某事实”，而不是直接覆盖设定库：

| 字段 | 说明 |
| --- | --- |
| `subject_entry_id` | 已解析实体；无法解析时为空并保留 `subject_text` |
| `predicate` | 受控键，如 `location`、`alive`、`owns`、`knows`、`ability_level` |
| `object_type` / `object_value` / `object_entry_id` | 标量或另一实体 |
| `polarity` | 肯定或否定 |
| `certainty` | `explicit` / `inferred` / `uncertain` |
| `source_kind` | `codex` / `body` / `outline` / `resolution` |
| `chapter_id` / `body_rev` / `outline_rev` | 来源版本 |
| `timeline_id` / `story_order` | 可空的故事时间线与其中的稀疏排序值，不等同于章节序号 |
| `valid_from_order` / `valid_to_order` | 同一故事时间线内的事实有效区间 |
| `extractor_version` / `confidence` | 可重跑、可校准 |
| `fingerprint` | 规范化 claim 哈希，防重复 |
| `status` | `candidate` / `accepted` / `rejected` / `superseded` |

唯一键建议为 `(source_kind, chapter_id, source_rev, fingerprint, extractor_version)`。旧正文版本的 claim 不删除，标为 superseded，确保告警可追溯。

#### `story_events`

保存事件的故事时间和叙事位置：`event_type`、`timeline_id`、`story_order numeric(24,8)`、`time_text`、`time_start/end`、`chapter_id`、`body_rev`、`anchor`、`confidence`。倒叙通过故事顺序和章节 `idx` 的差异表达，不能用章节 `idx` 代替故事时间。

`story_order` 是故事世界中的事件顺序，不是“第几章”。插章、拆章或移动章节不会重排已有事件。时间线服务在两个已确认事件之间用稀疏中点分配顺序值；空间不足时只重排同一 `timeline_id` 的事件和状态区间，并在单事务内完成。无法从正文可靠确定顺序时保持为空，只进入待确认列表，不运行依赖时序的硬规则。多线叙事先按 `timeline_id` 隔离，只有存在已确认的跨线锚点时才比较。

#### `entity_state_intervals`

保存可做 SQL 校验的状态区间：`entry_id`、`state_key`、`value_json`、`valid_from_order`、`valid_to_order`、`source_claim_id`、`status`。同一实体同一 state key 的两个 accepted 区间重叠且值互斥时产生候选冲突，不直接覆盖旧状态。

### 4.4 证据、告警与处置

`guard_issues` 增加 `run_id`、`rule_id`、`rule_version`、`fingerprint`、`status`、`confidence`、`issue_rev`、`stale_at`。同一项目同一 fingerprint 只允许一个 open issue。

新增 `guard_issue_evidence`：

- `issue_id`、`side`（expected/actual/context）。
- `source_kind`、`chapter_id`、`body_rev`、`outline_rev`、`codex_entry_id`。
- `paragraph_id`、`start_offset`、`end_offset`、`offset_encoding`、`quote`、`quote_hash`、`anchor_version`。
- `claim_id` 和 `sort_order`。

证据锚点以对应 `body_rev` 的不可变正文快照为基准。`paragraph_id` 在单个 ProseMirror 文档内唯一且跨普通编辑保持稳定；offset 使用 UTF-16 code unit，与浏览器和 ProseMirror 的字符串坐标一致。`quote_hash` 固定为 `SHA-256(UTF-8(NFC(quote)))`，不折叠空白、不替换标点，避免不同原文被误认成同一证据。当前正文 rev 改变时，历史证据仍可在原快照查看，但相对当前正文标为 stale；只有通过重新扫描产生的新 evidence 才能指向新 rev。`anchor_version` 用于未来升级纯文本提取和坐标算法。

新增 `guard_resolutions`：记录 `issue_id`、`issue_rev`、`action`、`note`、`created_by`、`created_at`。允许动作：`accept_old_fact`、`accept_new_fact`、`intentional_exception`、`false_positive`、`fixed_in_body`、`defer`。任何动作都只更新结构化状态或生成待审修改建议，不直接改正文。

## 5. 核心不变量：修改章纲绝不修改正文

API 使用枚举 `body_policy`，不能继续使用含义不清的布尔值：

- 无正文时允许省略，服务端归一为 `plan_only`。
- 已有正文且计划发生实质变化时必须显式提交 `plan_only` 或 `mark_body_for_revision`。
- `plan_only` 只新增 outline revision 并更新章节的计划字段；不得写 `chapter_bodies`、`chapter_versions`、正文 `rev`、正文内容或正文状态。
- `mark_body_for_revision` 同样不得改正文，只把 `body_needs_revision` 设为 true，并记录当时的 outline rev 与 body rev。
- 如果 `body_needs_revision` 已经为 true，后续 `plan_only` 不得隐式清除它。
- 清除标记必须走独立的作者确认接口，并带 `base_body_rev` 与 `addressed_outline_revision`；保存正文不自动清除标记。

实现上由 service 层单事务保证，并用测试断言保存前后正文行的内容、rev、更新时间和版本数完全不变。

## 6. 事务与异步时序

### 6.1 保存章纲

1. `SELECT chapter FOR UPDATE`，校验项目权限和 `base_outline_revision`。
2. 对 title/nodes/note 做规范化并判断实质变化；无变化返回当前版本，不制造快照。
3. 若有正文且实质变化但缺少 `body_policy`，返回 `422 BODY_POLICY_REQUIRED`。
4. 插入 `chapter_outline_revisions`，更新章节计划字段和 revision。
5. 按上节不变量处理标记；事务提交。
6. 若计划实体发生变化，写 outbox 事件用于刷新生成检索索引；不触发正文抽取，不自动扫描正文冲突。

乐观锁冲突返回 HTTP 409，包含服务端最新计划、revision 和客户端提交内容，前端必须展示双版本。

### 6.2 保存正文

1. `SELECT chapter_body FOR UPDATE` 并严格要求 `base_rev == current_rev`；小于或大于都返回 409。
2. 校验 ProseMirror JSON，确保可定位段落有稳定 `pid`；生成 `content_hash`。
3. 同一 `Idempotency-Key` 加相同 hash 直接返回原结果；key 相同但 hash 不同返回 409。
4. 在同一事务中写 `chapter_versions`、更新 `chapter_bodies` 和字数、全量重建显式 `codex_refs`。
5. 写 transactional outbox：`chapter.body_saved:{chapter_id}:{new_rev}`，然后提交。
6. outbox dispatcher 投递异步任务；HTTP 响应返回新 rev 和 `consistency_status=queued`。

不能在数据库 commit 前投递 Celery，否则 worker 可能读不到新正文；也不能只在 commit 后直接投递，否则进程崩溃会永久漏任务。

多实例 dispatcher 使用 `FOR UPDATE SKIP LOCKED` 批量领取事件，并在短事务内写入 `dispatching`、`lease_owner`、`lease_until`；网络投递不持有数据库锁。发送成功后凭 lease token 更新为 `sent`，租约超时可重新领取。进程在“任务已发送、sent 尚未落库”之间崩溃仍会导致重复投递，因此消费者的数据库幂等键才是最终保障，dispatcher 锁不能代替消费者幂等。

### 6.3 增量一致性流水线

队列分离：

- `consistency.extract`：实体解析、claim/event/state 抽取，LLM 配额受控。
- `consistency.summarize`：章摘要与卷摘要。
- `consistency.scan`：确定性规则、候选检索和 LLM 仲裁。
- `consistency.maintenance`：outbox 重试、全量重扫、旧快照清理。

任务链：

1. `extract_chapter(chapter_id, body_rev, pipeline_version)` 获取或创建 consistency run。
2. 先解析显式 CodexRef 和别名，再抽取未覆盖的实体、claim、事件；所有结果绑定 body rev。
3. 原子替换“该 body rev + extractor version”的候选集合，旧版本标为 superseded。
4. 成功后分别投递 `summarize_chapter` 和 `scan_impacted_claims`；扫描只依赖抽取，不等待摘要。
5. 扫描任务根据实体、predicate、故事时间区间和章节顺序构造影响集，先跑 SQL 规则，再对少量不确定候选做 LLM 仲裁。
6. upsert issue 与 evidence；上一 body rev 产生但新版本不再复现的 issue 标为 stale，不删除。

修改早期章节时，影响集包含：当前章、同实体/同 predicate 的后续 claim、依赖当前摘要的卷摘要，以及仍 open 的相关 issue。无实体可解析或 pipeline 版本升级时降级为本卷扫描；全书扫描只用于手工操作、评测或低峰维护。

## 7. 检索与判定分层

严格按成本与确定性从低到高执行：

1. **显式引用与精确别名**：读取 ProseMirror CodexRef，中文别名做 Unicode/空白规范化后等值命中。
2. **全文候选**：别名子串、BM25/trigram 找拼写变体；只能召回候选，不能直接判冲突。
3. **SQL 时态规则**：人物生死、物品归属、地点、能力等级、知情边界、旅行耗时、伏笔期限等受控 predicate。
4. **向量兜底**：只对未命中的句段做 top-k，必须按项目、实体类型、章节范围过滤；向量相似不能作为冲突证据。
5. **LLM 仲裁**：输入两个 claim、必要上下文和规则定义，输出结构化 `conflict / no_conflict / uncertain`。`uncertain` 不打高等级告警。

生成上下文与 Guard 共用实体/claim 存储，但排序目标不同：生成侧追求“相关且有用”，Guard 侧追求“可证实且可复现”。两者不能共用一个未经区分的 top-k 结果。

`confidence` 不使用跨规则统一的固定阈值。每个 extractor/rule/model 组合保存 `decision_policy_version`，阈值只根据版本化 dev 集校准并在 holdout 验证。作者确认事实固定为最高可信来源；低于出警阈值的模型候选只进入待确认，不生成 Guard issue；任一输入 claim 为 uncertain 时不得产生 high severity。线上反馈进入待审核样本池，不能自动改变阈值。

## 8. 幂等、并发与失败恢复

- API 幂等键：`user_id + route + Idempotency-Key`，保存请求 hash 和响应，保留 24 小时。
- outbox 唯一键：`topic + aggregate_id + aggregate_rev`。
- 抽取任务键：`extract:{chapter_id}:{body_rev}:{pipeline_version}`。
- 摘要任务键：`summary:{chapter_id}:{body_rev}:{summary_version}`。
- 扫描任务键：`scan:{chapter_id}:{body_rev}:{rule_set_version}`。
- issue fingerprint：`project + rule + normalized claim ids + rule version`。

worker 使用“至少一次投递 + 数据库幂等写”，不假设 Celery exactly-once。任务开始和结束都检查当前正文 rev；旧 rev 可以完成历史记录，但不得更新章节的 current summary、current run 或当前 open issue。失败采用指数退避和最大重试，永久失败进入 dead-letter 状态并在项目状态接口可见。锁只覆盖单章短事务，不在调用模型时持有数据库锁。

## 9. API 契约

### 9.1 章纲

`PUT /chapters/{chapter_id}/outline`

```json
{
  "title": "雪夜叩关",
  "nodes": ["北狄夜袭", "沈砚守关"],
  "note": "保留敌军退去的疑点",
  "base_outline_revision": 3,
  "body_policy": "mark_body_for_revision"
}
```

响应包含 `outline_revision`、`outline_updated_at`、`body_needs_revision` 和标记关联的正文版本。冲突返回 409；已有正文但未选择策略返回 422。

- `GET /chapters/{id}/outline/revisions?cursor=`：章纲历史。
- `POST /chapters/{id}/body-revision/resolve`：作者确认已处理正文标记，要求 `base_body_rev`、`addressed_outline_revision`。
- `POST /volumes/{id}/chapters`：插章，使用 `after_chapter_id` 和请求幂等键；服务端维护稀疏 idx。

### 9.2 正文与一致性

- `PUT /chapters/{id}/body`：保留 `base_rev`，补充 `Idempotency-Key`；冲突必须返回真实 HTTP 409。
- `GET /chapters/{id}/consistency-status`：返回各 body rev 的抽取、摘要、扫描状态。
- `POST /projects/{id}/guard-scans`：手工触发本卷或全书扫描，返回 task id。
- `GET /projects/{id}/guard/issues`：按 status、severity、type、chapter、entry 分页。
- `GET /guard/issues/{id}`：完整证据、来源版本和可用动作。
- `POST /guard/issues/{id}/resolutions`：带 `base_issue_rev` 提交处置，冲突返回 409。

所有错误使用稳定 `code`：`OUTLINE_REVISION_CONFLICT`、`BODY_REVISION_CONFLICT`、`BODY_POLICY_REQUIRED`、`STALE_SOURCE_REVISION`、`ISSUE_REVISION_CONFLICT`。

## 10. 规则集 P0 范围

P0 不追求一次覆盖所有文学歧义，先实现可评测的七类规则：

1. 人物硬属性：姓名/别名、年龄区间、外貌硬特征、生死。
2. 物品状态：归属、损毁、消耗、当前位置。
3. 时间线：先后关系、相对日期、持续时间。
4. 信息知情边界：某角色何时获知某事实。
5. 能力规则：等级、前置条件、代价和已知上限。
6. 地理距离：地点连接和最短合理耗时。
7. 伏笔：埋设、预计窗口、已回收、逾期。

软性格、语气风格和“剧情是否合理”先作为 P2 建议，不混入一致性指标，避免误报失控。

## 11. 评测与上线门槛

建立版本化 JSONL/数据库夹具，样本必须包含正文版本、设定、期望 issue type、证据 paragraph id 和 hard negative。20 个最明确矛盾只做流水线 smoke test，用于证明任务可运行和结果可定位，不设置上线结论。首版上线集至少 100 个正例、50 个 hard negative，七类规则每类均有覆盖。

Hard negative 是“共享实体和相似表述，但因条件、时间、视角或状态演变而不冲突”的配对，例如“不擅长用剑”与“危急时勉强拔剑”、“四月开始融雪”与“四月底山阴仍有残雪”、“左肩旧伤遇寒痛”与“服药后症状减轻”。它们必须和正例走同一候选召回路径，才能真实衡量误报；明显无关文本只能作为 easy negative，单独报告，不能稀释误报率。

指标定义：

- 召回率：正确命中的金标冲突数 / 全部金标冲突数；报告 overall 与七类 macro recall，overall >= 70%。
- 误报率：被标为 false positive 的告警 / 全部已判定告警；离线 hard-negative precision 同时报告，线上滚动 30 天 <= 20%。
- 证据定位准确率：issue 的 expected/actual 两端都命中金标 paragraph，且 quote hash 与该版本正文一致；>= 90%。
- 另报 p95 扫描延迟、每章 LLM token、`uncertain` 比例和 stale issue 比例，防止用成本换指标。

数据集分 train/dev/holdout，规则阈值只在 dev 调整。rule/extractor/prompt 版本变更必须跑 holdout；任一核心指标回退超过 3 个百分点阻止合并。线上作者处置只进入待审核样本池，不能直接污染金标。

## 12. 分阶段实施与工作树拆分

### P0-A：可靠写入边界

- 迁移：outline revision、chapter 标记、outbox、idempotency record。
- Outline service/API、真实 409/422、正文不变量测试。
- Body save outbox、稳定 paragraph id 校验、任务状态查询。

### P0-B：结构化一致性最小闭环

- consistency run、claim、event、state、evidence、resolution 表。
- exact alias/CodexRef 解析、三条确定性规则（生死、物品归属、知情边界）。
- 增量影响集、issue 生命周期、评测 runner 和首批夹具。

### P1：混合召回与完整七类规则

- trigram/BM25、pgvector 兜底、LLM 结构化仲裁。
- 时间、能力、地理、伏笔规则；章/卷摘要链路。
- 手工本卷/全书重扫、死信与运行可观测性。

### P2：体验与成本优化

- 基于作者处置的项目级 suppression，但必须可见可撤销。
- claim 合并辅助、告警修复候选、跨卷摘要压缩。
- 是否引入图存储以真实查询与性能数据决定，不预先绑定。

工作树边界：

- 主工作区保留现有未提交改动，不直接开发本阶段功能；最终合并前先单独处置这些改动。
- Claude 固定复用会话 `85240d32-a3ec-4012-8787-ace96fc115df`，实际绑定工作树 `.claude/worktrees/moshu-consistency-backend`；该分支已快进到架构提交。Claude 只负责新增 consistency 模型、outbox/幂等基础设施、纯规则与测试夹具。
- Codex 使用 `D:/moshu-worktrees/codex-consistency`，负责 API/service 契约、集成测试和最终集成。
- 仅新增文件可以直接并行；运行时接线必然需要修改 `main.py`、`db/__init__.py` 等共享文件，应由 Codex 在 Claude 提交完成后单线完成。通过迁移给已有表加字段并不等于 ORM 自动获得这些属性，不能把“零修改已有 Python 文件”误称为可运行集成。
- 两边不得同时修改 `memory/assembler.py`、`config.py`、`db/models_*.py` 或同一迁移；每个提交只包含约定边界内文件。

## 13. 验收清单

- 已有正文的章纲修改必须选择策略；两种策略都不改变任一正文字节或正文 rev。
- 并发保存章纲/正文均返回可恢复的 409，不静默覆盖。
- commit 后崩溃不会漏掉扫描任务；重复投递不产生重复 claim 或 issue。
- 旧 worker 结果不能覆盖新正文版本的摘要和告警。
- 每个 open issue 均能跳到确切版本、paragraph 和 quote；来源变化后标 stale。
- P0 测试集达到既定三项指标后才接入默认 Guard 流程。

## 14. 参考资料

- Microsoft GraphRAG Indexing Overview: <https://microsoft.github.io/graphrag/index/overview/>
- Graphiti Overview: <https://help.getzep.com/graphiti/getting-started/overview>
- Letta Stateful Agents and Memory: <https://docs.letta.com/guides/agents/memory>
- SillyTavern World Info: <https://docs.sillytavern.app/usage/core-concepts/worldinfo/>
- Lost in the Middle: <https://arxiv.org/abs/2307.03172>
- GraphRAG paper: <https://arxiv.org/abs/2404.16130>
- GRAG: <https://arxiv.org/abs/2405.16506>
