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

    return {
        "stage": "draft",
        "blocking": False,
        "status": "needs_attention"
        if any(check["status"] in {"attention", "author_review"} and check["severity"] == "warning" for check in checks)
        else "ready",
        "summary": _summary(checks, stage="draft"),
        "checks": checks,
        "method": "lexical_evidence_v1",
        "disclaimer": "字面证据只能辅助复核，不能替代作者对情节兑现和语义一致性的判断。",
    }
