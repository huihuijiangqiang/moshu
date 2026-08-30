# 墨枢写作工作台草图 v1

- 日期：2026-08-30
- 用途：前端重设计前的桌面端视觉草图
- 生成方式：`imagegen` CLI/API 回退模式，模型 `gpt-image-2`
- 输出：`moshu-workspace-concept-v1.png`（实际 1536 x 1024）

## 设计方向

面向中文长篇连载作者的专业写作 IDE。界面需要适合每天连续使用数小时，信息密度高但安静克制，重点突出章节组织、正文写作、AI 变更审阅和长篇一致性证据。

四区布局：

1. 64px 深墨绿全局功能轨：书架、正文、大纲、设定、守卫。
2. 280px 项目与章节树：卷章层级、搜索、状态点、新建章节。
3. 中央正文编辑器：居中长文纸面、章节信息、保存状态、行内 AI 修改审阅。
4. 320px 写作助手：上下文、相关人物/设定、连续性告警、生成动作。

颜色：墨绿 `#173B34`、冷雾白 `#F4F6F4`、纸白 `#FCFCFA`、石墨 `#202522`、钢灰 `#68736F`、朱砂 `#C9493D`、少量黄铜 `#B59A55`。

## 最终提示词

```text
Use case: ui-mockup
Asset type: high-fidelity desktop product UI mockup

Create a high-fidelity desktop product UI mockup for a Chinese long-form novel writing application called 墨枢. This is a serious daily workbench for professional serialized-fiction authors, shown as a clean full-window app screenshot, not a website landing page and not device hardware.

Use a four-zone desktop layout: (1) a fixed 64px dark ink-green global navigation rail on the far left with a distinctive seal-like 墨 mark and icons with compact labels 书架, 正文, 大纲, 设定, 守卫, with 正文 active; (2) a 280px cool-gray project sidebar with project title 《剑起山河》, searchable volume and chapter tree, clear hierarchy, chapter status dots, current item 第八十七章 残灯照雪, plus a compact add-chapter icon; (3) a spacious central writing editor on a quiet off-white paper surface, centered readable text column, top line showing 第八十七章 残灯照雪, 2,846 字 and 已保存, Chinese novel paragraphs in a refined Songti-style typeface, a small selection toolbar, and one subtle inline AI change-review block with accept/reject controls that never dominates the manuscript; (4) a 320px right inspector titled 写作助手 with tabs 上下文 and 审阅, a compact chapter goal field, relevant character/setting references, one evidence-backed continuity warning in vermilion, and a clear primary action 生成下一段.

Add a slim top title bar with breadcrumb and export/search controls. Information-dense but calm, designed for 6-hour writing sessions. Color palette: ink green #173B34, cool fog white #F4F6F4, paper white #FCFCFA, graphite #202522, muted steel #68736F, restrained vermilion #C9493D, and a tiny amount of brass #B59A55. Typography should be crisp and readable, using Chinese sans-serif for utility UI and Chinese Songti serif only for manuscript text. Use 1px dividers, squared geometry, corner radius no more than 6px, stable alignment and realistic spacing.

The signature detail is a narrow context-evidence spine on the right edge of the manuscript with small markers linking characters, timeline, and foreshadowing to the inspector.

Avoid gradients, giant cards, nested cards, purple, blue-dominant palettes, beige theme, glassmorphism, decorative blobs, illustrations, marketing copy, oversized headings, floating panels, excessive shadows, excessive rounded pills, random English text, watermark, browser chrome, laptop frame, hands, desk scene, or unreadable microtext. Render essential Chinese labels legibly and exactly once.
```
