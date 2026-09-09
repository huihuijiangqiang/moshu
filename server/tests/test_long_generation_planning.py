from services.long_generation import (
    build_context_manifest,
    context_budget_for_segment,
    make_segment_plan,
    next_segment_index,
    prompt_hash,
    split_target_words,
    validate_segment_output,
)


def test_split_million_words_preserves_total_and_minimum_tail():
    parts = split_target_words(1_000_000)
    assert sum(parts) == 1_000_000
    assert min(parts) >= 800
    assert max(parts) <= 32_000


def test_plan_distributes_beats_and_keeps_scene_purpose():
    plan = make_segment_plan(
        5_000,
        scene_purposes=["设局", "反制"],
        required_beats=["粮账证据", "里正试探", "粮商现身"],
        segment_words=2_400,
    )
    assert [item.target_words for item in plan] == [2_400, 2_600]
    assert plan[0].purpose == "设局"
    assert sum(len(item.required_beats) for item in plan) == 3


def test_context_budget_is_elastic_but_has_tested_ceiling():
    small = context_budget_for_segment(model_window_tokens=256_000, segment_target_words=1_000)
    large = context_budget_for_segment(model_window_tokens=1_000_000, segment_target_words=20_000)
    assert large == 208_000
    assert small > 200_000


def test_manifest_carries_revisions_and_recovery_index():
    plan = make_segment_plan(2_400)[0]
    manifest = build_context_manifest(
        chapter_id="ch1", segment=plan, source_revisions={"bodyRev": 3}, context_layers={"resident": 100}, open_threads=["t1"]
    )
    assert manifest["sourceRevisions"]["bodyRev"] == 3
    assert next_segment_index([
        {"segmentIndex": 0, "status": "accepted"},
        {"segmentIndex": 1, "status": "failed"},
    ]) == 1


def test_prompt_hash_distinguishes_segments():
    messages = [{"role": "user", "content": "same"}]
    assert prompt_hash(messages, segment_index=0) != prompt_hash(messages, segment_index=1)


def test_segment_gate_rejects_missing_fact_and_duplicate_boundary():
    check = validate_segment_output("新的正文", target_words=800, required_terms=["粮账"] , previous_tail="旧段落" * 40)
    assert check.blocking
    assert {item["id"] for item in check.checks} == {"non_empty", "length", "required_terms", "duplicate_boundary"}
