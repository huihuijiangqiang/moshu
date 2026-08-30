# 墨枢多作品库草图 v3

- 日期：2026-08-30
- 用途：应用级多作品管理首页
- 生成方式：`imagegen` CLI/API 回退模式，模型 `gpt-image-2`
- 输出：`moshu-library-multi-project-concept-v3.png`

## 信息架构

```text
墨枢应用
├─ 作品库：多部小说、状态、进度、跨作品提醒
└─ 单部作品空间：卷章、正文、大纲、设定、守卫
```

应用首页使用高密度作品列表，不用封面卡片墙。每部小说显示题材、状态、字数、章节、最后编辑、目标进度和一致性问题；点击“继续写作”进入该小说的独立写作空间。

## 最终提示词

```text
Create a high-fidelity full-window desktop product UI mockup for 墨枢, a Chinese long-form novel writing application that manages multiple novels. This screen is the application-level 作品库 dashboard before opening a specific book. It must clearly communicate multi-project support and must not look like a workspace for only one novel.

Use the same contemporary neutral visual language as a professional productivity IDE: matte charcoal #1C2026, cool mist gray #F1F3F6, porcelain white #FCFDFE, near-black #17191D, secondary gray #69707A, borders #DDE1E7, controlled cobalt #315EFB for active/primary actions, coral #D6524A only for alerts, muted jade #3F7D67 only for completed/saved status. Full desktop viewport, no browser frame and no device mockup.

Layout: (1) fixed 72px matte-charcoal global navigation rail with simple white 墨 glyph at top and icon+label items 作品, 今日, 素材, 模型, 设置; 作品 is active with a thin cobalt indicator; (2) 240px light sidebar titled 作品库 containing 全部作品 6, 连载中 3, 筹备中 2, 已完结 1, 回收站, plus compact genre/tag filters and a small team/workspace switcher; (3) wide main content area titled 全部作品 with search, sort, list/grid segmented control, and a clear 新建作品 button.

Show a dense professional list, not a card grid, with 4-5 distinct novels. Each row has a small restrained cover thumbnail, exact Chinese title, genre/status, word count, chapter count, last edited chapter/time, writing progress bar, continuity issue count, and overflow menu. Include these examples: 《剑起山河》 男频·边关权谋 连载中 78.3万字 88章; 《城南旧事簿》 都市异闻 已完结 52.1万字 61章; 《长夜渡舟》 悬疑·民俗 连载中 12.6万字 16章; 《春风不度》 古言·群像 筹备中; 《失重花园》 科幻·悬疑 连载中. The selected or most recent row may have a very pale cobalt tint and an action 继续写作, but no novel should visually own the whole page.

(4) 300px right activity inspector titled 今日写作 showing cross-project total 4,820 / 6,000 字, a compact progress bar, recent activity across different novels, and 跨作品提醒 with items such as 剑起山河 · 3 条一致性问题 and 长夜渡舟 · 章纲待确认.

Information-dense, calm, practical for repeat use. Typography: Chinese sans-serif throughout with strong hierarchy and no oversized title. Use 1px dividers, row-based layout, squared geometry, radius <= 6px, almost no shadows. Avoid giant cover cards, nested cards, marketing copy, hero sections, gradients, glassmorphism, purple, blue-dominant backgrounds, beige, gold, forest green, decorative illustrations, oversized statistics, excessive pills, random English text, watermark, laptop frame, desk scene, or unreadable tiny text. Render all essential Chinese labels legibly. The page must unmistakably show that one account manages multiple independent novels and that clicking a row opens that novel's separate writing workspace.
```
