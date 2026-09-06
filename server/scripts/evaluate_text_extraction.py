"""Run the production claim extractor against an authorized JSONL split.

Example:
  python scripts/evaluate_text_extraction.py --corpus C:/private/moshu-corpus.jsonl --split holdout
"""

from __future__ import annotations

import argparse
import asyncio
import json

from providers.consistency import ConsistencyProvider
from services.text_evaluation import evaluate_text_cases, load_text_corpus


async def _run(args: argparse.Namespace) -> None:
    cases = load_text_corpus(args.corpus, split=args.split)
    result = await evaluate_text_cases(cases, ConsistencyProvider())
    print(json.dumps(result.metrics(), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate claim extraction on authorized real text")
    parser.add_argument("--corpus", required=True, help="private JSONL corpus path; do not commit")
    parser.add_argument("--split", choices=("dev", "holdout"), required=True)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
