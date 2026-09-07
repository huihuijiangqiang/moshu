"""Validation and formatting for author-confirmed chapter time anchors."""

from __future__ import annotations

from datetime import date
from typing import Any


class TemporalAnchorError(ValueError):
    """Raised when a chapter time anchor is malformed or goes backwards."""


def normalize_temporal_anchor(value: dict[str, Any] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TemporalAnchorError("temporal_anchor must be an object")
    start = value.get("start")
    end = value.get("end") or start
    precision = value.get("precision") or "day"
    if not isinstance(start, str) or not start.strip():
        raise TemporalAnchorError("temporal_anchor.start is required")
    if not isinstance(end, str) or not end.strip():
        raise TemporalAnchorError("temporal_anchor.end must be a date")
    if precision not in {"day", "month", "year", "fuzzy"}:
        raise TemporalAnchorError("temporal_anchor.precision is invalid")
    try:
        start_date = date.fromisoformat(start.strip())
        end_date = date.fromisoformat(end.strip())
    except ValueError as exc:
        raise TemporalAnchorError("temporal_anchor dates must use YYYY-MM-DD") from exc
    if end_date < start_date:
        raise TemporalAnchorError("temporal_anchor.end cannot be earlier than start")
    return {"start": start_date.isoformat(), "end": end_date.isoformat(), "precision": precision}


def format_temporal_anchor(anchor: dict[str, Any] | None) -> str:
    normalized = normalize_temporal_anchor(anchor)
    if normalized is None:
        return ""
    if normalized["start"] == normalized["end"]:
        return f"本章故事时间固定在 {normalized['start']}（精度：{normalized['precision']}）。"
    return (
        f"本章故事时间必须落在 {normalized['start']} 至 {normalized['end']} 之间"
        f"（精度：{normalized['precision']}，不得早于起始日或晚于结束日）。"
    )


__all__ = ["TemporalAnchorError", "format_temporal_anchor", "normalize_temporal_anchor"]
