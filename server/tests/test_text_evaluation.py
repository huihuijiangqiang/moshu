import json

import pytest

from services.text_evaluation import TextCorpusError, evaluate_text_cases, load_text_corpus


def _write(path, records):
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


class FakeExtractor:
    def __init__(self, claims):
        self.claims = claims
        self.calls = []

    async def extract_claims(self, **kwargs):
        self.calls.append(kwargs)
        return self.claims


def test_load_text_corpus_keeps_requested_split_and_rejects_duplicates(tmp_path):
    path = tmp_path / "corpus.jsonl"
    _write(
        path,
        [
            {"id": "dev-1", "split": "dev", "text_html": "<p>[P0]她活着。</p>", "gold_claims": []},
            {"id": "holdout-1", "split": "holdout", "text_html": "<p>[P0]他离开。</p>", "gold_claims": []},
        ],
    )
    assert [case.case_id for case in load_text_corpus(path, split="dev")] == ["dev-1"]
    assert [case.case_id for case in load_text_corpus(path, split="holdout")] == ["holdout-1"]

    _write(path, [{"id": "dev-1", "split": "dev", "text_html": "x", "gold_claims": []}] * 2)
    with pytest.raises(TextCorpusError, match="duplicate"):
        load_text_corpus(path, split="dev")


@pytest.mark.asyncio
async def test_evaluation_reports_semantic_and_evidence_metrics(tmp_path):
    path = tmp_path / "corpus.jsonl"
    gold = {
        "subject_text": "沈青禾",
        "predicate": "alive",
        "object_type": "scalar",
        "object_value": "true",
        "polarity": "positive",
        "source_anchor": "P0",
    }
    _write(path, [{"id": "dev-1", "split": "dev", "text_html": "x", "gold_claims": [gold]}])
    cases = load_text_corpus(path, split="dev")
    extractor = FakeExtractor([{**gold, "confidence": 0.9}])
    result = await evaluate_text_cases(cases, extractor)
    metrics = result.metrics()
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["evidence_accuracy"] == 1.0
    assert extractor.calls[0]["chapter_id"] == "dev-1"


@pytest.mark.asyncio
async def test_evaluation_counts_unmatched_predictions_and_missing_gold(tmp_path):
    path = tmp_path / "corpus.jsonl"
    gold = {
        "subject_text": "甲", "predicate": "alive", "object_type": "scalar", "object_value": "true"
    }
    _write(path, [{"id": "dev-1", "split": "dev", "text_html": "x", "gold_claims": [gold]}])
    cases = load_text_corpus(path, split="dev")
    predicted = [{"subject_text": "乙", "predicate": "alive", "object_type": "scalar", "object_value": "true"}]
    result = await evaluate_text_cases(cases, FakeExtractor(predicted))
    assert result.metrics()["false_positive"] == 1
    assert result.metrics()["false_negative"] == 1
    assert result.metrics()["f1"] == 0.0
