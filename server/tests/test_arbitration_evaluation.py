import json

import pytest

from providers.consistency import ArbitrationDecision
from services.arbitration_evaluation import (
    ArbitrationCorpusError,
    evaluate_arbitration_cases,
    load_arbitration_corpus,
)


def test_arbitration_corpus_keeps_split_and_validates_case_id(tmp_path):
    path = tmp_path / "arbitration.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "dev-1",
                "split": "dev",
                "gold_verdict": "supported",
                "case": {"case_id": "dev-1", "issue_type": "alive_conflict", "evidence": []},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert load_arbitration_corpus(path, split="dev")[0].case_id == "dev-1"
    with pytest.raises(ArbitrationCorpusError, match="no holdout"):
        load_arbitration_corpus(path, split="holdout")


class FakeArbitrator:
    async def arbitrate_conflicts(self, cases):
        return {
            case["case_id"]: ArbitrationDecision(
                case_id=case["case_id"],
                verdict="supported" if case["case_id"] == "dev-1" else "uncertain",
                confidence=0.9,
                rationale="证据明确",
            )
            for case in cases
        }


@pytest.mark.asyncio
async def test_arbitration_metrics_include_confusion_and_failure_rate(tmp_path):
    path = tmp_path / "arbitration.jsonl"
    records = [
        {"id": "dev-1", "split": "dev", "gold_verdict": "supported", "case": {"case_id": "dev-1"}},
        {"id": "dev-2", "split": "dev", "gold_verdict": "unsupported", "case": {"case_id": "dev-2"}},
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    result = await evaluate_arbitration_cases(load_arbitration_corpus(path, split="dev"), FakeArbitrator())
    metrics = result.metrics()
    assert metrics["total"] == 2
    assert metrics["correct"] == 1
    assert metrics["failed"] == 0
    assert metrics["confusion"]["unsupported"]["uncertain"] == 1
