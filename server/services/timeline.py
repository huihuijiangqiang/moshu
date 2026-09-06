"""
时间线服务 - 把可追溯的时间锚点解析成全局可比的 story_order。

为什么这一层必须存在（架构 4.3）：

抽取是**逐块**进行的。如果让模型自己给 story_order，它只能在当前块内自行编号
——「use any increasing numbers」。块与块之间、章与章之间没有共同标尺：第 1 块
的 3.0 和第 2 块的 3.0 毫无关系，第 1 块的 5.0 也不代表比第 2 块的 3.0 晚。但
规则扫描是**全项目**范围的，它会把这些数字直接放在一起排序。于是局部序号被当
成全局序号，抽取器越"配合"，伪造的顺序就越多。

所以职责这样切分：

* 模型只报**可追溯的证据**：正文里那句时间表述的原文（temporal_anchor_text）、
  它归一化后的绝对时间（temporal_anchor_value），或者它相对于哪个锚点的先后
  关系（temporal_relation / temporal_relation_ref）。这些都能回到正文里核对。
* story_order 只由本模块分配，且只依据**已确认的全局锚点**。没有共同锚点就
  保持 NULL —— claim 照常落库进入待确认，不参与依赖时序的硬规则。

保守范围限制
------------
本版支持明确的 ISO 绝对时间，以及能精确、唯一引用到这类全局锚点的确定性相对时长。
事件标签与时间证据随 claim 持久化，因此后章可以引用前章，当前批次内也可以链式引用。
以下情形仍保持 NULL：

* ``relative_to_anchor`` 的时长模糊、事件标签缺失/歧义、跨时间线或方向冲突；
* ``narration_local``：块内叙述顺序。这**恰恰是不能用**的那一类 —— 它只在块内
  有意义，跨块不可比。见 tests/test_timeline.py 里的对应测试。
* ``unknown``：模型自己都说不确定。

无法审计到原文、无法确定解析的时间不会进入硬规则。这会漏报，但不会为了提高命中率
伪造顺序；误报（把正常倒叙判成矛盾）对作者的伤害更大。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

#: order_basis 的合法取值。
#:
#: absolute_datetime：正文写明了可归一化的绝对时间 —— 唯一的全局锚点。
#: relative_to_anchor：只给了相对关系（「三天后」），需要先确认参照的绝对时间。
#: narration_local：仅块内叙述顺序，跨块不可比，绝不能当全局序号使用。
#: unknown：无法判断。
VALID_ORDER_BASES = ("absolute_datetime", "relative_to_anchor", "narration_local", "unknown")

#: MVP 里唯一能分配全局 story_order 的依据。
GLOBAL_ORDER_BASES = frozenset({"absolute_datetime"})

#: 采信锚点所需的最低置信度。低于它只进待确认。
MIN_ORDER_CONFIDENCE = 0.7

#: story_order 的标尺：绝对时间的 Unix 秒。同一时间线内单调，跨块跨章一致。
#: 用秒而不是「第几个事件」，就是为了让标尺来自正文本身而不是抽取顺序。
_ISO_PATTERNS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)

_ISO_LIKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?$")

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_RELATIVE_RE = re.compile(
    r"^(?P<number>半|\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百]+)"
    r"(?:个)?(?P<unit>分钟|小时|时辰|天|日|周)(?P<direction>前|后)$"
)
_UNIT_SECONDS = {
    "分钟": 60,
    "小时": 3600,
    "时辰": 7200,
    "天": 86400,
    "日": 86400,
    "周": 7 * 86400,
}


@dataclass(frozen=True)
class RelativeTimeResolution:
    """可审计的相对时间解析结果。

    模糊表达只产生区间，不会被提升成一个伪精确的 story_order。
    """

    original: str
    offset_min_seconds: float
    offset_max_seconds: float
    normalized: str
    precision: str

    @property
    def is_exact(self) -> bool:
        return self.offset_min_seconds == self.offset_max_seconds

    def as_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "offset_min_seconds": self.offset_min_seconds,
            "offset_max_seconds": self.offset_max_seconds,
            "normalized": self.normalized,
            "precision": self.precision,
            "is_exact": self.is_exact,
        }


class UnparseableAnchorError(ValueError):
    """锚点值无法归一化成绝对时间。"""


def parse_absolute_anchor(value: Optional[str]) -> Optional[float]:
    """把归一化的绝对时间字符串解析成 Unix 秒；无法解析返回 None。

    只接受 ISO-8601 形状的值。模型给的是自然语言（「三日后的清晨」）时，
    temporal_anchor_value 应当留空 —— 这里返回 None，claim 进待确认。

    返回 None 而不是抛错：一条锚点解析不了不该让整章抽取失败，它只是不进硬规则。
    """
    if not value:
        return None
    candidate = value.strip()
    if not _ISO_LIKE_RE.match(candidate):
        return None
    for pattern in _ISO_PATTERNS:
        try:
            parsed = datetime.strptime(candidate, pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc).timestamp()
    return None


def _parse_chinese_number(value: str) -> Optional[float]:
    if value == "半":
        return 0.5
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)
    if "百" in value:
        left, right = value.split("百", 1)
        hundreds = _CN_DIGITS.get(left, 1) if left else 1
        tail = _parse_chinese_number(right) if right else 0
        return None if tail is None else hundreds * 100 + tail
    if "十" in value:
        left, right = value.split("十", 1)
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return None if ones is None else tens * 10 + ones
    digits = [_CN_DIGITS.get(char) for char in value]
    if any(digit is None for digit in digits):
        return None
    return float("".join(str(digit) for digit in digits))


def parse_relative_offset(value: Optional[str]) -> Optional[float]:
    """Parse a narrow, auditable relative duration into signed seconds."""
    if not value:
        return None
    candidate = re.sub(r"\s+", "", value)
    match = _RELATIVE_RE.fullmatch(candidate)
    if not match:
        return None
    number = _parse_chinese_number(match.group("number"))
    if number is None or number <= 0:
        return None
    seconds = number * _UNIT_SECONDS[match.group("unit")]
    return seconds if match.group("direction") == "后" else -seconds


_VAGUE_DAY_RE = re.compile(r"^(?P<prefix>过|约|大约|将近)?(?P<number>几|数)(?:个)?(?P<direction>日|天)(?P<suffix>后|前)$")
_CALENDAR_RE = re.compile(
    r"^(?P<number>[一二两三四五六七八九十百\d]+)(?:个)?(?P<unit>月|年)(?P<direction>后|前)$"
)
_DAYPART_HOURS = {
    "清晨": (4, 8),
    "早晨": (5, 9),
    "上午": (8, 12),
    "中午": (11, 14),
    "下午": (12, 18),
    "傍晚": (17, 20),
    "夜里": (19, 24),
    "深夜": (22, 30),
}


def normalize_relative_expression(value: Optional[str]) -> Optional[RelativeTimeResolution]:
    """Normalize exact and fuzzy relative calendar expressions into auditable ranges.

    The parser deliberately does not guess a single point for calendar units. A month is
    represented as 28-31 days and a year as 365-366 days. Day-part suffixes widen the
    range by the documented hours. Callers may safely persist this metadata and keep the
    claim out of hard ordering rules unless ``is_exact`` is true.
    """
    if not value:
        return None
    original = str(value)
    candidate = re.sub(r"\s+", "", original)
    exact = parse_relative_offset(candidate)
    if exact is not None:
        return RelativeTimeResolution(candidate, exact, exact, "exact_duration", "exact")

    if candidate in {"次日", "翌日", "隔日", "第二日"}:
        return RelativeTimeResolution(candidate, 86400, 86400, "1日后", "calendar_day")
    if candidate in {"当日", "同日", "当晚", "当天"}:
        return RelativeTimeResolution(candidate, 0, 0, "同日", "exact_day")

    match = _VAGUE_DAY_RE.fullmatch(candidate)
    if match:
        sign = 1 if match.group("suffix") == "后" else -1
        low, high = sorted((sign * 2 * 86400, sign * 7 * 86400))
        return RelativeTimeResolution(
            candidate,
            low,
            high,
            "2-7日后" if sign > 0 else "2-7日前",
            "fuzzy_days",
        )

    match = _CALENDAR_RE.fullmatch(candidate)
    if match:
        number = _parse_chinese_number(match.group("number"))
        if number is not None and number > 0:
            sign = 1 if match.group("direction") == "后" else -1
            if match.group("unit") == "月":
                lower, upper = number * 28 * 86400, number * 31 * 86400
                normalized = f"{number:g}月后" if sign > 0 else f"{number:g}月前"
            else:
                lower, upper = number * 365 * 86400, number * 366 * 86400
                normalized = f"{number:g}年后" if sign > 0 else f"{number:g}年前"
            return RelativeTimeResolution(
                candidate,
                min(sign * lower, sign * upper),
                max(sign * lower, sign * upper),
                normalized,
                "calendar_month" if match.group("unit") == "月" else "calendar_year",
            )

    daypart = next((name for name in _DAYPART_HOURS if candidate.endswith(name)), None)
    if daypart:
        base = candidate[: -len(daypart)]
        if base.endswith("的"):
            base = base[:-1]
        base_resolution = normalize_relative_expression(base)
        if base_resolution is not None:
            start, end = _DAYPART_HOURS[daypart]
            return RelativeTimeResolution(
                candidate,
                base_resolution.offset_min_seconds + start * 3600,
                base_resolution.offset_max_seconds + end * 3600,
                f"{base_resolution.normalized}+{daypart}",
                "fuzzy_daypart",
            )
    return None


def build_temporal_dependency_graph(
    claims: list[dict], *, known_anchors: Optional[list[dict]] = None
) -> dict[str, Any]:
    """Build a dependency graph and report unresolved, ambiguous, or cyclic references."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    by_ref: dict[tuple[str, str], set[float]] = {}
    for anchor in known_anchors or []:
        ref = _event_ref(anchor)
        timeline = anchor.get("timeline_id")
        order = anchor.get("story_order")
        if ref and timeline and order is not None:
            by_ref.setdefault((timeline, ref), set()).add(float(order))
    # Current-batch absolute anchors are valid dependencies even before persistence.
    for claim in claims:
        ref = _event_ref(claim)
        timeline = claim.get("timeline_id")
        if not ref or not timeline:
            continue
        order = claim.get("story_order")
        if order is None and claim.get("order_basis") in GLOBAL_ORDER_BASES:
            order = parse_absolute_anchor(claim.get("temporal_anchor_value"))
        if order is not None:
            by_ref.setdefault((timeline, ref), set()).add(float(order))
    for index, claim in enumerate(claims):
        ref = _event_ref(claim)
        node_id = str(claim.get("fingerprint") or claim.get("id") or f"claim:{index}")
        nodes[node_id] = {"timeline_id": claim.get("timeline_id"), "event_ref": ref}
        relation_ref = re.sub(
            r"\s+", "", str(claim.get("temporal_relation_ref") or "")
        ).casefold() or None
        if claim.get("order_basis") != "relative_to_anchor" or not relation_ref:
            continue
        timeline = claim.get("timeline_id")
        candidates = set(by_ref.get((timeline, relation_ref), set()))
        for candidate in claims:
            if candidate is claim or candidate.get("timeline_id") != timeline:
                continue
            if _event_ref(candidate) == relation_ref and candidate.get("story_order") is not None:
                candidates.add(float(candidate["story_order"]))
        resolution = normalize_relative_expression(claim.get("temporal_anchor_text"))
        status = "resolved" if len(candidates) == 1 and resolution and resolution.is_exact else (
            "ambiguous" if len(candidates) > 1 else "unresolved"
        )
        edges.append({
            "from": node_id,
            "to_ref": relation_ref,
            "timeline_id": timeline,
            "status": status,
            "offset_min_seconds": resolution.offset_min_seconds if resolution else None,
            "offset_max_seconds": resolution.offset_max_seconds if resolution else None,
        })

    adjacency: dict[str, set[str]] = {}
    ref_nodes = {(node["timeline_id"], node["event_ref"]): node_id for node_id, node in nodes.items() if node["event_ref"]}
    for edge in edges:
        target = ref_nodes.get((edge["timeline_id"], edge["to_ref"]))
        if target:
            adjacency.setdefault(edge["from"], set()).add(target)
    cycles: list[list[str]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in visiting:
            cycles.append(path[path.index(node):] + [node])
            return
        if node in visited:
            return
        visiting.add(node)
        for child in adjacency.get(node, set()):
            visit(child, path + [child])
        visiting.remove(node)
        visited.add(node)

    for node in nodes:
        visit(node, [node])
    return {
        "nodes": nodes,
        "edges": edges,
        "cycles": cycles,
        "has_blocking_issue": bool(cycles) or any(e["status"] in {"ambiguous", "unresolved"} for e in edges),
    }


def _event_ref(claim: dict) -> Optional[str]:
    value = claim.get("temporal_event_ref")
    if not value:
        return None
    return re.sub(r"\s+", "", str(value)).casefold() or None


def is_globally_anchored(claim: dict) -> bool:
    """这条 claim 是否具备**全局**可比的时间锚点。

    四个条件同时成立才算：

    1. order_basis 属于 GLOBAL_ORDER_BASES（MVP 里只有 absolute_datetime）；
    2. order_confidence 达到阈值；
    3. temporal_anchor_value 能解析成绝对时间；
    4. timeline_id 明确 —— 跨时间线不比较（架构 4.3），线未知就无从比较。

    注意这里**不看** claim 自带的 story_order：那个字段由本模块写入，不是输入。
    """
    confidence = claim.get("order_confidence")
    if confidence is None or confidence < MIN_ORDER_CONFIDENCE:
        return False
    if not claim.get("timeline_id"):
        return False
    if claim.get("order_basis") in GLOBAL_ORDER_BASES:
        return parse_absolute_anchor(claim.get("temporal_anchor_value")) is not None
    return (
        claim.get("order_basis") == "relative_to_anchor"
        and claim.get("_resolved_relative") is True
        and claim.get("story_order") is not None
    )


def assign_story_orders(
    claims: list[dict],
    *,
    known_anchors: Optional[list[dict]] = None,
) -> list[dict]:
    """Resolve absolute anchors and unambiguous relative chains.

    这是 story_order 的**唯一**写入点。模型给的任何 story_order 都会被覆盖 ——
    它没有全局标尺可用，给出的必然是局部序号。

    返回新的 dict 列表，不修改入参。
    """
    assigned = []
    for claim in claims:
        positioned = dict(claim)
        positioned["_resolved_relative"] = False
        if (
            positioned.get("order_basis") in GLOBAL_ORDER_BASES
            and is_globally_anchored(positioned)
        ):
            positioned["story_order"] = parse_absolute_anchor(
                positioned["temporal_anchor_value"]
            )
        else:
            # 没有共同锚点 —— 保持 NULL，进待确认列表
            positioned["story_order"] = None
        assigned.append(positioned)

    # Accepted claims from earlier chapters provide the cross-chapter anchor
    # vocabulary. Only their persisted event label, timeline and computed
    # order are used; no model-created local ordinal is accepted here.
    external = [
        dict(anchor)
        for anchor in (known_anchors or [])
        if anchor.get("story_order") is not None and _event_ref(anchor)
    ]

    # Resolve chains iteratively. A relative claim can reference an absolute
    # anchor, a persisted anchor from another chapter, or a relative claim
    # resolved in an earlier pass.
    for _ in range(len(assigned)):
        changed = False
        for claim in assigned:
            if claim.get("story_order") is not None:
                continue
            if claim.get("order_basis") != "relative_to_anchor":
                continue
            confidence = claim.get("order_confidence")
            if confidence is None or confidence < MIN_ORDER_CONFIDENCE:
                continue
            timeline_id = claim.get("timeline_id")
            reference = re.sub(
                r"\s+", "", str(claim.get("temporal_relation_ref") or "")
            ).casefold()
            offset = parse_relative_offset(claim.get("temporal_anchor_text"))
            relation = claim.get("temporal_relation")
            if not timeline_id or not reference or offset is None:
                continue
            if relation == "after" and offset <= 0:
                continue
            if relation == "before" and offset >= 0:
                continue
            if relation == "simultaneous" and offset != 0:
                continue
            if relation not in {"before", "after", "simultaneous"}:
                continue

            matching_orders = {
                float(candidate["story_order"])
                for candidate in [*external, *assigned]
                if candidate is not claim
                and candidate.get("timeline_id") == timeline_id
                and candidate.get("story_order") is not None
                and reference == _event_ref(candidate)
            }
            # Multiple facts may describe the same event. They remain an
            # unambiguous anchor when their validated global order agrees.
            if len(matching_orders) != 1:
                continue
            claim["story_order"] = matching_orders.pop() + offset
            claim["_resolved_relative"] = True
            changed = True
        if not changed:
            break
    return assigned


__all__ = [
    "GLOBAL_ORDER_BASES",
    "MIN_ORDER_CONFIDENCE",
    "VALID_ORDER_BASES",
    "UnparseableAnchorError",
    "assign_story_orders",
    "is_globally_anchored",
    "parse_absolute_anchor",
    "parse_relative_offset",
    "RelativeTimeResolution",
    "normalize_relative_expression",
    "build_temporal_dependency_graph",
]
