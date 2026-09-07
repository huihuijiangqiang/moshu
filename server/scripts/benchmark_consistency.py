"""Repeatable local benchmark for the deterministic consistency hot path.

This benchmark intentionally avoids the model gateway and production data.  It
measures the CPU/memory part that is safe to compare across machines:

* RuleScanner's three deterministic rules over accepted claims;
* interval-aware impact pruning for ownership claims sharing one hot key;
* HTML-to-text conversion and token counting used by ContextAssembler.

The output is JSON so CI or a spreadsheet can consume it.  Database retrieval,
pgvector latency, and network/model latency are environment-specific and are
reported as explicit limitations instead of being replaced with fake numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow both ``python scripts/benchmark_consistency.py`` and module execution
# from the server directory without requiring an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.assembler import html_to_text
from memory.tokenizer import tokenizer
from services.rule_scanner import RuleScanner


@dataclass(slots=True)
class BenchClaim:
    """Small claim double containing exactly the fields RuleScanner reads."""

    id: int
    subject_text: str
    predicate: str
    object_type: str
    object_value: str
    story_order: float
    timeline_id: str = "main"
    subject_entry_id: str | None = None
    object_entry_id: str | None = None
    valid_from_order: float | None = None
    valid_to_order: float | None = None
    polarity: str = "positive"
    certainty: str = "explicit"
    chapter_id: str = "bench-chapter"
    body_rev: int = 1
    outline_rev: int | None = None
    paragraph_id: str = "p1"
    source_anchor: str = "P1"


def build_claims(target_words: int) -> list[BenchClaim]:
    """Create a stable synthetic workload proportional to manuscript size.

    A 10k-word manuscript maps to 100 entities, which keeps the benchmark
    small enough for a laptop while preserving the scanner's grouping shape.
    """

    entity_count = max(10, target_words // 100)
    claims: list[BenchClaim] = []
    claim_id = 1
    for index in range(entity_count):
        subject = f"人物{index:06d}"
        base = float(index * 10)
        claims.extend(
            [
                BenchClaim(claim_id, subject, "alive", "scalar", "true", base),
                BenchClaim(claim_id + 1, subject, "alive", "scalar", "true", base + 1),
            ]
        )
        claim_id += 2

        item_id = f"item-{index:06d}"
        claims.extend(
            [
                BenchClaim(
                    claim_id,
                    f"持有人甲{index:06d}",
                    "owns",
                    "entity",
                    item_id,
                    base,
                    object_entry_id=item_id,
                    valid_from_order=base,
                    valid_to_order=base + 20,
                ),
                BenchClaim(
                    claim_id + 1,
                    f"持有人乙{index:06d}",
                    "owns",
                    "entity",
                    item_id,
                    base + 1,
                    object_entry_id=item_id,
                    valid_from_order=base + 1,
                ),
            ]
        )
        claim_id += 2

        secret = f"秘密{index:06d}"
        claims.extend(
            [
                BenchClaim(claim_id, subject, "uses_knowledge", "scalar", secret, base),
                BenchClaim(claim_id + 1, subject, "acquires_knowledge", "scalar", secret, base + 1),
            ]
        )
        claim_id += 2
    return claims


async def measure_rules(claims: list[BenchClaim], repeats: int) -> dict[str, Any]:
    scanner = RuleScanner()
    samples: list[float] = []
    issue_counts: list[int] = []
    tracemalloc.start()
    try:
        for _ in range(repeats):
            started = time.perf_counter()
            issues = []
            issues.extend(await scanner._check_alive_conflicts(None, "bench", claims))
            issues.extend(await scanner._check_ownership_conflicts(None, "bench", claims))
            issues.extend(await scanner._check_knowledge_boundary(None, "bench", claims))
            samples.append((time.perf_counter() - started) * 1000)
            issue_counts.append(len(issues))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        "claims": len(claims),
        "issues": issue_counts[-1] if issue_counts else 0,
        "elapsed_ms": {
            "median": round(statistics.median(samples), 3),
            "p95": round(sorted(samples)[max(0, int(len(samples) * 0.95) - 1)], 3),
            "min": round(min(samples), 3),
            "max": round(max(samples), 3),
        },
        "peak_memory_mb": round(peak / 1024 / 1024, 3),
    }


def build_interval_impact_claims(target_words: int) -> tuple[list[BenchClaim], list[BenchClaim]]:
    """Build one changed interval plus a long history for the same owned item."""
    candidate_count = max(100, target_words // 20)
    midpoint = candidate_count // 2
    source_start = float(midpoint * 10)
    source = BenchClaim(
        1,
        "当前持有人",
        "owns",
        "entity",
        "共享地契",
        source_start,
        object_entry_id="shared-deed",
        valid_from_order=source_start,
        valid_to_order=source_start + 8,
        chapter_id="changed-chapter",
    )
    claims = [source]
    for index in range(candidate_count):
        start = float(index * 10)
        timeline_id = "mirror" if index % 10 == 0 else "main"
        story_order = None if index % 20 == 2 else start
        valid_to_order = None if index % 10 == 1 else start + 4
        claims.append(
            BenchClaim(
                index + 2,
                f"历史持有人{index:06d}",
                "owns",
                "entity",
                "共享地契",
                story_order,
                timeline_id=timeline_id,
                object_entry_id="shared-deed",
                valid_from_order=story_order,
                valid_to_order=valid_to_order,
                chapter_id=f"history-chapter-{index % 270}",
            )
        )
    return claims, [source]


def measure_interval_pruning(target_words: int, repeats: int) -> dict[str, Any]:
    claims, sources = build_interval_impact_claims(target_words)
    samples: list[float] = []
    kept: list[BenchClaim] = []
    for _ in range(repeats):
        started = time.perf_counter()
        kept = RuleScanner._prune_disjoint_temporal_claims(
            claims,
            sources,
            chapter_id="changed-chapter",
        )
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "candidates": len(claims),
        "kept": len(kept),
        "pruned": len(claims) - len(kept),
        "elapsed_ms": {
            "median": round(statistics.median(samples), 3),
            "p95": round(sorted(samples)[max(0, int(len(samples) * 0.95) - 1)], 3),
        },
    }


def measure_context(target_words: int, repeats: int) -> dict[str, Any]:
    paragraph = "这是用于上下文装配基准的正文片段，包含稳定的中文字符。"
    html = "".join(f"<p>{paragraph}</p>" for _ in range(max(1, target_words // 25)))
    samples: list[float] = []
    tracemalloc.start()
    try:
        for _ in range(repeats):
            started = time.perf_counter()
            text = html_to_text(html)
            tokenizer.count(text)
            samples.append((time.perf_counter() - started) * 1000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        "input_words_approx": target_words,
        "characters": len(html),
        "elapsed_ms": {
            "median": round(statistics.median(samples), 3),
            "p95": round(sorted(samples)[max(0, int(len(samples) * 0.95) - 1)], 3),
        },
        "peak_memory_mb": round(peak / 1024 / 1024, 3),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[10_000, 30_000, 100_000])
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    report = {
        "benchmark": "moshu-consistency-cpu-v2",
        "repeats": args.repeats,
        "results": [],
        "limitations": [
            "不包含正文 claim 抽取、摘要、embedding、LLM 仲裁或网络耗时",
            "不包含 PostgreSQL/pgvector 查询耗时；需在真实数据库上另行压测",
            "RuleScanner 此处测量确定性规则 CPU 路径，不写入 GuardIssue",
            "区间裁剪测量内存中候选过滤，不包含 PostgreSQL 读取候选的耗时",
        ],
    }
    for size in args.sizes:
        if size < 1:
            parser.error("sizes must be positive")
        claims = build_claims(size)
        report["results"].append(
            {
                "target_words": size,
                "rule_scanner": await measure_rules(claims, args.repeats),
                "interval_impact_pruning": measure_interval_pruning(size, args.repeats),
                "context_text_tokenization": measure_context(size, args.repeats),
            }
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
