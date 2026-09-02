"""
时间线服务测试 - story_order 只能来自已确认的全局锚点。

这里守的是架构 4.3 的一条硬约束：story_order 是故事世界中的事件顺序，必须有
**跨块跨章共同的标尺**。抽取逐块进行，模型在单块里给出的任何序号都只在块内自洽；
规则扫描却是全项目范围的，会把这些数字直接排在一起。所以本文件里最重要的一组
测试是「局部序号不得升格成全局序号」。
"""
import pytest

from services.consistency import assign_narrative_positions, is_order_reliable
from services.timeline import (
    GLOBAL_ORDER_BASES,
    MIN_ORDER_CONFIDENCE,
    VALID_ORDER_BASES,
    assign_story_orders,
    is_globally_anchored,
    parse_absolute_anchor,
    parse_relative_offset,
)


def anchored_claim(**overrides) -> dict:
    """一条具备全局锚点的 claim。"""
    claim = {
        "subject_text": "李长风",
        "predicate": "alive",
        "object_type": "scalar",
        "object_value": "true",
        "polarity": "positive",
        "timeline_id": "main",
        "order_basis": "absolute_datetime",
        "order_confidence": 0.95,
        "temporal_anchor_text": "元和七年冬月初三",
        "temporal_anchor_value": "0812-11-03",
    }
    claim.update(overrides)
    return claim


# --- 锚点解析 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "value,offset_seconds",
    [
        ("2024-03-01", 0),
        ("2024-03-01 00:00", 0),
        ("2024-03-01T00:00", 0),
        ("2024-03-01T00:00:00", 0),
        ("2024-03-01 18:30", 18 * 3600 + 30 * 60),
        ("2024-03-01T18:30", 18 * 3600 + 30 * 60),
        ("2024-03-01T18:30:45", 18 * 3600 + 30 * 60 + 45),
    ],
)
def test_iso_shapes_parse_to_unix_seconds(value, offset_seconds):
    """支持的 ISO 形状都解析成同一标尺上的 Unix 秒（按 UTC，不受运行机器时区影响）。"""
    midnight = parse_absolute_anchor("2024-03-01")
    assert parse_absolute_anchor(value) == midnight + offset_seconds


def test_more_precise_anchor_is_larger_within_the_same_day():
    """同一天里时刻越晚，story_order 越大 —— 标尺来自时间本身。"""
    morning = parse_absolute_anchor("2024-03-01T06:00")
    evening = parse_absolute_anchor("2024-03-01T21:00")
    assert morning < evening


def test_anchor_scale_is_shared_across_chunks():
    """两个块各自解析出的绝对时间可以直接比较 —— 这正是「共同标尺」的含义。"""
    from_chunk_one = parse_absolute_anchor("0812-11-03")
    from_chunk_seven = parse_absolute_anchor("0812-11-05")
    assert from_chunk_one < from_chunk_seven


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "三日后的清晨", "元和七年冬", "later", "2024", "2024-03", "3"],
)
def test_non_absolute_values_do_not_parse(value):
    """自然语言、残缺日期、纯数字都不算绝对时间 —— 返回 None，不猜。"""
    assert parse_absolute_anchor(value) is None


def test_impossible_date_does_not_parse():
    """形状像 ISO 但日期不存在时也返回 None，不抛错。"""
    assert parse_absolute_anchor("2024-02-31") is None


def test_unparseable_anchor_does_not_raise():
    """一条锚点解析不了不该让整章抽取失败。"""
    assert parse_absolute_anchor("紧接着") is None


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("三日后", 3 * 86400),
        ("两小时前", -2 * 3600),
        ("半个时辰后", 3600),
        ("1.5小时后", 90 * 60),
        ("12分钟后", 12 * 60),
        ("一百零二天前", -102 * 86400),
    ],
)
def test_relative_duration_parser_is_deterministic(text, seconds):
    assert parse_relative_offset(text) == seconds


@pytest.mark.parametrize(
    "text",
    [None, "", "过几日", "次日", "三日左右", "很久以后", "一月后", "一年后", "0天后"],
)
def test_vague_relative_durations_are_rejected(text):
    assert parse_relative_offset(text) is None


# --- 全局锚点判定 -------------------------------------------------------------


def test_absolute_datetime_is_the_only_global_basis():
    """MVP 限制固定在这里：只有 absolute_datetime 提供全局标尺。"""
    assert GLOBAL_ORDER_BASES == frozenset({"absolute_datetime"})
    assert set(VALID_ORDER_BASES) == {
        "absolute_datetime",
        "relative_to_anchor",
        "narration_local",
        "unknown",
    }


def test_claim_with_absolute_anchor_is_globally_anchored():
    assert is_globally_anchored(anchored_claim()) is True


@pytest.mark.parametrize("basis", ["relative_to_anchor", "narration_local", "unknown"])
def test_non_absolute_bases_are_never_globally_anchored(basis):
    """即便带着一个可解析的时间值，非绝对依据也不算全局锚定。

    依据说明的是模型凭什么给出这个时间；依据是「相对」或「块内叙述」时，那个值
    没有被正文钉死，不能当共同标尺用。
    """
    claim = anchored_claim(order_basis=basis)
    assert is_globally_anchored(claim) is False


def test_low_confidence_anchor_is_not_accepted():
    claim = anchored_claim(order_confidence=MIN_ORDER_CONFIDENCE - 0.01)
    assert is_globally_anchored(claim) is False


def test_missing_confidence_is_not_accepted():
    claim = anchored_claim(order_confidence=None)
    assert is_globally_anchored(claim) is False


def test_unparseable_anchor_value_is_not_accepted():
    """模型说是绝对时间，但值归一化不了 —— 不采信它的自述。"""
    claim = anchored_claim(temporal_anchor_value="元和七年冬")
    assert is_globally_anchored(claim) is False


def test_missing_timeline_is_not_accepted():
    """线未知就无从比较（架构 4.3：多线叙事先按 timeline_id 隔离）。"""
    assert is_globally_anchored(anchored_claim(timeline_id=None)) is False
    assert is_globally_anchored(anchored_claim(timeline_id="")) is False


def test_a_supplied_story_order_does_not_make_a_claim_anchored():
    """claim 自带 story_order 不是输入 —— 判定只看证据。"""
    claim = anchored_claim(
        order_basis="narration_local",
        temporal_anchor_value=None,
        story_order=3.0,
    )
    assert is_globally_anchored(claim) is False


# --- 分配 story_order ---------------------------------------------------------


def test_absolute_anchor_gets_a_story_order():
    [assigned] = assign_story_orders([anchored_claim()])
    assert assigned["story_order"] == parse_absolute_anchor("0812-11-03")


def test_assignment_overwrites_whatever_the_model_supplied():
    """模型给的 story_order 一律被覆盖 —— 它没有全局标尺可用。"""
    [assigned] = assign_story_orders([anchored_claim(story_order=99.0)])
    assert assigned["story_order"] == parse_absolute_anchor("0812-11-03")
    assert assigned["story_order"] != 99.0


@pytest.mark.parametrize("basis", ["relative_to_anchor", "narration_local", "unknown"])
def test_claims_without_a_global_anchor_keep_a_null_order(basis):
    """没有共同锚点保持 NULL（架构 4.3），claim 仍然照常落库。"""
    [assigned] = assign_story_orders(
        [anchored_claim(order_basis=basis, story_order=7.0, temporal_anchor_value=None)]
    )
    assert assigned["story_order"] is None
    assert assigned["subject_text"] == "李长风"


def test_assignment_does_not_mutate_the_input():
    original = anchored_claim(story_order=None)
    assign_story_orders([original])
    assert original["story_order"] is None


def test_relative_evidence_is_preserved_even_though_it_yields_no_order():
    """相对表述不分配顺序，但证据必须留下来供后续解析与人工确认。"""
    [assigned] = assign_story_orders(
        [
            anchored_claim(
                order_basis="relative_to_anchor",
                temporal_anchor_value=None,
                temporal_anchor_text="三日后",
                temporal_relation="after",
                temporal_relation_ref="李长风下山",
            )
        ]
    )
    assert assigned["story_order"] is None
    assert assigned["temporal_anchor_text"] == "三日后"
    assert assigned["temporal_relation"] == "after"
    assert assigned["temporal_relation_ref"] == "李长风下山"


def test_relative_anchor_resolves_against_one_exact_event_reference():
    base, relative = assign_story_orders(
        [
            anchored_claim(temporal_event_ref="李长风下山"),
            anchored_claim(
                subject_text="陆青",
                order_basis="relative_to_anchor",
                temporal_anchor_value=None,
                temporal_anchor_text="三日后",
                temporal_relation="after",
                temporal_relation_ref="李长风下山",
            ),
        ]
    )
    assert relative["story_order"] == base["story_order"] + 3 * 86400
    assert is_order_reliable(relative) is True


def test_relative_anchor_resolves_against_a_persisted_cross_chapter_event():
    [relative] = assign_story_orders(
        [
            anchored_claim(
                subject_text="陆青",
                order_basis="relative_to_anchor",
                temporal_anchor_value=None,
                temporal_anchor_text="三日后",
                temporal_relation="after",
                temporal_relation_ref="李长风下山",
            )
        ],
        known_anchors=[
            {
                "timeline_id": "main",
                "story_order": parse_absolute_anchor("0812-11-03"),
                "temporal_event_ref": "李长风下山",
            }
        ],
    )

    assert relative["story_order"] == parse_absolute_anchor("0812-11-06")
    assert is_order_reliable(relative) is True


def test_duplicate_facts_for_the_same_persisted_event_are_not_ambiguous():
    base_order = parse_absolute_anchor("0812-11-03")
    [relative] = assign_story_orders(
        [
            anchored_claim(
                order_basis="relative_to_anchor",
                temporal_anchor_value=None,
                temporal_anchor_text="一日后",
                temporal_relation="after",
                temporal_relation_ref="启程",
            )
        ],
        known_anchors=[
            {"timeline_id": "main", "story_order": base_order, "temporal_event_ref": "启程"},
            {"timeline_id": "main", "story_order": base_order, "temporal_event_ref": "启程"},
        ],
    )

    assert relative["story_order"] == base_order + 86400


def test_event_reference_matching_normalizes_whitespace_and_case():
    base_order = parse_absolute_anchor("2024-01-01")
    [relative] = assign_story_orders(
        [
            anchored_claim(
                order_basis="relative_to_anchor",
                temporal_anchor_value=None,
                temporal_anchor_text="一日后",
                temporal_relation="after",
                temporal_relation_ref="  ARRIVE   HOME ",
            )
        ],
        known_anchors=[
            {
                "timeline_id": "main",
                "story_order": base_order,
                "temporal_event_ref": "Arrive Home",
            }
        ],
    )

    assert relative["story_order"] == base_order + 86400


def test_relative_anchor_chain_resolves_in_multiple_passes():
    base, second, third = assign_story_orders(
        [
            anchored_claim(temporal_event_ref="启程"),
            anchored_claim(
                subject_text="第二件事",
                order_basis="relative_to_anchor",
                temporal_anchor_value=None,
                temporal_anchor_text="两日后",
                temporal_relation="after",
                temporal_relation_ref="启程",
                temporal_event_ref="抵达",
            ),
            anchored_claim(
                subject_text="第三件事",
                order_basis="relative_to_anchor",
                temporal_anchor_value=None,
                temporal_anchor_text="三小时前",
                temporal_relation="before",
                temporal_relation_ref="抵达",
            ),
        ]
    )
    assert second["story_order"] == base["story_order"] + 2 * 86400
    assert third["story_order"] == second["story_order"] - 3 * 3600


def test_ambiguous_or_cross_timeline_relative_reference_stays_null():
    assigned = assign_story_orders(
        [
            anchored_claim(temporal_event_ref="启程"),
            anchored_claim(temporal_event_ref="启程", temporal_anchor_value="0812-11-04"),
            anchored_claim(
                subject_text="歧义引用",
                order_basis="relative_to_anchor",
                temporal_anchor_value=None,
                temporal_anchor_text="一日后",
                temporal_relation="after",
                temporal_relation_ref="启程",
            ),
            anchored_claim(
                subject_text="跨线引用",
                timeline_id="line_b",
                order_basis="relative_to_anchor",
                temporal_anchor_value=None,
                temporal_anchor_text="一日后",
                temporal_relation="after",
                temporal_relation_ref="启程",
            ),
        ]
    )
    assert assigned[2]["story_order"] is None
    assert assigned[3]["story_order"] is None


def test_relation_direction_must_match_the_duration_text():
    _, relative = assign_story_orders(
        [
            anchored_claim(temporal_event_ref="启程"),
            anchored_claim(
                order_basis="relative_to_anchor",
                temporal_anchor_value=None,
                temporal_anchor_text="一日前",
                temporal_relation="after",
                temporal_relation_ref="启程",
            ),
        ]
    )
    assert relative["story_order"] is None


def test_two_chunks_with_absolute_anchors_are_comparable():
    """两个块各自锚定绝对时间时，分配出的顺序可以跨块比较。"""
    early, late = assign_story_orders(
        [
            anchored_claim(temporal_anchor_value="0812-11-03", chunk_index=0),
            anchored_claim(temporal_anchor_value="0812-11-09", chunk_index=6),
        ]
    )
    assert early["story_order"] < late["story_order"]


# --- MVP 限制：局部序号绝不升格成全局序号 -------------------------------------


def test_local_narration_numbers_from_two_chunks_are_not_comparable():
    """两个块各自的「块内第 1、第 2 条」不会被排成一条全局序列。

    这是 C 项的核心：旧提示词让每个块「use any increasing numbers」，于是块 0 的
    2.0 和块 5 的 1.0 被全项目规则直接比较，得出「块 5 的事件更早」这种凭空结论。
    现在两条都拿不到 story_order。
    """
    assigned = assign_story_orders(
        [
            anchored_claim(
                subject_text="李长风",
                order_basis="narration_local",
                temporal_anchor_value=None,
                story_order=2.0,
                chunk_index=0,
            ),
            anchored_claim(
                subject_text="陆青",
                order_basis="narration_local",
                temporal_anchor_value=None,
                story_order=1.0,
                chunk_index=5,
            ),
        ]
    )
    assert [claim["story_order"] for claim in assigned] == [None, None]


def test_a_locally_ordered_claim_never_reaches_the_time_dependent_rules():
    """局部序号过不了 is_order_reliable —— 依赖时序的硬规则拿不到它。"""
    claim = anchored_claim(
        order_basis="narration_local", temporal_anchor_value=None, story_order=1.0
    )
    [assigned] = assign_story_orders([claim])
    assert is_order_reliable(assigned) is False


def test_local_order_cannot_close_an_interval_against_an_anchored_claim():
    """块内序号不会与绝对锚定的 claim 混在一起闭合区间。

    混在一起的后果是：一条没有全局位置的 claim 会给另一条的有效区间划上终点，
    ownership / alive 规则随即在错误的区间上判定冲突。
    """
    positioned = assign_narrative_positions(
        assign_story_orders(
            [
                anchored_claim(
                    predicate="owns",
                    object_type="entity",
                    object_value="断水剑",
                    temporal_anchor_value="0812-11-03",
                ),
                anchored_claim(
                    predicate="owns",
                    object_type="entity",
                    object_value="断水剑",
                    order_basis="narration_local",
                    temporal_anchor_value=None,
                    story_order=999.0,
                ),
            ]
        )
    )
    anchored, local = positioned
    assert local["story_order"] is None
    assert local["valid_from_order"] is None
    assert local["valid_to_order"] is None
    # 唯一一条有全局位置的 claim 找不到后继，区间保持开放
    assert anchored["valid_from_order"] == parse_absolute_anchor("0812-11-03")
    assert anchored["valid_to_order"] is None


def test_anchored_claims_on_the_same_timeline_do_close_intervals():
    """有共同锚点时区间照常闭合 —— 限制的是伪造，不是功能。"""
    positioned = assign_narrative_positions(
        assign_story_orders(
            [
                anchored_claim(
                    predicate="owns",
                    object_type="entity",
                    object_value="断水剑",
                    temporal_anchor_value="0812-11-03",
                ),
                anchored_claim(
                    predicate="owns",
                    object_type="entity",
                    object_value="断水剑",
                    temporal_anchor_value="0812-11-09",
                ),
            ]
        )
    )
    earlier, later = positioned
    assert earlier["valid_to_order"] == later["valid_from_order"]
    assert later["valid_to_order"] is None


def test_anchored_claims_on_different_timelines_do_not_close_intervals():
    """跨线不比较（架构 4.3）—— 两条线各自的区间互不影响。"""
    positioned = assign_narrative_positions(
        assign_story_orders(
            [
                anchored_claim(
                    predicate="owns",
                    object_type="entity",
                    object_value="断水剑",
                    timeline_id="line_a",
                    temporal_anchor_value="0812-11-03",
                ),
                anchored_claim(
                    predicate="owns",
                    object_type="entity",
                    object_value="断水剑",
                    timeline_id="line_b",
                    temporal_anchor_value="0812-11-09",
                ),
            ]
        )
    )
    assert all(claim["valid_to_order"] is None for claim in positioned)
