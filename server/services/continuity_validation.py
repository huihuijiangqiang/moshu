"""Deterministic checks for structured resource facts extracted from prose."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ContinuityIssue:
    kind: str
    claim_id: int | str
    description: str
    expected: float
    actual: float


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _claim_value(claim: Any, name: str, default: Any = None) -> Any:
    if isinstance(claim, dict):
        return claim.get(name, default)
    return getattr(claim, name, default)


def validate_structured_claims(claims: Iterable[Any]) -> list[ContinuityIssue]:
    """Validate explicit cash, inventory, wage, and resource ledgers.

    Unknown predicates and free-form values are ignored rather than converted into
    speculative warnings. Only JSON structures with the documented fields are hard
    checks, so a model cannot create a false arithmetic error from ordinary prose.
    """
    issues: list[ContinuityIssue] = []
    for claim in claims:
        predicate = str(_claim_value(claim, "predicate", "")).strip().lower()
        if predicate not in {"cash_balance", "inventory_balance", "piece_wage", "resource_usage"}:
            continue
        payload = _payload(_claim_value(claim, "object_value"))
        if payload is None:
            continue
        claim_id = _claim_value(claim, "id", "unknown")
        if predicate in {"cash_balance", "inventory_balance"}:
            opening = _number(payload.get("opening"))
            closing = _number(payload.get("closing"))
            inflow = _number(payload.get("inflow", payload.get("income", payload.get("produced", 0))))
            outflow = _number(payload.get("outflow", payload.get("expense", payload.get("sold", 0))))
            loss = _number(payload.get("loss", payload.get("damaged", 0)))
            if None in {opening, closing, inflow, outflow, loss}:
                continue
            expected = opening + inflow - outflow - loss
            if not math.isclose(expected, closing, rel_tol=0, abs_tol=1e-6):
                issues.append(ContinuityIssue("ledger_arithmetic", claim_id, f"{predicate} 账目不平：期初 {opening:g} + 流入 {inflow:g} - 流出 {outflow:g} - 损耗 {loss:g} = {expected:g}，但期末为 {closing:g}。", expected, closing))
        elif predicate == "piece_wage":
            quantity = _number(payload.get("quantity"))
            rate = _number(payload.get("rate"))
            total = _number(payload.get("total"))
            if None in {quantity, rate, total}:
                continue
            expected = quantity * rate
            if not math.isclose(expected, total, rel_tol=0, abs_tol=1e-6):
                issues.append(ContinuityIssue("wage_arithmetic", claim_id, f"计件工资不一致：{quantity:g} × {rate:g} = {expected:g}，但记录为 {total:g}。", expected, total))
        else:
            used = _number(payload.get("used"))
            available = _number(payload.get("available"))
            if used is None or available is None:
                continue
            if used > available:
                issues.append(ContinuityIssue("resource_overuse", claim_id, f"资源数量不足：同时使用 {used:g}，可用数量仅 {available:g}。", available, used))
    return issues


__all__ = ["ContinuityIssue", "validate_structured_claims"]
