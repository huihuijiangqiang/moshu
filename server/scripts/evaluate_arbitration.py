"""Evaluate production arbitration against a private, human-labeled JSONL set."""

from __future__ import annotations

import argparse
import asyncio
import json

from providers.consistency import ConsistencyProvider
from services.arbitration_evaluation import evaluate_arbitration_cases, load_arbitration_corpus


async def _run(args: argparse.Namespace) -> None:
    cases = load_arbitration_corpus(args.corpus, split=args.split)
    result = await evaluate_arbitration_cases(cases, ConsistencyProvider())
    print(json.dumps(result.metrics(), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate conflict arbitration on an authorized gold set")
    parser.add_argument("--corpus", required=True, help="private JSONL corpus path; do not commit")
    parser.add_argument("--split", choices=("dev", "holdout"), required=True)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
