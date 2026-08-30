# 墨枢写作工作台草图 v2：冷调中性色

- 日期：2026-08-30
- 输入：`moshu-workspace-concept-v1.png`
- 用途：仅替换 v1 配色，保持界面结构和内容不变
- 生成方式：`imagegen` CLI/API 回退模式，模型 `gpt-image-2`，图片编辑
- 输出：`moshu-workspace-concept-v2-cool-neutral.png`

## 配色

- 全局功能轨：炭黑 `#1C2026`
- 章节栏：冷雾灰 `#F1F3F6`
- 正文：瓷白 `#FCFDFE`
- 写作助手：冷浅灰 `#F7F8FA`
- 主文字：`#17191D`
- 次级文字：`#69707A`
- 分隔线：`#DDE1E7`
- 主操作：钴蓝 `#315EFB`
- 选中背景：`#E9EEFF`
- 冲突告警：珊瑚红 `#D6524A`
- 保存成功：灰绿 `#3F7D67`

## 最终提示词

```text
Image 1 is the edit target. Change only the color system of this Chinese novel-writing desktop UI. Preserve the exact four-column layout, viewport crop, dimensions, panel widths, all controls, icons, Chinese labels, manuscript text, typography hierarchy, spacing, borders, character portraits, continuity warning content, selection review block, and every functional element. Do not redesign, move, remove, add, or rewrite anything.

Replace the old traditional dark-green, gold, cream, and vermilion-heavy palette with a crisp contemporary neutral productivity palette: far-left navigation rail matte charcoal #1C2026; chapter sidebar cool mist gray #F1F3F6; central editor clean porcelain white #FCFDFE; right inspector very light cool gray #F7F8FA; primary text near-black #17191D; secondary text #69707A; borders #DDE1E7. Use cobalt #315EFB only for the active navigation indicator, selected chapter detail, focus state, and primary generate/accept buttons. Use pale cobalt tint #E9EEFF for selections. Use coral red #D6524A only for the continuity warning and destructive/reject states. Use muted jade #3F7D67 only for the small saved-success indicator.

Remove every trace of gold, brass, yellow-brown, beige paper, forest green panels, and decorative cultural-theme coloring. The logo should become a simple white 墨 glyph on charcoal with a thin cobalt focus outline, not a gold seal. Keep backgrounds flat with no gradients. Maintain high contrast and calm brightness for six-hour writing sessions. Blue must remain a controlled accent, never a large background. No purple, no glassmorphism, no shadows, no new cards, no watermark. This is a precise palette replacement only; all geometry and content must remain invariant.
```
