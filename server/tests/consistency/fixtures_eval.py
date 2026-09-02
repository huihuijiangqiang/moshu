"""Versioned evaluation corpus for the three deterministic P0 rules.

The corpus deliberately keeps hard negatives on the same subjects, objects, and
timelines as positive candidates. They exercise the scanner's actual candidate
path instead of inflating quality with unrelated prose.
"""

from __future__ import annotations

from typing import Any

CORPUS_VERSION = "rule-eval-v1"
POSITIVE_CASES_PER_RULE = 40
HARD_NEGATIVE_CASES_PER_RULE = 20
EASY_NEGATIVE_CASES = 20


def _claim(
    case_id: str,
    side: int,
    *,
    subject: str,
    predicate: str,
    object_type: str,
    object_value: str | None,
    story_order: float | None,
    timeline_id: str = "main",
    chapter_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    chapter = chapter_id or ("ch_source" if side == 1 else "ch_followup")
    return {
        "subject_text": subject,
        "predicate": predicate,
        "object_type": object_type,
        "object_value": object_value,
        "story_order": story_order,
        "timeline_id": timeline_id,
        "chapter_id": chapter,
        "body_rev": 1,
        "paragraph_id": f"{case_id}-p{side}",
        "source_anchor": f"P{side - 1}",
        "fingerprint": f"{CORPUS_VERSION}-{case_id}-claim-{side}",
        **extra,
    }


def _case(
    case_id: str,
    *,
    split: str,
    rule: str | None,
    description: str,
    claims: list[dict[str, Any]],
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "corpus_version": CORPUS_VERSION,
        "split": split,
        "rule": rule,
        "description": description,
        "claims": claims,
        "expected_conflict": split == "positive",
        "expected_issue_type": rule if split == "positive" else None,
        "expected_evidence": [claim["paragraph_id"] for claim in claims],
        "reason": reason,
    }


def _positive_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for index in range(POSITIVE_CASES_PER_RULE):
        suffix = f"{index + 1:03d}"
        alive_id = f"alive-positive-{suffix}"
        alive_subject = f"生死正例人物{suffix}"
        cases.append(
            _case(
                alive_id,
                split="positive",
                rule="alive_conflict",
                description="角色明确死亡后，在同一时间线中无解释地再次活动。",
                claims=[
                    _claim(
                        alive_id,
                        1,
                        subject=alive_subject,
                        predicate="alive",
                        object_type="scalar",
                        object_value="false",
                        story_order=1000 + index * 10,
                    ),
                    _claim(
                        alive_id,
                        2,
                        subject=alive_subject,
                        predicate="alive",
                        object_type="scalar",
                        object_value="true",
                        story_order=1001 + index * 10,
                    ),
                ],
            )
        )

        ownership_id = f"ownership-positive-{suffix}"
        item_id = f"eval-item-positive-{suffix}"
        cases.append(
            _case(
                ownership_id,
                split="positive",
                rule="ownership_conflict",
                description="同一件物品在重叠有效期内被声明为归不同人物所有。",
                claims=[
                    _claim(
                        ownership_id,
                        1,
                        subject=f"原持有人{suffix}",
                        predicate="owns",
                        object_type="entity",
                        object_value=f"冲突物品{suffix}",
                        object_entry_id=item_id,
                        story_order=2000 + index * 10,
                        valid_from_order=2000 + index * 10,
                        valid_to_order=2005 + index * 10,
                    ),
                    _claim(
                        ownership_id,
                        2,
                        subject=f"新持有人{suffix}",
                        predicate="owns",
                        object_type="entity",
                        object_value=f"冲突物品{suffix}",
                        object_entry_id=item_id,
                        story_order=2001 + index * 10,
                        valid_from_order=2001 + index * 10,
                    ),
                ],
            )
        )

        knowledge_id = f"knowledge-positive-{suffix}"
        knowledge_subject = f"知情正例人物{suffix}"
        knowledge = f"密信内容{suffix}"
        cases.append(
            _case(
                knowledge_id,
                split="positive",
                rule="knowledge_boundary",
                description="角色先使用秘密，后在同一时间线中才获知该秘密。",
                claims=[
                    _claim(
                        knowledge_id,
                        1,
                        subject=knowledge_subject,
                        predicate="uses_knowledge",
                        object_type="scalar",
                        object_value=knowledge,
                        story_order=3000 + index * 10,
                    ),
                    _claim(
                        knowledge_id,
                        2,
                        subject=knowledge_subject,
                        predicate="acquires_knowledge",
                        object_type="scalar",
                        object_value=knowledge,
                        story_order=3001 + index * 10,
                    ),
                ],
            )
        )
    return cases


def _alive_hard_negative(index: int) -> dict[str, Any]:
    suffix = f"{index + 1:03d}"
    case_id = f"alive-hard-negative-{suffix}"
    subject = f"生死反例人物{suffix}"
    base = 4000 + index * 10
    if index < 8:
        claims = [
            _claim(case_id, 1, subject=subject, predicate="alive", object_type="scalar", object_value="true", story_order=base),
            _claim(case_id, 2, subject=subject, predicate="alive", object_type="scalar", object_value="false", story_order=base + 1),
        ]
        reason = "自然的由生到死不是复活冲突。"
    elif index < 14:
        claims = [
            _claim(case_id, 1, subject=subject, predicate="alive", object_type="scalar", object_value="false", story_order=base, timeline_id="main"),
            _claim(case_id, 2, subject=subject, predicate="alive", object_type="scalar", object_value="true", story_order=base + 1, timeline_id="dream"),
        ]
        reason = "主时间线死亡与梦境时间线活动不能相互推出冲突。"
    else:
        claims = [
            _claim(case_id, 1, subject=subject, predicate="alive", object_type="scalar", object_value="false", story_order=None),
            _claim(case_id, 2, subject=subject, predicate="alive", object_type="scalar", object_value="true", story_order=base + 1),
        ]
        reason = "缺少可靠先后顺序时必须保守跳过。"
    return _case(case_id, split="hard_negative", rule="alive_conflict", description="共享角色的非冲突生死状态。", claims=claims, reason=reason)


def _ownership_hard_negative(index: int) -> dict[str, Any]:
    suffix = f"{index + 1:03d}"
    case_id = f"ownership-hard-negative-{suffix}"
    item_id = f"eval-item-negative-{suffix}"
    base = 5000 + index * 10
    if index < 8:
        claims = [
            _claim(case_id, 1, subject=f"赠与人{suffix}", predicate="owns", object_type="entity", object_value=f"转让物品{suffix}", object_entry_id=item_id, story_order=base, valid_from_order=base, valid_to_order=base + 1),
            _claim(case_id, 2, subject=f"受赠人{suffix}", predicate="owns", object_type="entity", object_value=f"转让物品{suffix}", object_entry_id=item_id, story_order=base + 1, valid_from_order=base + 1),
        ]
        reason = "旧归属在新归属开始时已经结束。"
    elif index < 14:
        owner = f"同一持有人{suffix}"
        claims = [
            _claim(case_id, 1, subject=owner, predicate="owns", object_type="entity", object_value=f"持续持有物品{suffix}", object_entry_id=item_id, story_order=base),
            _claim(case_id, 2, subject=owner, predicate="owns", object_type="entity", object_value=f"持续持有物品{suffix}", object_entry_id=item_id, story_order=base + 1),
        ]
        reason = "同一持有人重复出现不构成归属冲突。"
    else:
        claims = [
            _claim(case_id, 1, subject=f"现实持有人{suffix}", predicate="owns", object_type="entity", object_value=f"分线物品{suffix}", object_entry_id=item_id, story_order=base, timeline_id="main"),
            _claim(case_id, 2, subject=f"梦境持有人{suffix}", predicate="owns", object_type="entity", object_value=f"分线物品{suffix}", object_entry_id=item_id, story_order=base + 1, timeline_id="dream"),
        ]
        reason = "不同时间线的归属不直接冲突。"
    return _case(case_id, split="hard_negative", rule="ownership_conflict", description="共享物品的合法归属变化。", claims=claims, reason=reason)


def _knowledge_hard_negative(index: int) -> dict[str, Any]:
    suffix = f"{index + 1:03d}"
    case_id = f"knowledge-hard-negative-{suffix}"
    subject = f"知情反例人物{suffix}"
    knowledge = f"账册秘密{suffix}"
    base = 6000 + index * 10
    if index < 8:
        claims = [
            _claim(case_id, 1, subject=subject, predicate="acquires_knowledge", object_type="scalar", object_value=knowledge, story_order=base),
            _claim(case_id, 2, subject=subject, predicate="uses_knowledge", object_type="scalar", object_value=knowledge, story_order=base + 1),
        ]
        reason = "先获知再使用符合知情边界。"
    elif index < 14:
        claims = [
            _claim(case_id, 1, subject=subject, predicate="uses_knowledge", object_type="scalar", object_value=f"甲{knowledge}", story_order=base),
            _claim(case_id, 2, subject=subject, predicate="acquires_knowledge", object_type="scalar", object_value=f"乙{knowledge}", story_order=base + 1),
        ]
        reason = "同一角色涉及不同信息，不应串成冲突。"
    else:
        claims = [
            _claim(case_id, 1, subject=subject, predicate="uses_knowledge", object_type="scalar", object_value=knowledge, story_order=base, timeline_id="main"),
            _claim(case_id, 2, subject=subject, predicate="acquires_knowledge", object_type="scalar", object_value=knowledge, story_order=base + 1, timeline_id="flashback"),
        ]
        reason = "不同时间线的知情状态相互独立。"
    return _case(case_id, split="hard_negative", rule="knowledge_boundary", description="共享人物和信息的合法知情过程。", claims=claims, reason=reason)


def _hard_negative_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for index in range(HARD_NEGATIVE_CASES_PER_RULE):
        cases.extend(
            [
                _alive_hard_negative(index),
                _ownership_hard_negative(index),
                _knowledge_hard_negative(index),
            ]
        )
    return cases


def _easy_negative_cases() -> list[dict[str, Any]]:
    return [
        _case(
            f"easy-negative-{index + 1:03d}",
            split="easy_negative",
            rule=None,
            description="互不相关的人物地点描写。",
            claims=[
                _claim(
                    f"easy-negative-{index + 1:03d}",
                    1,
                    subject=f"路人{index + 1:03d}",
                    predicate="located_at",
                    object_type="location",
                    object_value=f"村落{index + 1:03d}",
                    story_order=7000 + index * 10,
                ),
                _claim(
                    f"easy-negative-{index + 1:03d}",
                    2,
                    subject=f"商队{index + 1:03d}",
                    predicate="weather_observed",
                    object_type="scalar",
                    object_value="晴",
                    story_order=7001 + index * 10,
                ),
            ],
            reason="主体、谓词和对象均无冲突关系。",
        )
        for index in range(EASY_NEGATIVE_CASES)
    ]


POSITIVE_CASES = _positive_cases()
HARD_NEGATIVES = _hard_negative_cases()
EASY_NEGATIVES = _easy_negative_cases()


def get_all_fixtures() -> dict[str, list[dict[str, Any]]]:
    return {
        "positive": POSITIVE_CASES,
        "hard_negative": HARD_NEGATIVES,
        "easy_negative": EASY_NEGATIVES,
    }


def get_positive_count() -> int:
    return len(POSITIVE_CASES)


def get_hard_negative_count() -> int:
    return len(HARD_NEGATIVES)
