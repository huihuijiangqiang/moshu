"""Deterministic acceptance metrics for private long-form novel runs.

The manuscript and human annotations stay outside git.  This module only
defines the report format and calculations so the same private run can be
compared after retrieval, guard, or prompting changes.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any


class LongNovelEvaluationError(ValueError):
    pass


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LongNovelEvaluationError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise LongNovelEvaluationError(f"JSON root must be an object: {path}")
    return value


def _private_cases(path: str | Path | None, *, required: tuple[str, ...]) -> list[dict[str, Any]] | None:
    if path is None:
        return None
    source = Path(path)
    if not source.is_file():
        raise LongNovelEvaluationError(f"private annotation file does not exist: {source}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LongNovelEvaluationError(f"{source}:{line_number}: invalid JSON") from exc
        if not isinstance(row, dict) or any(not isinstance(row.get(field), list) for field in required):
            joined = ", ".join(required)
            raise LongNovelEvaluationError(f"{source}:{line_number}: expected list fields {joined}")
        rows.append(row)
    if not rows:
        raise LongNovelEvaluationError(f"private annotation file is empty: {source}")
    return rows


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def _retrieval_metrics(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    if rows is None:
        return _unavailable("no human-reviewed retrieval gold was supplied")
    recalls: list[float] = []
    hits = relevant = 0
    for row in rows:
        expected = {str(value) for value in row["relevant_chapters"]}
        k = max(1, int(row.get("k") or len(row["retrieved_chapters"]) or 1))
        actual = {str(value) for value in row["retrieved_chapters"][:k]}
        matched = len(expected & actual)
        hits += matched
        relevant += len(expected)
        recalls.append(matched / len(expected) if expected else 1.0)
    return {
        "status": "measured",
        "cases": len(rows),
        "macro_recall_at_k": round(statistics.fmean(recalls), 4),
        "micro_recall_at_k": round(hits / relevant, 4) if relevant else 1.0,
    }


def _guard_metrics(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    if rows is None:
        return _unavailable("no human-reviewed expected-conflict set was supplied")
    expected: set[str] = set()
    detected: set[str] = set()
    for row in rows:
        expected.update(str(value) for value in row["expected_conflicts"])
        detected.update(str(value) for value in row["detected_conflicts"])
    hits = len(expected & detected)
    return {
        "status": "measured",
        "cases": len(rows),
        "expected": len(expected),
        "detected": hits,
        "recall": round(hits / len(expected), 4) if expected else 1.0,
        "missed_ids": sorted(expected - detected),
    }


def _style_features(text: str) -> dict[str, float | int]:
    visible = re.sub(r"\s+", "", text)
    sentences = [item for item in re.split(r"[。！？!?]+", text) if item.strip()]
    paragraphs = [item.strip() for item in text.splitlines() if item.strip()]
    dialogue_chars = sum(len(item) for item in re.findall(r"[“「『](.*?)[”」』]", text, flags=re.DOTALL))
    return {
        "visible_chars": len(visible),
        "average_sentence_chars": round(len(visible) / max(1, len(sentences)), 2),
        "average_paragraph_chars": round(len(visible) / max(1, len(paragraphs)), 2),
        "dialogue_ratio": round(dialogue_chars / max(1, len(visible)), 4),
    }


def _relative_delta(value: float, baseline: float) -> float:
    return abs(value - baseline) / max(abs(baseline), 0.01)


def _style_drift(chapters: list[dict[str, Any]]) -> dict[str, Any]:
    if not chapters:
        return {"method": "surface-form-only", "baseline_chapters": 0, "flagged_chapters": []}
    baseline_rows = chapters[: min(3, len(chapters))]
    fields = ("average_sentence_chars", "average_paragraph_chars", "dialogue_ratio")
    baseline = {
        field: round(statistics.median(float(row["style"][field]) for row in baseline_rows), 4)
        for field in fields
    }
    flagged: list[dict[str, Any]] = []
    for chapter in chapters:
        deltas = {field: round(_relative_delta(float(chapter["style"][field]), baseline[field]), 4) for field in fields}
        if max(deltas.values(), default=0.0) >= 0.6:
            flagged.append({"chapter": chapter["number"], "relative_deltas": deltas})
    return {
        "method": "surface-form-only",
        "limitation": "flags rhythm/dialogue changes for review; it does not judge literary quality or authorship",
        "baseline_chapters": len(baseline_rows),
        "baseline": baseline,
        "flag_threshold": 0.6,
        "flagged_chapters": flagged,
    }


def evaluate_long_novel(
    output_dir: str | Path,
    *,
    target_chars: int = 100_000,
    retrieval_gold: str | Path | None = None,
    guard_gold: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(output_dir).resolve()
    checkpoint_path = root / "checkpoint.json"
    checkpoint = _load_json(checkpoint_path)
    raw_chapters = checkpoint.get("chapters")
    if not isinstance(raw_chapters, list):
        raise LongNovelEvaluationError("checkpoint.chapters must be a list")

    chapters: list[dict[str, Any]] = []
    hashes_ok = 0
    summaries = claims = embeddings = 0
    for raw in raw_chapters:
        if not isinstance(raw, dict) or not isinstance(raw.get("number"), int) or not isinstance(raw.get("file"), str):
            raise LongNovelEvaluationError("each completed chapter needs integer number and file")
        chapter_path = (root / raw["file"]).resolve()
        if root not in chapter_path.parents or not chapter_path.is_file():
            raise LongNovelEvaluationError(f"chapter file is missing or outside output directory: {raw['file']}")
        text = chapter_path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        expected_digest = raw.get("sha256")
        if not expected_digest or expected_digest == digest:
            hashes_ok += 1
        number = raw["number"]
        summary_file = root / "analysis" / f"{number:02d}-summary.txt"
        claims_file = root / "analysis" / f"{number:02d}-claims.json"
        embedding_file = root / "analysis" / f"{number:02d}-embedding.json"
        summaries += int(bool(str(raw.get("summary") or "").strip()) and summary_file.is_file())
        claims += int(isinstance(raw.get("claims"), list) and bool(raw["claims"]) and claims_file.is_file())
        embeddings += int(embedding_file.is_file() and embedding_file.stat().st_size > 2)
        chapters.append({"number": number, "style": _style_features(text)})

    planned = int(checkpoint.get("chapter_count") or len(checkpoint.get("plan", {}).get("chapters", [])) or len(chapters))
    visible_chars = sum(int(item["style"]["visible_chars"]) for item in chapters)
    usage = checkpoint.get("usage") if isinstance(checkpoint.get("usage"), dict) else {}
    completed = len(chapters)
    coverage_denominator = max(1, completed)
    retrieval_rows = _private_cases(retrieval_gold, required=("relevant_chapters", "retrieved_chapters"))
    guard_rows = _private_cases(guard_gold, required=("expected_conflicts", "detected_conflicts"))
    return {
        "report": "moshu-private-long-novel-v1",
        "source": {"checkpoint": str(checkpoint_path), "manuscript_in_report": False},
        "completion": {
            "planned_chapters": planned,
            "completed_chapters": completed,
            "visible_non_whitespace_characters": visible_chars,
            "target_characters": target_chars,
            "chapter_completion_ratio": _ratio(completed, planned),
            "target_reached": visible_chars >= target_chars and completed >= planned,
            "hashes_verified": hashes_ok,
        },
        "pipeline_coverage": {
            "summaries": {"covered": summaries, "ratio": _ratio(summaries, coverage_denominator)},
            "claims": {"covered": claims, "ratio": _ratio(claims, coverage_denominator)},
            "embeddings": {"covered": embeddings, "ratio": _ratio(embeddings, coverage_denominator)},
        },
        "retrieval": _retrieval_metrics(retrieval_rows),
        "guard": _guard_metrics(guard_rows),
        "style_drift": _style_drift(chapters),
        "usage": {
            "model": checkpoint.get("model"),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "credits": _unavailable("this checkpoint was generated outside the platform billing ledger"),
            "stage_timings": _unavailable("the legacy checkpoint did not record per-stage timings"),
        },
    }


__all__ = ["LongNovelEvaluationError", "evaluate_long_novel"]
