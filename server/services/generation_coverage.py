"""Explainable, non-blocking coverage checks for generation prompts and drafts.

The checks intentionally avoid claiming semantic certainty.  Prompt checks prove
that author-authored requirements reached the model package; draft checks only
surface lexical evidence and leave the final judgement to the author.
"""

import re
from typing import Any

_COMMON_BIGRAMS = {
    "一个",
    "一些",
    "以及",
    "之后",
    "他们",
    "她们",
    "这个",
    "那个",
    "什么",
    "如何",
    "开始",
    "继续",
    "需要",
    "本章",
    "场景",
    "人物",
    "主角",
}

_PROCEDURAL_TERMS = (
    "登记",
    "核验",
    "复称",
    "保管",
    "交接",
    "凭据",
    "责任",
    "封存",
    "时辰",
    "费用",
    "文书",
    "袋号",
)
_CHECKLIST_ENDING = re.compile(
    r"(?:^|[\n。；])\s*(?:一|二|三|四|五|六|七|八|九|十)[、，.]"
)
_TURN_MARKERS = (
    "却", "但", "没想到", "突然", "直到", "原本", "改口", "拒绝", "答应",
    "发现", "暴露", "扣住", "拦住", "失去", "转身", "反悔", "被截", "落空",
)
_HOOK_MARKERS = (
    "?", "？", "未完", "还没", "尚未", "却在", "突然", "门外", "脚步", "来人",
    "明日", "今晚", "必须", "等着", "不敢", "要么", "还是", "倒计时", "期限",
)
_HOOK_ACTION_MARKERS = (
    "敲", "撞", "闯", "扣", "拔", "抬", "转", "递", "烧", "撕", "打开", "按住",
    "站起", "回头", "冲进", "落笔", "封", "带走", "逼问", "拔刀", "来信",
)


def _summary(checks: list[dict[str, Any]], *, stage: str) -> dict[str, int | str]:
    attention = sum(check["status"] in {"attention", "author_review"} for check in checks)
    confirmed = sum(check["status"] in {"included", "evidence_found"} for check in checks)
    if stage == "prompt":
        message = f"生成依据已核对：{confirmed} 项已进入提示词，{attention} 项需要留意。"
    else:
        message = f"候选覆盖已核对：{confirmed} 项找到字面证据，{attention} 项需要作者确认。"
    return {"total": len(checks), "confirmed": confirmed, "attention": attention, "message": message}


def build_prompt_coverage(
    guidance: dict[str, Any],
    *,
    included_content: str,
    task: str,
) -> dict[str, Any]:
    """Report which planning requirements reached the exact prompt package."""
    checks = [dict(check) for check in guidance.get("inputChecks", [])]
    applicability = "chapter" if task == "chapter" else "reference"
    for requirement in guidance.get("requirements", []):
        raw_expected = [str(value) for value in requirement.get("expected", []) if str(value).strip()]
        included = bool(raw_expected) and all(value in included_content for value in raw_expected)
        expected = [value if len(value) <= 240 else value[:237] + "..." for value in raw_expected]
        source_type = requirement.get("sourceType", "plan")
        checks.append(
            {
                "id": requirement["id"],
                "checkType": "requirement",
                "sourceType": source_type,
                "sourceId": requirement.get("sourceId"),
                "label": requirement["label"],
                "status": "included" if included else "attention",
                "severity": "info" if included or applicability == "reference" else "warning",
                "applicability": applicability,
                "message": (
                    "已进入本次生成提示词。"
                    if included
                    else "内容受上下文预算裁剪影响，未完整进入提示词，请缩短规划后重试。"
                ),
                "expected": expected,
                "expectedTruncated": any(len(value) > 240 for value in raw_expected),
                "evidence": expected if included else [],
            }
        )
    return {
        "stage": "prompt",
        "blocking": False,
        "status": "needs_attention"
        if any(check["status"] == "attention" and check["severity"] == "warning" for check in checks)
        else "ready",
        "summary": _summary(checks, stage="prompt"),
        "checks": checks,
    }


def _salient_terms(values: list[str]) -> list[str]:
    terms: list[str] = []
    for value in values:
        for latin in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", value):
            terms.append(latin.lower())
        for run in re.findall(r"[\u3400-\u9fff]+", value):
            if len(run) <= 4:
                terms.append(run)
            width = 2
            terms.extend(run[index : index + width] for index in range(len(run) - width + 1))
    unique: list[str] = []
    for term in terms:
        if term in _COMMON_BIGRAMS or term in unique:
            continue
        unique.append(term)
        if len(unique) >= 24:
            break
    return unique


def _narrative_vitality_checks(content: str) -> list[dict[str, Any]]:
    """Surface deterministic symptoms of report-like, low-drama prose.

    This is deliberately a review signal rather than an automatic rejection:
    procedural language can be valid in courtroom or investigation scenes, but
    a dense cluster gives the author a concrete reason to inspect the draft.
    """
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", content or ""))
    if cjk_count < 600:
        return []

    term_counts = {term: content.count(term) for term in _PROCEDURAL_TERMS}
    hits = sum(term_counts.values())
    density = hits * 1000 / max(cjk_count, 1)
    used = [term for term, count in term_counts.items() if count]
    report_like = density >= 18 and len(used) >= 5
    checks: list[dict[str, Any]] = [
        {
            "id": "quality.procedural_density",
            "checkType": "quality",
            "sourceType": "quality",
            "sourceId": None,
            "label": "戏剧张力",
            "status": "author_review" if report_like else "evidence_found",
            "severity": "warning" if report_like else "info",
            "message": (
                "候选中过多篇幅用于登记、核验、责任或费用说明，可能读成办事记录；请确认是否有主动对手、"
                "升级压力、不可逆选择和情绪兑现。"
                if report_like
                else "未发现流程词汇明显挤占故事篇幅。"
            ),
            "expected": [],
            "evidence": [
                f"每千字流程词约 {density:.1f} 次",
                *[f"{term}×{term_counts[term]}" for term in used[:8]],
            ],
        }
    ]

    thesis_count = content.count("不等于")
    ending = content[-1600:]
    checklist_ending = bool(_CHECKLIST_ENDING.search(ending)) and any(
        term in ending for term in _PROCEDURAL_TERMS
    )
    summary_ending = thesis_count >= 3 or checklist_ending
    checks.append(
        {
            "id": "quality.report_ending",
            "checkType": "quality",
            "sourceType": "quality",
            "sourceId": None,
            "label": "章末钩子",
            "status": "author_review" if summary_ending else "evidence_found",
            "severity": "warning" if summary_ending else "info",
            "message": (
                "章末出现规则复述、待办清单或反复使用“不等于”，可能没有落在人物选择或对手行动上。"
                if summary_ending
                else "未发现明显的报告式章末。"
            ),
            "expected": [],
            "evidence": [
                f"全文“不等于”×{thesis_count}",
                f"章末待办清单：{'是' if checklist_ending else '否'}",
            ],
        }
    )
    return checks


def _dramatic_contract_checks(content: str, prompt_coverage: dict[str, Any]) -> list[dict[str, Any]]:
    """Surface weak turn/hook evidence without pretending to judge semantics.

    A model can repeat a scene-card sentence verbatim while keeping the story's
    state unchanged. These checks therefore inspect *where* evidence appears:
    a turn should have a late transition plus an action/choice, and a hook should
    leave a concrete unresolved pressure in the final part of the draft. They
    remain author-review warnings, never automatic acceptance decisions.
    """
    text = content or ""
    if len(text) < 300:
        return []
    checks: list[dict[str, Any]] = []
    requirements = [
        item for item in prompt_coverage.get("checks", [])
        if isinstance(item, dict) and item.get("checkType") == "requirement"
    ]
    turn_requirements = [item for item in requirements if item.get("semanticType") == "turn"]
    hook_requirements = [item for item in requirements if item.get("semanticType") == "hook"]

    if turn_requirements:
        midpoint = max(1, len(text) // 2)
        late = text[midpoint:]
        transitions = [marker for marker in _TURN_MARKERS if marker in late]
        actions = [marker for marker in _HOOK_ACTION_MARKERS if marker in late]
        checks.append(
            {
                "id": "quality.turning_point",
                "checkType": "quality",
                "sourceType": "scene",
                "sourceId": turn_requirements[0].get("sourceId"),
                "label": "中段转折落地",
                "status": "evidence_found" if transitions and actions else "author_review",
                "severity": "info" if transitions and actions else "warning",
                "message": (
                    "后半段同时出现了状态转向和行动证据；仍需确认原策略是否真的失效。"
                    if transitions and actions
                    else "后半段缺少成对的状态转向与行动证据；转折可能只是新增信息或旁白宣告。"
                ),
                "expected": [],
                "evidence": [
                    "转向词：" + "、".join(transitions[:6]) if transitions else "未找到明显转向词",
                    "行动词：" + "、".join(actions[:6]) if actions else "未找到明显选择/后果行动",
                ],
            }
        )

    if hook_requirements:
        tail = text[-1200:]
        unresolved = [marker for marker in _HOOK_MARKERS if marker in tail]
        actions = [marker for marker in _HOOK_ACTION_MARKERS if marker in tail]
        has_summary = any(phrase in tail for phrase in ("这一切", "终于明白", "新的篇章", "才刚刚开始"))
        strong = bool(unresolved and actions and not has_summary)
        checks.append(
            {
                "id": "quality.chapter_hook",
                "checkType": "quality",
                "sourceType": "scene",
                "sourceId": hook_requirements[-1].get("sourceId"),
                "label": "章尾具体追读问题",
                "status": "evidence_found" if strong else "author_review",
                "severity": "info" if strong else "warning",
                "message": (
                    "章尾附近同时出现了未决压力和具体行动；请确认下一章问题尚未被提前解决。"
                    if strong
                    else "章尾未形成清晰的未决压力+具体动作组合，可能以总结、离场或空泛预告收尾。"
                ),
                "expected": [],
                "evidence": [
                    "未决信号：" + "、".join(unresolved[:8]) if unresolved else "未找到未决信号",
                    "动作信号：" + "、".join(actions[:8]) if actions else "未找到章尾动作信号",
                    "总结式尾声：" + ("是" if has_summary else "否"),
                ],
            }
        )
    return checks


def assess_draft_coverage(prompt_coverage: dict[str, Any] | None, content: str) -> dict[str, Any] | None:
    """Turn a stored prompt report into a conservative draft-review report."""
    if not prompt_coverage:
        return None
    normalized_content = (content or "").lower()
    checks: list[dict[str, Any]] = []
    for original in prompt_coverage.get("checks", []):
        if original.get("checkType") != "requirement":
            checks.append(dict(original))
            continue

        check = dict(original)
        expected = [str(value) for value in check.get("expected", []) if str(value).strip()]
        terms = _salient_terms(expected)
        matches = [term for term in terms if term.lower() in normalized_content]
        is_reference = check.get("applicability") == "reference"
        # Two separate terms reduce false positives from common two-character words.
        evidence_found = len(matches) >= 2 or (len(matches) == 1 and len(matches[0]) >= 4)
        if evidence_found:
            check.update(
                status="evidence_found",
                severity="info",
                message="候选中找到相关字面证据；仍建议结合上下文确认语义是否真正兑现。",
                evidence=matches[:8],
            )
        else:
            check.update(
                status="author_review",
                severity="info" if check.get("sourceType") == "positioning" or is_reference else "warning",
                message=(
                    "行内任务不要求覆盖整章规划，此项仅作为一致性参考。"
                    if is_reference
                    else "未找到足够字面证据；可能使用了同义改写，请作者确认后再采纳。"
                ),
                evidence=[],
            )
        checks.append(check)

    checks.extend(_narrative_vitality_checks(content))
    checks.extend(_dramatic_contract_checks(content, prompt_coverage))
    return {
        "stage": "draft",
        "blocking": False,
        "status": "needs_attention"
        if any(check["status"] in {"attention", "author_review"} and check["severity"] == "warning" for check in checks)
        else "ready",
        "summary": _summary(checks, stage="draft"),
        "checks": checks,
        "method": "lexical_evidence_v2_dramatic_contract",
        "disclaimer": "字面证据只能辅助复核，不能替代作者对情节兑现和语义一致性的判断。",
    }
