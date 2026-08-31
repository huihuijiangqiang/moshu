"""
claim 身份指纹测试 - 指纹回答的是「同一条事实的同一次出现吗」。

指纹既是跨块去重键，也是数据库唯一键。合并错了就是静默丢事实：两条独立事实被
并成一条，其中一条从此不存在，依赖它的冲突永远检不出来。
"""
from services.claim_identity import claim_fingerprint
from services.consistency import compute_claim_fingerprint


def fingerprint(**overrides) -> str:
    payload = {
        "subject_text": "李长风",
        "predicate": "alive",
        "object_type": "scalar",
        "object_value": "true",
        "polarity": "positive",
    }
    payload.update(overrides)
    return claim_fingerprint(**payload)


# --- 语义部分：老规矩不变 -----------------------------------------------------


def test_same_claim_gives_the_same_fingerprint():
    assert fingerprint() == fingerprint()
    assert len(fingerprint()) == 64


def test_case_and_whitespace_are_normalized():
    assert fingerprint(subject_text=" 李长风 ", predicate="Alive", object_value="TRUE") == (
        fingerprint()
    )


def test_semantic_differences_change_the_fingerprint():
    assert fingerprint(subject_text="陆青") != fingerprint()
    assert fingerprint(predicate="located_at") != fingerprint()
    assert fingerprint(object_value="false") != fingerprint()
    assert fingerprint(polarity="negative") != fingerprint()
    assert fingerprint(object_type="entity") != fingerprint()


# --- 时间线：同一句话在两条线上是两条事实 -------------------------------------


def test_same_statement_on_two_timelines_is_two_facts():
    """并行 POV 里「李长风还活着」在 A 线和 B 线各自成立。

    合并成一条，其中一条就永远看不见；跨线冲突也就永远检不出来（架构 4.3 要求
    多线叙事先按 timeline_id 隔离）。
    """
    assert fingerprint(timeline_id="line_a") != fingerprint(timeline_id="line_b")


def test_same_statement_on_the_same_timeline_is_one_fact():
    assert fingerprint(timeline_id="line_a") == fingerprint(timeline_id="line_a")


def test_unknown_timeline_is_its_own_identity_space():
    """线未知的 claim 之间照旧合并，行为与引入这个字段之前一致。"""
    assert fingerprint(timeline_id=None) == fingerprint(timeline_id="")
    assert fingerprint(timeline_id=None) != fingerprint(timeline_id="main")


# --- 来源锚点：同一条线上不同段落是两次出现 -----------------------------------


def test_same_fact_in_two_paragraphs_stays_two_occurrences():
    """同线不同段落的同语义陈述各自成行，作者才能分别定位。

    合并成一条只会保留其中一处的位置，另一处的告警指向错误的地方。
    """
    assert fingerprint(timeline_id="main", source_anchor="3") != fingerprint(
        timeline_id="main", source_anchor="17"
    )


def test_same_paragraph_seen_twice_is_one_occurrence():
    """重叠区域里同一段在相邻两块中拿到同一个全局段落号 —— 去重仍然成立。"""
    assert fingerprint(timeline_id="main", source_anchor="7") == fingerprint(
        timeline_id="main", source_anchor="7"
    )


def test_anchor_whitespace_is_normalized():
    assert fingerprint(source_anchor=" 7 ") == fingerprint(source_anchor="7")


def test_missing_anchor_is_its_own_identity_space():
    assert fingerprint(source_anchor=None) == fingerprint(source_anchor="")
    assert fingerprint(source_anchor=None) != fingerprint(source_anchor="0")


# --- 不参与身份的字段 ---------------------------------------------------------


def test_confidence_is_not_part_of_the_identity():
    """置信度是把握程度，不是身份。

    纳入它，模型每次把 0.9 微调成 0.85 都会插出一条新行，作者已有的处置记录
    随之失效。这里通过「函数根本不接受这个参数」来固定住。
    """
    import inspect

    parameters = inspect.signature(claim_fingerprint).parameters
    assert "confidence" not in parameters
    assert "order_confidence" not in parameters
    assert "story_order" not in parameters
    assert "order_basis" not in parameters


# --- 两条写入路径必须算出同一个指纹 -------------------------------------------


def test_service_and_provider_paths_agree():
    """抽取任务走 providers，服务层走 upsert_claim；指纹必须一致。

    各算各的哈希时，一点序列化差异（分隔符、ensure_ascii、None 与空串）就会让
    同一条事实得到两个指纹，唯一键形同虚设，同一版本里出现重复行。
    """
    from providers.consistency import claim_fingerprint as provider_fingerprint

    service_value = compute_claim_fingerprint(
        "李长风", "alive", "scalar", "true", "positive", "main", "7"
    )
    provider_value = provider_fingerprint(
        subject_text="李长风",
        predicate="alive",
        object_type="scalar",
        object_value="true",
        polarity="positive",
        timeline_id="main",
        source_anchor="7",
    )
    assert service_value == provider_value


def test_service_path_defaults_match_the_provider_defaults():
    """服务层不传时间线/锚点时，与 provider 的默认取值行为一致。"""
    assert compute_claim_fingerprint("李长风", "alive", "scalar", "true", "positive") == (
        fingerprint()
    )


def test_none_object_value_matches_empty_string():
    """object_value 缺失与空串是同一条事实 —— 早期两条路径在这里就分叉过。"""
    assert fingerprint(object_value=None) == fingerprint(object_value="")
