"""Server-verified provenance for accepted AI draft paragraphs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_usage import GenerationRun

PROVENANCE_ALGORITHM = "djb2-32-v1"


def provenance_hash(text: str) -> str:
    """Match the small deterministic hash stored by the editor."""
    value = 5381
    for character in text:
        value = ((value * 33) ^ ord(character)) & 0xFFFFFFFF
    return f"{value:08x}"


def generated_paragraph_hashes(text: str) -> list[str]:
    """Return one fingerprint for every non-empty generated paragraph."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [provenance_hash(part) for part in normalized.split("\n") if part]


def _node_text(node: dict) -> str:
    if node.get("type") == "text":
        value = node.get("text")
        return value if isinstance(value, str) else ""
    children = node.get("content")
    if not isinstance(children, list):
        return ""
    return "".join(_node_text(child) for child in children if isinstance(child, dict))


@dataclass(frozen=True)
class ProvenanceParagraph:
    paragraph_id: str
    text: str
    words: int
    source: str
    run_id: str | None = None


def _allowed_hashes(run: GenerationRun) -> Counter[str]:
    report = run.layer_report if isinstance(run.layer_report, dict) else {}
    provenance = report.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("algorithm") != PROVENANCE_ALGORITHM:
        return Counter()
    hashes = provenance.get("paragraph_hashes")
    if not isinstance(hashes, list):
        return Counter()
    return Counter(value for value in hashes if isinstance(value, str))


async def classify_document(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    content_json: dict,
    known_runs: Mapping[str, GenerationRun] | None = None,
) -> list[ProvenanceParagraph]:
    """Classify locatable blocks using generation records, never text heuristics."""
    nodes = content_json.get("content", []) if isinstance(content_json, dict) else []
    candidates: list[tuple[str, str, str | None, str | None]] = []
    run_ids: set[str] = set()
    for index, node in enumerate(nodes if isinstance(nodes, list) else []):
        if not isinstance(node, dict) or node.get("type") not in {"paragraph", "heading", "listItem"}:
            continue
        attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
        run_id = attrs.get("aiRunId") if isinstance(attrs.get("aiRunId"), str) else None
        source_hash = attrs.get("aiSourceHash") if isinstance(attrs.get("aiSourceHash"), str) else None
        if run_id:
            run_ids.add(run_id)
        paragraph_id = attrs.get("pid") if isinstance(attrs.get("pid"), str) else f"p-{index}"
        candidates.append((paragraph_id, _node_text(node), run_id, source_hash))

    runs: dict[str, GenerationRun]
    if known_runs is not None:
        runs = {
            run_id: run
            for run_id in run_ids
            if (run := known_runs.get(run_id)) is not None
            and run.project_id == project_id
            and run.chapter_id == chapter_id
        }
    elif run_ids:
        result = await db.execute(
            select(GenerationRun).where(
                GenerationRun.id.in_(run_ids),
                GenerationRun.project_id == project_id,
                GenerationRun.chapter_id == chapter_id,
            )
        )
        runs = {run.id: run for run in result.scalars()}
    else:
        runs = {}
    remaining = {run_id: _allowed_hashes(run) for run_id, run in runs.items()}

    paragraphs: list[ProvenanceParagraph] = []
    for paragraph_id, text, run_id, source_hash in candidates:
        words = len(text.strip())
        source = "human"
        trusted_run_id = None
        allowed = remaining.get(run_id or "")
        if run_id and source_hash and allowed and allowed[source_hash] > 0:
            allowed[source_hash] -= 1
            trusted_run_id = run_id
            source = "ai-raw" if provenance_hash(text) == source_hash else "ai-edited"
        paragraphs.append(ProvenanceParagraph(paragraph_id, text, words, source, trusted_run_id))
    return paragraphs


async def sync_accepted_words(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    content_json: dict,
) -> None:
    """Recompute accepted words for every generation run belonging to a chapter."""
    runs = list((await db.execute(
        select(GenerationRun).where(
            GenerationRun.project_id == project_id,
            GenerationRun.chapter_id == chapter_id,
        )
    )).scalars())
    if not runs:
        return
    accepted: Counter[str] = Counter()
    for paragraph in await classify_document(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        content_json=content_json,
        known_runs={run.id: run for run in runs},
    ):
        if paragraph.run_id:
            accepted[paragraph.run_id] += paragraph.words
    for run in runs:
        run.accepted_words = accepted[run.id]
