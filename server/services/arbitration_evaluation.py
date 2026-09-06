"""Gold-set evaluation helpers for the LLM conflict-arbitration stage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from providers.consistency import ArbitrationDecision

VERDICTS = ("supported", "unsupported", "uncertain")


class ArbitrationCorpusError(ValueError):
    pass


class ConflictArbitrator(Protocol):
    async def arbitrate_conflicts(self, cases: list[dict[str, Any]]) -> dict[str, ArbitrationDecision]: ...


@dataclass(frozen=True)
class ArbitrationCase:
    case_id: str
    split: str
    payload: dict[str, Any]
    gold_verdict: str


def load_arbitration_corpus(path: str | Path, *, split: str) -> list[ArbitrationCase]:
    if split not in {"dev", "holdout"}:
        raise ArbitrationCorpusError("split must be dev or holdout")
    source = Path(path)
    if not source.is_file():
        raise ArbitrationCorpusError(f"corpus file does not exist: {source}")
    cases: list[ArbitrationCase] = []
    seen: set[str] = set()
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ArbitrationCorpusError(f"line {line_number}: invalid JSON") from exc
            if not isinstance(raw, dict):
                raise ArbitrationCorpusError(f"line {line_number}: record must be an object")
            case_id = raw.get("id")
            record_split = raw.get("split")
            verdict = raw.get("gold_verdict")
            payload = raw.get("case")
            if not isinstance(case_id, str) or not case_id.strip() or len(case_id) > 120:
                raise ArbitrationCorpusError(f"line {line_number}.id is invalid")
            if record_split != split:
                continue
            if case_id in seen:
                raise ArbitrationCorpusError(f"duplicate case id: {case_id}")
            if verdict not in VERDICTS:
                raise ArbitrationCorpusError(f"line {line_number}.gold_verdict is invalid")
            if not isinstance(payload, dict) or payload.get("case_id") != case_id:
                raise ArbitrationCorpusError(f"line {line_number}.case must carry the same case_id")
            seen.add(case_id)
            cases.append(ArbitrationCase(case_id.strip(), split, dict(payload), verdict))
    if not cases:
        raise ArbitrationCorpusError(f"no {split} cases found in {source}")
    return cases


@dataclass
class ArbitrationEvaluation:
    split: str
    total: int
    correct: int
    failed: int
    confusion: dict[str, dict[str, int]]

    def metrics(self) -> dict[str, Any]:
        accuracy = self.correct / self.total if self.total else 0.0
        macro: dict[str, float] = {}
        for verdict in VERDICTS:
            row = self.confusion[verdict]
            support = sum(row.values())
            macro[verdict] = row[verdict] / support if support else 0.0
        return {
            "split": self.split,
            "total": self.total,
            "correct": self.correct,
            "failed": self.failed,
            "failure_rate": self.failed / self.total if self.total else 0.0,
            "accuracy": accuracy,
            "macro_recall": sum(macro.values()) / len(VERDICTS),
            "recall_by_verdict": macro,
            "confusion": self.confusion,
        }


async def evaluate_arbitration_cases(
    cases: list[ArbitrationCase], arbitrator: ConflictArbitrator
) -> ArbitrationEvaluation:
    if not cases:
        raise ArbitrationCorpusError("cannot evaluate an empty corpus")
    decisions = await arbitrator.arbitrate_conflicts([case.payload for case in cases])
    confusion = {gold: {predicted: 0 for predicted in VERDICTS} for gold in VERDICTS}
    correct = failed = 0
    for case in cases:
        decision = decisions.get(case.case_id)
        if decision is None or decision.verdict not in VERDICTS:
            failed += 1
            continue
        confusion[case.gold_verdict][decision.verdict] += 1
        correct += int(decision.verdict == case.gold_verdict)
    return ArbitrationEvaluation(case_list_split(cases), len(cases), correct, failed, confusion)


def case_list_split(cases: list[ArbitrationCase]) -> str:
    splits = {case.split for case in cases}
    if len(splits) != 1:
        raise ArbitrationCorpusError("evaluation cases must belong to one split")
    return cases[0].split


__all__ = [
    "ArbitrationCase",
    "ArbitrationCorpusError",
    "ArbitrationEvaluation",
    "evaluate_arbitration_cases",
    "load_arbitration_corpus",
]
