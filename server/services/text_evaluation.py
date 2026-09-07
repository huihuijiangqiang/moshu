"""Evaluation primitives for authorized real-text extraction corpora.

The corpus intentionally lives outside git.  A record contains one chapter of
authorized text plus human-reviewed claims, allowing dev/holdout runs to use
the production extraction provider without shipping copyrighted prose.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol


class TextCorpusError(ValueError):
    pass


class ClaimExtractor(Protocol):
    async def extract_claims(self, content_html: str, project_id: str, chapter_id: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class TextCase:
    case_id: str
    split: str
    text_html: str
    gold_claims: tuple[dict[str, Any], ...]


def _required_string(value: Any, field: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise TextCorpusError(f"{field} must be a non-empty string of at most {max_length} characters")
    return value.strip()


def _claim_key(claim: dict[str, Any]) -> tuple[str, ...]:
    """Return the semantic identity used for extraction scoring."""
    return (
        str(claim.get("subject_text") or "").strip().casefold(),
        str(claim.get("predicate") or "").strip().casefold(),
        str(claim.get("object_type") or "").strip().casefold(),
        str(claim.get("object_value") or "").strip().casefold(),
        str(claim.get("polarity") or "positive").strip().casefold(),
        str(claim.get("timeline_id") or "").strip().casefold(),
    )


def load_text_corpus(path: str | Path, *, split: str) -> list[TextCase]:
    """Load and validate one split, rejecting duplicate IDs and mixed records."""
    if split not in {"dev", "holdout"}:
        raise TextCorpusError("split must be dev or holdout")
    source = Path(path)
    if not source.is_file():
        raise TextCorpusError(f"corpus file does not exist: {source}")
    cases: list[TextCase] = []
    seen: set[str] = set()
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TextCorpusError(f"line {line_number}: invalid JSON") from exc
            if not isinstance(raw, dict):
                raise TextCorpusError(f"line {line_number}: record must be an object")
            case_id = _required_string(raw.get("id"), f"line {line_number}.id", max_length=120)
            record_split = _required_string(raw.get("split"), f"line {line_number}.split", max_length=20)
            if record_split != split:
                continue
            if case_id in seen:
                raise TextCorpusError(f"duplicate case id: {case_id}")
            text_html = _required_string(raw.get("text_html"), f"line {line_number}.text_html", max_length=2_000_000)
            raw_claims = raw.get("gold_claims")
            if not isinstance(raw_claims, list):
                raise TextCorpusError(f"line {line_number}.gold_claims must be a list")
            gold_claims: list[dict[str, Any]] = []
            keys: set[tuple[str, ...]] = set()
            for index, claim in enumerate(raw_claims):
                if not isinstance(claim, dict) or not _claim_key(claim)[0] or not _claim_key(claim)[1]:
                    raise TextCorpusError(f"line {line_number}.gold_claims[{index}] is not a valid claim")
                key = _claim_key(claim)
                if key in keys:
                    raise TextCorpusError(f"line {line_number}: duplicate gold claim")
                keys.add(key)
                gold_claims.append(dict(claim))
            seen.add(case_id)
            cases.append(TextCase(case_id, split, text_html, tuple(gold_claims)))
    if not cases:
        raise TextCorpusError(f"no {split} cases found in {source}")
    return cases


@dataclass
class TextEvaluation:
    split: str
    cases: int
    gold_claims: int
    predicted_claims: int
    true_positive: int
    false_positive: int
    false_negative: int
    evidence_matched: int
    evidence_candidates: int

    def metrics(self) -> dict[str, float | int]:
        precision = self.true_positive / self.predicted_claims if self.predicted_claims else 0.0
        recall = self.true_positive / self.gold_claims if self.gold_claims else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        evidence_accuracy = self.evidence_matched / self.evidence_candidates if self.evidence_candidates else 0.0
        return {
            "split": self.split,
            "cases": self.cases,
            "gold_claims": self.gold_claims,
            "predicted_claims": self.predicted_claims,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "evidence_accuracy": evidence_accuracy,
        }


async def evaluate_text_cases(cases: Iterable[TextCase], extractor: ClaimExtractor) -> TextEvaluation:
    case_list = list(cases)
    if not case_list:
        raise TextCorpusError("cannot evaluate an empty corpus")
    tp = fp = fn = evidence_matched = evidence_candidates = 0
    for case in case_list:
        predicted = await extractor.extract_claims(
            content_html=case.text_html,
            project_id=f"eval-{case.split}",
            chapter_id=case.case_id,
        )
        predicted_keys = {_claim_key(claim) for claim in predicted}
        gold_by_key = {_claim_key(claim): claim for claim in case.gold_claims}
        tp += len(predicted_keys & gold_by_key.keys())
        fp += len(predicted_keys - gold_by_key.keys())
        fn += len(gold_by_key.keys() - predicted_keys)
        evidence_candidates += len(predicted_keys & gold_by_key.keys())
        for key in predicted_keys & gold_by_key.keys():
            expected_anchor = str(gold_by_key[key].get("source_anchor") or "").strip()
            actual = next(claim for claim in predicted if _claim_key(claim) == key)
            if expected_anchor and actual.get("source_anchor") == expected_anchor:
                evidence_matched += 1
    return TextEvaluation(
        split=case_list[0].split,
        cases=len(case_list),
        gold_claims=sum(len(case.gold_claims) for case in case_list),
        predicted_claims=tp + fp,
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        evidence_matched=evidence_matched,
        evidence_candidates=evidence_candidates,
    )


__all__ = ["TextCase", "TextCorpusError", "TextEvaluation", "evaluate_text_cases", "load_text_corpus"]
