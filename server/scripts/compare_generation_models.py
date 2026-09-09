"""Compare Volcengine Coding Plan models on one fixed fiction task.

Usage (from ``server``): ``python scripts/compare_generation_models.py --run``.
The script reads ``VOLCENGINE_API_KEY`` and optionally
``VOLCENGINE_BASE_URL`` from the local environment/.env.  Results are written
under ``.local/model-comparisons`` (ignored by Git) and keys are never logged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from services.model_catalog import CODING_PLAN_MODELS, VOLCENGINE_CODING_BASE_URL


TASK = """请写一段约800字的中文女频穿越种田小说。女主沈砚秋刚穿越到欠税的河湾村，
她发现里正私扣赈粮，决定先用晒菜干换盐，再借村中账册留下证据。要求有三方势力的
利益冲突（女主、里正、粮商），信息差只让女主知道一半，结尾留下可回收的权谋钩子。
只输出正文，不要解释写作过程。"""


async def run_model(client: httpx.AsyncClient, endpoint: str, key: str, model: str) -> dict:
    started = time.perf_counter()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是严谨的长篇网络小说作者，遵守用户给出的情节硬约束。"},
            {"role": "user", "content": TASK},
        ],
        "temperature": 0.8,
        "max_tokens": 1800,
        "stream": False,
    }
    try:
        response = await client.post(endpoint, headers={"Authorization": f"Bearer {key}"}, json=payload)
        elapsed = round(time.perf_counter() - started, 3)
        if response.is_error:
            return {"model": model, "ok": False, "status": response.status_code, "elapsed": elapsed}
        body = response.json()
        text = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return {"model": model, "ok": bool(text), "status": response.status_code, "elapsed": elapsed, "chars": len(text), "text": text}
    except (httpx.HTTPError, ValueError) as exc:
        return {"model": model, "ok": False, "error": type(exc).__name__}


async def main(run: bool) -> None:
    out_dir = Path(__file__).resolve().parents[1] / ".local" / "model-comparisons"
    out_dir.mkdir(parents=True, exist_ok=True)
    key = os.getenv("VOLCENGINE_API_KEY", "").strip()
    base = os.getenv("VOLCENGINE_BASE_URL", VOLCENGINE_CODING_BASE_URL).rstrip("/")
    endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    summary = {"endpoint": endpoint, "models": [model["id"] for model in CODING_PLAN_MODELS], "executed": run}
    if run and not key:
        raise SystemExit("VOLCENGINE_API_KEY is required with --run (kept out of Git and logs)")
    if run:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
            results = await asyncio.gather(
                *(run_model(client, endpoint, key, str(model["id"])) for model in CODING_PLAN_MODELS)
            )
        (out_dir / "results.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in results), encoding="utf-8")
        summary["resultsFile"] = str(out_dir / "results.jsonl")
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="send the fixed task to every model")
    asyncio.run(main(parser.parse_args().run))
