repo: huihuijiangqiang/moshu
branch: main
visibility: private
path: .

## Last sync

date: 2026-08-29

### Updated in this project

- 初始化本地 Git 工作区并关联私有仓库 `huihuijiangqiang/moshu`
- 在 `app/` 下生成 Vue 3 + TS + Vite 全量脚手架，覆盖全部十二屏（41 个文件，含单测）
- 在 `server/` 下建立 FastAPI、SQLAlchemy 与四层记忆装配器骨架
- 约定：组件名 PascalCase、目录名 kebab-case、Vitest 单测
- 补齐根级 README、忽略规则与统一换行规则

## Screen map

| 项目内屏幕 | 仓库文件 |
| --- | --- |
| 写作台（屏 01/02） | app/src/views/WorkspaceView.vue, app/src/editor/extensions/AiDraft.ts, app/src/editor/use-novel-editor.ts |
| 设定库（屏 03） | app/src/views/CodexView.vue, app/src/stores/codex.ts, app/src/editor/extensions/CodexRef.ts |
| 一致性守卫（屏 04） | app/src/views/GuardView.vue, app/src/stores/guard.ts |
| 开书向导（屏 05） | app/src/views/WizardView.vue |
| 书架（屏 06） | app/src/views/ShelfView.vue, app/src/api/mock/shelf.ts |
| 大纲（屏 07） | app/src/views/OutlineView.vue |
| 风格档（屏 08） | app/src/views/StyleView.vue, app/src/api/mock/style-profile.ts |
| AI 占比自查（屏 09） | app/src/views/AiRatioView.vue, app/src/api/mock/ai-ratio.ts |
| 导出（屏 10） | app/src/views/ExportView.vue |
| 用量与计费（屏 11） | app/src/views/UsageView.vue |
| 登录（屏 12） | app/src/views/LoginView.vue |
| 应用外壳 | app/src/App.vue, app/src/router/index.ts, app/src/components/layout/*.vue |
| 接口与假数据 | app/src/api/http.ts, app/src/api/generation.ts, app/src/api/mock/*.ts |
| 设计系统 | app/src/styles/modernist.css, app/src/styles/app.css |
| 后端服务 | server/main.py, server/api/*.py, server/db/*.py, server/memory/*.py |
