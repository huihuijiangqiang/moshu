# ⚠️ SUPERSEDED - 历史文档，请勿作为当前结论使用

**本文档已过期**。唯一当前事实来源是 [`server/docs/IMPLEMENTATION_STATUS.md`](./IMPLEMENTATION_STATUS.md)。

本文档记录的是早期 P0-A/P0-B 阶段的实现状态与评测指标，**不代表当前后端状态**。
请勿引用本文档中的指标、功能声明或技术决策作为当前结论。

---

# Consistency Backend Implementation Status (历史版本)

## 完成项 (Completed)

### P0-A: 可靠写入边界
- ✅ ChapterOutlineState/Revision 模型与约束
- ✅ OutboxEvent 事务 outbox 模型
- ✅ IdempotencyRecord 幂等记录模型
- ✅ OutlineService 章纲保存服务（含 body_policy 强制约束）
- ✅ OutboxService 事务 outbox 服务
- ✅ IdempotencyService 幂等服务（两阶段：reserve/complete）
- ✅ 正文不变量测试（修改章纲不改正文）
- ✅ Outbox 与幂等性测试

### P0-B: 结构化一致性最小闭环
- ✅ ConsistencyRun, DocumentSummary, ConsistencyClaim, StoryEvent, EntityStateInterval 模型
- ✅ GuardIssueEvidence, GuardResolution 证据与处置模型
- ✅ BodyService 正文保存服务（versioning, content_hash, paragraph ID 验证, CodexRef 重建）
- ✅ ConsistencyService claim 管理与实体解析
- ✅ P0 三条确定性规则实现：
  - 生死冲突检测（alive_conflict）
  - 物品归属冲突检测（ownership_conflict）
  - 知情边界违规检测（knowledge_boundary）
- ✅ 评测夹具（3 个正例 + 5 个 hard negatives + 2 个 easy negatives）
- ✅ 评测 runner 与指标计算（recall, false_positive_rate, hard_negative_precision）
- ✅ Smoke test 通过（recall 100%, FPR 0%, hard negative precision 100%）

## 未完成项 (Not Completed)

### P0-A 剩余
- ❌ Alembic 迁移文件（blocked by baseline models_core.py missing imports）
- ❌ 章纲/正文 API routes（需要修改共享文件 main.py/routes/__init__.py）
- ❌ 真实 409/422 HTTP 响应测试（需要 API 层）

### P0-B 剩余
- ❌ 增量影响集计算（需要确定哪些 claim 受影响）
- ❌ Issue 生命周期管理（open/stale/resolved）
- ❌ Celery task 定义与异步链路（extract -> summarize/scan）
- ❌ 完整的数据库集成测试（blocked by baseline import errors）

### P1 完全未开始
- ❌ trigram/BM25 全文检索候选
- ❌ pgvector 嵌入兜底（需要 embedding provider 接口）
- ❌ LLM 结构化仲裁（需要 LLM provider 接口）
- ❌ 时间线、能力、地理、伏笔规则
- ❌ 章/卷摘要链路
- ❌ 手工重扫 API
- ❌ 死信与可观测性

### P2 完全未开始
- ❌ 项目级 suppression
- ❌ Claim 合并辅助
- ❌ 告警修复候选
- ❌ 跨卷摘要压缩

## 技术债务与已知问题

1. **Baseline blocker**: `server/db/models_core.py` 缺少 `DateTime` 和 `func` 导入，导致所有需要导入 db 模型的测试无法运行。这是已知的基线问题，不在当前任务范围内修复。

2. **缺少 Alembic 配置**: 项目尚无 Alembic 配置和迁移目录，需要初始化。

3. **缺少 content_hash 列**: `ChapterVersion` 模型中 `content_hash` 字段在 models_core.py 中不存在，需要通过迁移添加。

4. **测试隔离**: 当前测试不依赖数据库/Redis/LLM，但完整集成测试需要测试数据库环境。

5. **API 层缺失**: 章纲/正文保存、一致性状态查询、issue 列表/详情、resolution 提交等 API 尚未实现。

6. **异步任务未实现**: Celery worker、outbox dispatcher、任务链路、幂等消费尚未实现。

## 测试覆盖

### 单元测试（不依赖外部服务）
- ✅ Content hash 计算与幂等性
- ✅ Paragraph ID 提取
- ✅ CodexRef 提取
- ✅ Claim fingerprint 计算
- ✅ 规则逻辑概念验证
- ✅ Hard negative 案例

### Smoke test
- ✅ 评测框架可运行
- ✅ 指标计算正确
- ✅ 3 个正例全部检测（recall 100%）
- ✅ 5 个 hard negatives 无误报（precision 100%）

### 集成测试（需要数据库）
- ❌ Blocked by baseline import errors

## 下一步优先级

1. **修复 baseline blocker**（由 Codex 负责，不在 Claude 工作树范围）
2. **初始化 Alembic 并创建迁移**
3. **实现 API 层**（章纲、正文保存、一致性状态查询）
4. **实现 Celery 任务与 outbox dispatcher**
5. **完整数据库集成测试**
6. **实现增量影响集与 issue 生命周期**

## 提交历史

1. `fix: harden consistency persistence primitives` - 修复 P0-A 基础设施的一致性约束
2. `feat: add consistency backend models and services` - 添加 P0-B 核心模型与服务

## 评测结果

```
=== Consistency Rule Evaluation Report ===

Recall: 100.00% (3/3)
False Positive Rate: 0.00%
Hard Negative Precision: 100.00% (5/5)
Easy Negative Precision: 2/2

Positive Cases:
  alive_001: ✓ DETECTED
  ownership_001: ✓ DETECTED
  knowledge_001: ✓ DETECTED

Hard Negatives:
  hn_conditional_ability: ✓ CORRECT
  hn_temporal_change: ✓ CORRECT
  hn_treatment_effect: ✓ CORRECT
  hn_age_progression: ✓ CORRECT
  hn_ownership_transfer: ✓ CORRECT
```

**注意**: 这是 smoke test 结果，使用简化的检测逻辑。实际评测需要完整的数据库环境和规则引擎。
