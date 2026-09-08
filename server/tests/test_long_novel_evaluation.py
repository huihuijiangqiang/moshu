import hashlib
import json

import pytest

from services.long_novel_evaluation import LongNovelEvaluationError, evaluate_long_novel


def _write_run(tmp_path):
    (tmp_path / "chapters").mkdir()
    (tmp_path / "analysis").mkdir()
    chapters = []
    for number, text in enumerate(("“先走。”她推门出去。\n天色将明。", "他停了很久，才说：“账目对得上。”\n雨落在窗纸上。"), start=1):
        relative = f"chapters/{number:02d}.md"
        (tmp_path / relative).write_text(text, encoding="utf-8")
        (tmp_path / "analysis" / f"{number:02d}-summary.txt").write_text("摘要", encoding="utf-8")
        (tmp_path / "analysis" / f"{number:02d}-claims.json").write_text('[{"predicate":"said"}]', encoding="utf-8")
        (tmp_path / "analysis" / f"{number:02d}-embedding.json").write_text("[0.1, 0.2]", encoding="utf-8")
        chapters.append({
            "number": number, "file": relative, "summary": "摘要", "claims": [{"predicate": "said"}],
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
    checkpoint = {
        "model": "test-model", "chapter_count": 2, "chapters": chapters,
        "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
    }
    (tmp_path / "checkpoint.json").write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")


def test_reports_coverage_usage_and_explicitly_unavailable_gold_metrics(tmp_path):
    _write_run(tmp_path)
    report = evaluate_long_novel(tmp_path, target_chars=10)

    assert report["completion"] == pytest.approx({
        "planned_chapters": 2, "completed_chapters": 2,
        "visible_non_whitespace_characters": 40, "target_characters": 10,
        "chapter_completion_ratio": 1, "target_reached": True, "hashes_verified": 2,
    })
    assert report["pipeline_coverage"]["embeddings"]["ratio"] == 1
    assert report["retrieval"]["status"] == "unavailable"
    assert report["guard"]["status"] == "unavailable"
    assert report["usage"]["total_tokens"] == 140
    assert report["style_drift"]["method"] == "surface-form-only"


def test_scores_only_supplied_human_gold(tmp_path):
    _write_run(tmp_path)
    retrieval = tmp_path / "retrieval.jsonl"
    retrieval.write_text(
        json.dumps({"relevant_chapters": ["c1", "c2"], "retrieved_chapters": ["c1", "c9"], "k": 2}) + "\n",
        encoding="utf-8",
    )
    guard = tmp_path / "guard.jsonl"
    guard.write_text(
        json.dumps({"expected_conflicts": ["money", "place"], "detected_conflicts": ["money"]}) + "\n",
        encoding="utf-8",
    )

    report = evaluate_long_novel(tmp_path, retrieval_gold=retrieval, guard_gold=guard)
    assert report["retrieval"]["macro_recall_at_k"] == 0.5
    assert report["guard"]["recall"] == 0.5
    assert report["guard"]["missed_ids"] == ["place"]


def test_rejects_chapter_paths_outside_private_run(tmp_path):
    outside = tmp_path.parent / "outside.md"
    outside.write_text("private", encoding="utf-8")
    (tmp_path / "checkpoint.json").write_text(
        json.dumps({"chapters": [{"number": 1, "file": "../outside.md"}]}),
        encoding="utf-8",
    )
    with pytest.raises(LongNovelEvaluationError, match="outside output directory"):
        evaluate_long_novel(tmp_path)
