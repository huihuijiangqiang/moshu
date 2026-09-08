"""Create a private long-novel acceptance report without copying prose into git."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from services.long_novel_evaluation import evaluate_long_novel  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="private generation directory containing checkpoint.json")
    parser.add_argument("--target-chars", type=int, default=100_000)
    parser.add_argument("--retrieval-gold", help="private JSONL with relevant_chapters and retrieved_chapters")
    parser.add_argument("--guard-gold", help="private JSONL with expected_conflicts and detected_conflicts")
    parser.add_argument("--report", help="optional local JSON output path; stdout is always written")
    args = parser.parse_args()
    if args.target_chars < 1:
        parser.error("--target-chars must be positive")
    report = evaluate_long_novel(
        args.output_dir,
        target_chars=args.target_chars,
        retrieval_gold=args.retrieval_gold,
        guard_gold=args.guard_gold,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        destination = Path(args.report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
