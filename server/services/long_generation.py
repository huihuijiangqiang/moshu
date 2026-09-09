"""Planning and validation primitives for million-character novels.

This module intentionally has no network or database side effects.  API/task
layers can persist its plans and execute one segment at a time, while tests can
exercise recovery and quality gates deterministically.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable


MAX_LONG_WORDS = 1_000_000
MIN_SEGMENT_WORDS = 800
DEFAULT_SEGMENT_WORDS = 2_400
MAX_SEGMENT_WORDS = 32_000


@dataclass(frozen=True)
class SegmentPlan:
    index: int
    target_words: int
    purpose: str
    required_beats: tuple[str, ...] = ()


def split_target_words(target_words: int, *, segment_words: int = DEFAULT_SEGMENT_WORDS) -> list[int]:
    """Split a long target into bounded calls without losing the requested total."""
    if not isinstance(target_words, int) or target_words < MIN_SEGMENT_WORDS:
        raise ValueError(f"target_words must be >= {MIN_SEGMENT_WORDS}")
    if target_words > MAX_LONG_WORDS:
        raise ValueError(f"target_words must be <= {MAX_LONG_WORDS}")
    if segment_words < MIN_SEGMENT_WORDS or segment_words > MAX_SEGMENT_WORDS:
        raise ValueError(f"segment_words must be between {MIN_SEGMENT_WORDS} and {MAX_SEGMENT_WORDS}")
    count, remainder = divmod(target_words, segment_words)
    sizes = [segment_words] * count
    if remainder:
        if remainder < MIN_SEGMENT_WORDS and sizes:
            sizes[-1] += remainder
        else:
            sizes.append(remainder)
    return sizes or [target_words]


def make_segment_plan(
    target_words: int,
    *,
    scene_purposes: Iterable[str] = (),
    required_beats: Iterable[str] = (),
    segment_words: int = DEFAULT_SEGMENT_WORDS,
) -> list[SegmentPlan]:
    sizes = split_target_words(target_words, segment_words=segment_words)
    purposes = [str(value).strip() for value in scene_purposes if str(value).strip()]
    beats = tuple(str(value).strip() for value in required_beats if str(value).strip())
    result: list[SegmentPlan] = []
    for index, size in enumerate(sizes):
        purpose = purposes[index] if index < len(purposes) else (
            "开场与目标" if index == 0 else "推进冲突并完成转折" if index < len(sizes) - 1 else "收束本段并留下自然钩子"
        )
        # Beats are distributed, rather than repeated in every call.  The
        # caller may still include all open threads in the context manifest.
        assigned = tuple(beat for beat_index, beat in enumerate(beats) if beat_index % len(sizes) == index)
        result.append(SegmentPlan(index=index, target_words=size, purpose=purpose, required_beats=assigned))
    return result


def context_budget_for_segment(
    *,
    model_window_tokens: int,
    segment_target_words: int,
    safety_margin_tokens: int = 16_000,
    output_reserve_ratio: float = 1.8,
) -> int:
    """Return an elastic input budget for one segment.

    A 256K window is a ceiling, not a requirement to stuff the whole novel in
    every request.  Larger segments receive a larger output reserve while the
    remaining input budget is capped at the provider-tested 208K ceiling.
    """
    if model_window_tokens < 4_096:
        raise ValueError("model_window_tokens is too small")
    output_reserve = max(1_024, int(segment_target_words * output_reserve_ratio))
    return max(1_024, min(208_000, model_window_tokens - safety_margin_tokens - output_reserve))


def prompt_hash(
    messages: list[dict[str, str]],
    *,
    source_revisions: dict[str, Any] | None = None,
    segment_index: int | None = None,
) -> str:
    prefix = f"segment:{segment_index}\n" if segment_index is not None else ""
    payload = prefix + "\n".join(f"{message.get('role','')}:{message.get('content','')}" for message in messages)
    if source_revisions:
        payload += "\nrevisions:" + repr(sorted(source_revisions.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_context_manifest(
    *,
    chapter_id: str,
    segment: SegmentPlan,
    source_revisions: dict[str, Any],
    context_layers: dict[str, int],
    open_threads: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "version": 1,
        "chapterId": chapter_id,
        "segmentIndex": segment.index,
        "targetWords": segment.target_words,
        "purpose": segment.purpose,
        "requiredBeats": list(segment.required_beats),
        "sourceRevisions": dict(source_revisions),
        "contextLayers": {key: int(value) for key, value in context_layers.items()},
        "openThreads": [str(value) for value in open_threads],
    }


@dataclass(frozen=True)
class SegmentCheck:
    status: str
    checks: tuple[dict[str, Any], ...]

    @property
    def blocking(self) -> bool:
        return self.status == "blocked"


def validate_segment_output(
    text: str,
    *,
    target_words: int,
    required_terms: Iterable[str] = (),
    previous_tail: str = "",
    min_ratio: float = 0.55,
    max_ratio: float = 1.35,
) -> SegmentCheck:
    """Hard-gate malformed segments before they can be merged into a chapter."""
    content = (text or "").strip()
    # CJK chars and latin words match the application's billing counter.
    count = len(re.findall(r"[\u3400-\u9fff]", content)) + len(re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*", content))
    checks: list[dict[str, Any]] = []
    checks.append({"id": "non_empty", "ok": bool(content), "message": "段落不能为空"})
    checks.append({"id": "length", "ok": int(target_words * min_ratio) <= count <= int(target_words * max_ratio), "actual": count, "target": target_words})
    terms = [term.strip() for term in required_terms if term and term.strip()]
    missing = [term for term in terms if term not in content]
    checks.append({"id": "required_terms", "ok": not missing, "missing": missing})
    overlap = False
    if previous_tail and content:
        tail = previous_tail[-120:].strip()
        overlap = len(tail) >= 20 and (tail in content or content[:120].find(tail[:20]) >= 0)
    checks.append({"id": "duplicate_boundary", "ok": not overlap})
    blocking = any(not bool(check.get("ok")) for check in checks)
    return SegmentCheck(status="blocked" if blocking else "ready", checks=tuple(checks))


def next_segment_index(rows: Iterable[dict[str, Any]]) -> int:
    """Find the first segment that is not accepted, tolerating sparse rows."""
    statuses = {int(row["segmentIndex"]): str(row.get("status", "pending")) for row in rows if "segmentIndex" in row}
    index = 0
    while statuses.get(index) in {"accepted", "ready", "skipped"}:
        index += 1
    return index


__all__ = [
    "MAX_LONG_WORDS", "MIN_SEGMENT_WORDS", "MAX_SEGMENT_WORDS", "DEFAULT_SEGMENT_WORDS",
    "SegmentPlan", "SegmentCheck", "split_target_words", "make_segment_plan",
    "context_budget_for_segment", "prompt_hash", "build_context_manifest",
    "validate_segment_output", "next_segment_index",
]
