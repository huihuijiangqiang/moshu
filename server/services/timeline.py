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

MVP 范围限制（有意为之，不是遗漏）
----------------------------------
本版只支持一种全局锚点：**明确的绝对时间**。绝对时间天然有共同标尺，跨块、跨章
都可比。除此之外一律保持 NULL：

* ``relative_to_anchor``：「三天后」这类相对表述需要先确认它锚在哪个绝对时间上，
  解析链尚未实现，因此不分配 story_order（保留证据供后续解析与人工确认）。
* ``narration_local``：块内叙述顺序。这**恰恰是不能用**的那一类 —— 它只在块内
  有意义，跨块不可比。见 tests/test_timeline.py 里的对应测试。
* ``unknown``：模型自己都说不确定。

也就是说：MVP 下只有正文里写明了时间的事件才会进硬规则。这会漏报，但不会误报，
而误报（把正常倒叙判成矛盾）对作者的伤害大得多。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

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


def is_globally_anchored(claim: dict) -> bool:
    """这条 claim 是否具备**全局**可比的时间锚点。

    四个条件同时成立才算：

    1. order_basis 属于 GLOBAL_ORDER_BASES（MVP 里只有 absolute_datetime）；
    2. order_confidence 达到阈值；
    3. temporal_anchor_value 能解析成绝对时间；
    4. timeline_id 明确 —— 跨时间线不比较（架构 4.3），线未知就无从比较。

    注意这里**不看** claim 自带的 story_order：那个字段由本模块写入，不是输入。
    """
    if claim.get("order_basis") not in GLOBAL_ORDER_BASES:
        return False
    confidence = claim.get("order_confidence")
    if confidence is None or confidence < MIN_ORDER_CONFIDENCE:
        return False
    if not claim.get("timeline_id"):
        return False
    return parse_absolute_anchor(claim.get("temporal_anchor_value")) is not None


def assign_story_orders(claims: list[dict]) -> list[dict]:
    """按已确认的全局锚点分配 story_order；其余保持 NULL。

    这是 story_order 的**唯一**写入点。模型给的任何 story_order 都会被覆盖 ——
    它没有全局标尺可用，给出的必然是局部序号。

    返回新的 dict 列表，不修改入参。
    """
    assigned = []
    for claim in claims:
        positioned = dict(claim)
        if is_globally_anchored(positioned):
            positioned["story_order"] = parse_absolute_anchor(
                positioned["temporal_anchor_value"]
            )
        else:
            # 没有共同锚点 —— 保持 NULL，进待确认列表
            positioned["story_order"] = None
        assigned.append(positioned)
    return assigned


__all__ = [
    "GLOBAL_ORDER_BASES",
    "MIN_ORDER_CONFIDENCE",
    "VALID_ORDER_BASES",
    "UnparseableAnchorError",
    "assign_story_orders",
    "is_globally_anchored",
    "parse_absolute_anchor",
]
