# 墨枢 · AI 网文创作平台（前端脚手架）

Vue 3 + TypeScript + Vite。开箱即跑，无需后端 —— 所有接口默认走 `src/api/mock`。

```bash
cd app
cp .env.example .env
npm i
npm run dev      # http://localhost:5173
npm test         # 单测
npm run typecheck
```

## 五屏对应

| 路由 | 视图 | 设计稿 |
| --- | --- | --- |
| `/` | `ShelfView` | 屏 06 书架 |
| `/write` | `WorkspaceView` | 屏 01 写作台 + 屏 02 一键成章（同一界面的两个状态） |
| `/outline` | `OutlineView` | 屏 07 大纲 |
| `/codex` | `CodexView` | 屏 03 设定库 |
| `/guard` | `GuardView` | 屏 04 一致性守卫 |
| `/style` | `StyleView` | 屏 08 风格档 |
| `/ai-ratio` | `AiRatioView` | 屏 09 AI 占比自查 |
| `/export` | `ExportView` | 屏 10 导出 |
| `/usage` | `UsageView` | 屏 11 用量与计费 |
| `/wizard` | `WizardView` | 屏 05 开书向导 |
| `/login` | `LoginView` | 屏 12 登录 |

## 目录

```
src/
  api/            接口层。http.ts 只管传输，mock/ 是可丢弃的假数据层
    generation.ts 一键成章：mock 逐字回放 / 真实 SSE，两条路径同一签名
    mock/seed.ts  假数据（《剑起山河》），换成真接口时整个 mock/ 删掉即可
  editor/
    extensions/
      AiDraft.ts            ★ 待采纳草稿容器，产品最核心的交互
      CodexRef.ts           ★ 设定引用节点，存 id 不存文字
      codex-suggestion.ts   @ 唤起的下拉
    use-novel-editor.ts     编辑器组装入口
  stores/         Pinia：project / codex / guard
  composables/    use-autosave.ts —— 三层保险的自动保存
  components/     layout/ 应用外壳，editor/ 编辑器内部件
  views/          四个页面
  styles/
    modernist.css 设计系统令牌与组件层（勿手改，随设计系统更新）
    app.css       只放 modernist 覆盖不到的应用层样式
```

## 三个必须先读的设计决定

**1. 一章一文档。** 80 万字放进单个 ProseMirror 文档会卡死。`listChapters` 不返回正文，`openChapter` 才按需拉；切章用 `setContent` 复用同一编辑器实例，不重挂载。

**2. AI 输出落在 `aiDraft` 节点里，不进正文也不进撤销栈。** 流式增量全部 `tr.setMeta('addToHistory', false)`，用户按一次 Cmd+Z 应该整段退回而不是退回一个字。采纳是 `replaceWith(node.content)` 一次事务。这套行为有单测锁住（`AiDraft.spec.ts`），改动前先跑测试。

**3. 设定引用存 id。** `CodexRef` 节点的 `attrs.id` 指向条目，渲染时从 store 读当前名称 —— 改名自动跟随，且服务端能精确知道本章引用了什么（一致性检查与上下文装配都依赖这份关系）。

## 接后端要改的地方

1. `.env` 里 `VITE_USE_MOCK=false`，`vite.config.ts` 打开 proxy。
2. `src/api/mock/index.ts` 的每个方法换成 `request()` 调用，签名不变，上层不用动。
3. `/api/generate/chapter` 按 SSE 推 `data: {"text":"…"}` / `data: {"node":2}` / `data: [DONE]`，`generation.ts` 里的解析已经写好。
4. 上下文装配（`getContextLayers` 返回的四层）在服务端完成，前端只负责展示预算与上限。

## 尚未实现（有意留白）

- 行内 AI 动作目前只插占位文本，等 `/api/generate/inline`
- 大纲网格的拖拽换序（结构已就位，缺 dnd）
- 登录只做前端校验，无真实鉴权
- 多人协作（Yjs + Hocuspocus）—— MVP 阶段不要引入，它会改写整个持久化模型
- 登录鉴权、支付与积分结算
