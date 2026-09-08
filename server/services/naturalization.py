"""Explainable paragraph-level naturalization review.

This module deliberately keeps the first pass deterministic.  It produces reviewable
candidates from the same sentence-risk rules shown by the provenance endpoint; an
LLM can be added later without changing the revision and acceptance contract.
"""

from __future__ import annotations

import copy
import html
import re
import secrets
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_core import Chapter, ChapterBody
from db.models_naturalization import NaturalizationFinding, NaturalizationRun
from db.models_usage import GenerationRun
from services.body import BodyRevisionConflictError, compute_content_hash, save_chapter_body
from services.outbox import OutboxService
from services.provenance import (
    PROVENANCE_ALGORITHM,
    SENTENCE_RISK_VERSION,
    ProvenanceParagraph,
    detect_suspected_sentences,
    provenance_hash,
)


class NaturalizationError(RuntimeError):
    code = "NATURALIZATION_ERROR"


class NaturalizationStaleError(NaturalizationError):
    code = "NATURALIZATION_STALE"


class NaturalizationAnchorError(NaturalizationError):
    code = "NATURALIZATION_ANCHOR_MISMATCH"


@dataclass(frozen=True)
class ParagraphRef:
    paragraph_id: str
    text: str
    node: dict[str, Any]


_TRANSITIONS = (
    "值得注意的是，",
    "值得注意的是",
    "不可否认，",
    "不可否认",
    "毋庸置疑，",
    "毋庸置疑",
    "总而言之，",
    "总而言之",
    "综上所述，",
    "综上所述",
    "在这个过程中，",
    "在这个过程中",
    "从某种意义上说，",
    "从某种意义上说",
)
_SOFTENERS = ("似乎", "仿佛", "某种", "一种", "难以言喻", "莫名", "隐隐")
_ACTION_ADVERBS = ("缓缓", "微微", "轻轻", "不禁", "下意识", "忍不住", "悄然")


def _node_text(node: dict[str, Any]) -> str:
    if node.get("type") == "text":
        value = node.get("text")
        return value if isinstance(value, str) else ""
    children = node.get("content")
    if not isinstance(children, list):
        return ""
    return "".join(_node_text(item) for item in children if isinstance(item, dict))


def _paragraph_refs(content_json: dict[str, Any]) -> list[ParagraphRef]:
    refs: list[ParagraphRef] = []

    def walk(nodes: Any) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("type") in {"paragraph", "heading", "listItem"}:
                attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
                paragraph_id = attrs.get("pid")
                if isinstance(paragraph_id, str) and paragraph_id:
                    refs.append(ParagraphRef(paragraph_id, _node_text(node), node))
            walk(node.get("content"))

    walk(content_json.get("content", []))
    return refs


def _naturalize_text(text: str) -> str:
    """Make a conservative candidate while retaining the sentence's facts."""
    candidate = text
    for phrase in _TRANSITIONS:
        candidate = candidate.replace(phrase, "", 1)
    # Keep the first occurrence of repeated softeners/adverbs; repetition is the
    # mechanical signal, while the first modifier may carry authorial intent.
    for word in (*_SOFTENERS, *_ACTION_ADVERBS):
        seen = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal seen
            seen += 1
            return match.group(0) if seen == 1 else ""

        candidate = re.sub(re.escape(word), replace, candidate)
    candidate = re.sub(r" {2,}", " ", candidate)
    candidate = re.sub(r"，{2,}", "，", candidate)
    candidate = re.sub(r"^，", "", candidate).strip()
    return candidate or text


def _replace_text_in_paragraph(node: dict[str, Any], start: int, end: int, replacement: str) -> bool:
    """Replace a range across text leaves while preserving paragraph attrs/marks."""
    leaves: list[dict[str, Any]] = []

    def collect(current: dict[str, Any]) -> None:
        if current.get("type") == "text" and isinstance(current.get("text"), str):
            leaves.append(current)
            return
        children = current.get("content")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    collect(child)

    collect(node)
    full_text = "".join(leaf["text"] for leaf in leaves)
    if start < 0 or end > len(full_text) or end <= start or full_text[start:end] != node.get("_expected", full_text[start:end]):
        return False
    cursor = 0
    inserted = False
    for leaf in leaves:
        original = leaf["text"]
        leaf_start, leaf_end = cursor, cursor + len(original)
        overlap_start = max(start, leaf_start)
        overlap_end = min(end, leaf_end)
        if overlap_start < overlap_end:
            local_start = overlap_start - leaf_start
            local_end = overlap_end - leaf_start
            before = original[:local_start]
            after = original[local_end:]
            if not inserted:
                leaf["text"] = before + replacement + (after if end <= leaf_end else "")
                inserted = True
            else:
                leaf["text"] = after if end <= leaf_end else ""
        cursor = leaf_end
    return inserted


def _replace_paragraph(
    content_json: dict[str, Any],
    paragraph_id: str,
    start: int,
    end: int,
    original: str,
    candidate: str,
    *,
    provenance_run_id: str,
    provenance_source_hash: str,
) -> dict[str, Any]:
    updated = copy.deepcopy(content_json)
    found = False

    def walk(nodes: Any) -> None:
        nonlocal found
        if found or not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("type") in {"paragraph", "heading", "listItem"}:
                attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
                if attrs.get("pid") == paragraph_id:
                    node["_expected"] = original
                    found = _replace_text_in_paragraph(node, start, end, candidate)
                    node.pop("_expected", None)
                    if found:
                        attrs = dict(attrs)
                        attrs["aiRunId"] = provenance_run_id
                        attrs["aiSourceHash"] = provenance_source_hash
                        node["attrs"] = attrs
                    return
            walk(node.get("content"))

    walk(updated.get("content", []))
    if not found:
        raise NaturalizationAnchorError("paragraph text no longer matches the review candidate")
    return updated


class _ParagraphHtmlRange(HTMLParser):
    def __init__(self, source: str, paragraph_id: str) -> None:
        super().__init__(convert_charrefs=False)
        self.paragraph_id = paragraph_id
        self.line_offsets: list[int] = []
        offset = 0
        for line in source.splitlines(keepends=True):
            self.line_offsets.append(offset)
            offset += len(line)
        if not self.line_offsets:
            self.line_offsets.append(0)
        self.stack: list[str] = []
        self.target_depth: int | None = None
        self.target_tag: str | None = None
        self.content_start: int | None = None
        self.content_end: int | None = None

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack.append(tag)
        if self.target_depth is not None:
            return
        attributes = dict(attrs)
        if attributes.get("data-paragraph-id") != self.paragraph_id:
            return
        raw_tag = self.get_starttag_text() or ""
        self.target_depth = len(self.stack)
        self.target_tag = tag
        self.content_start = self._offset() + len(raw_tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        if (
            self.target_depth is not None
            and self.content_end is None
            and len(self.stack) == self.target_depth
            and tag == self.target_tag
        ):
            self.content_end = self._offset()
        if self.stack:
            self.stack.pop()


def _replace_html_text(content_html: str, paragraph_id: str, original: str, candidate: str) -> str:
    parser = _ParagraphHtmlRange(content_html, paragraph_id)
    parser.feed(content_html)
    if parser.content_start is not None and parser.content_end is not None:
        start, end = parser.content_start, parser.content_end
        prefix, target, suffix = content_html[:start], content_html[start:end], content_html[end:]
    else:
        # Older saved bodies may predate paragraph attributes. They remain safe
        # only when the source sentence is unique across the complete HTML body.
        prefix, target, suffix = "", content_html, ""

    if target.count(original) == 1:
        return prefix + target.replace(original, candidate, 1) + suffix
    escaped_original = html.escape(original, quote=False)
    if target.count(escaped_original) == 1:
        escaped_candidate = html.escape(candidate, quote=False)
        return prefix + target.replace(escaped_original, escaped_candidate, 1) + suffix
    # Refuse ambiguous, styled or cross-node ranges instead of allowing the
    # editor's HTML and JSON representations to diverge.
    raise NaturalizationAnchorError("正文样式跨越候选范围或存在重复句，请在写作台手工修改")


def _run_id() -> str:
    return f"nr_{secrets.token_hex(12)}"


def _finding_id() -> str:
    return f"nf_{secrets.token_hex(12)}"


def _numeric_facts(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?(?:年|月|日|天|岁|里|亩|两|文|钱|个|次)?", text)


def _validate_candidate(original: str, candidate: str) -> dict[str, Any]:
    source_numbers = _numeric_facts(original)
    candidate_numbers = _numeric_facts(candidate)
    numeric_facts_preserved = source_numbers == candidate_numbers
    return {
        "status": "passed" if numeric_facts_preserved else "blocked",
        "numeric_facts_preserved": numeric_facts_preserved,
        "source_numbers": source_numbers,
        "candidate_numbers": candidate_numbers,
        "deep_consistency": "queued_on_accept",
    }


async def scan_naturalization(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    scope: str = "chapter",
    mode: str = "rules",
    paragraph_ids: list[str] | None = None,
    source_body_rev: int | None = None,
    style_profile_id: str | None = None,
) -> NaturalizationRun:
    chapter = await db.scalar(select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id, Chapter.deleted_at.is_(None)))
    if chapter is None:
        raise NaturalizationError("chapter not found")
    body = await db.scalar(select(ChapterBody).where(ChapterBody.chapter_id == chapter_id))
    if body is None:
        raise NaturalizationError("chapter has no body")
    if source_body_rev is not None and source_body_rev != body.rev:
        raise NaturalizationStaleError(f"body revision changed: expected {source_body_rev}, actual {body.rev}")
    refs = _paragraph_refs(body.content_json)
    selected = set(paragraph_ids or [])
    if selected:
        refs = [ref for ref in refs if ref.paragraph_id in selected]
    paragraphs = [ProvenanceParagraph(ref.paragraph_id, ref.text, len(ref.text.strip()), "human") for ref in refs]
    suspected = detect_suspected_sentences(paragraphs)
    content_hash = compute_content_hash(body.content_json)
    run = NaturalizationRun(
        id=_run_id(), user_id=user_id, project_id=project_id, chapter_id=chapter_id,
        source_body_rev=body.rev, source_content_hash=content_hash, scope=scope, mode=mode,
        status="ready", style_profile_id=style_profile_id, prompt_version=f"{SENTENCE_RISK_VERSION}:naturalize-v1",
        finding_count=0, accepted_count=0,
    )
    db.add(run)
    # There is intentionally no ORM relationship between run and findings;
    # flush the parent explicitly so FK ordering is deterministic on every DB.
    await db.flush()
    by_id = {ref.paragraph_id: ref for ref in refs}
    for item in suspected:
        ref = by_id.get(item.paragraph_id)
        if ref is None:
            continue
        original = item.text
        candidate = _naturalize_text(original)
        finding = NaturalizationFinding(
            id=_finding_id(), run_id=run.id, paragraph_id=item.paragraph_id,
            start=item.start, end=item.end, original_text=original, candidate_text=candidate,
            source_text_hash=provenance_hash(original), rule_ids=[risk.rule_id for risk in item.risks],
            reasons=list(item.reasons), locked_facts={"paragraph_id": item.paragraph_id, "numbers": _numeric_facts(original)},
            validation={**_validate_candidate(original, candidate), "risk_version": SENTENCE_RISK_VERSION}, status="pending", revision=1,
        )
        db.add(finding)
        run.finding_count += 1
    await db.flush()
    return run


async def refresh_run_staleness(db: AsyncSession, run: NaturalizationRun) -> bool:
    body = await db.scalar(select(ChapterBody).where(ChapterBody.chapter_id == run.chapter_id))
    stale = body is None or body.rev != run.source_body_rev or compute_content_hash(body.content_json) != run.source_content_hash
    if stale and run.status in {"ready", "scanning"}:
        run.status = "stale"
        findings = list((await db.execute(select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id))).scalars())
        for finding in findings:
            if finding.status == "pending":
                finding.status = "stale"
    return stale


async def regenerate_finding(db: AsyncSession, finding: NaturalizationFinding) -> NaturalizationFinding:
    run = await db.get(NaturalizationRun, finding.run_id)
    if run is None:
        raise NaturalizationError("naturalization run not found")
    if await refresh_run_staleness(db, run):
        raise NaturalizationStaleError("naturalization run is stale")
    finding.candidate_text = _naturalize_text(finding.original_text)
    finding.revision += 1
    finding.status = "pending"
    finding.validation = {
        **_validate_candidate(finding.original_text, finding.candidate_text),
        "risk_version": SENTENCE_RISK_VERSION,
    }
    await db.flush()
    return finding


async def accept_finding(db: AsyncSession, *, finding_id: str, project_id: str) -> tuple[NaturalizationFinding, NaturalizationRun, int]:
    finding = await db.scalar(select(NaturalizationFinding).where(NaturalizationFinding.id == finding_id).with_for_update())
    if finding is None:
        raise NaturalizationError("finding not found")
    run = await db.scalar(select(NaturalizationRun).where(NaturalizationRun.id == finding.run_id).with_for_update())
    if run is None or run.project_id != project_id:
        raise NaturalizationError("finding not found")
    body = await db.scalar(select(ChapterBody).where(ChapterBody.chapter_id == run.chapter_id).with_for_update())
    if body is None or body.rev != run.source_body_rev or compute_content_hash(body.content_json) != run.source_content_hash:
        run.status = "stale"
        finding.status = "stale"
        raise NaturalizationStaleError("正文已变化，请重新扫描")
    if finding.status != "pending":
        raise NaturalizationError(f"finding is {finding.status}")
    refs = {ref.paragraph_id: ref for ref in _paragraph_refs(body.content_json)}
    ref = refs.get(finding.paragraph_id)
    if ref is None or finding.end > len(ref.text) or ref.text[finding.start:finding.end] != finding.original_text:
        finding.status = "stale"
        raise NaturalizationAnchorError("正文段落已变化，请重新扫描")
    if provenance_hash(finding.original_text) != finding.source_text_hash:
        finding.status = "stale"
        raise NaturalizationAnchorError("候选来源校验失败")
    validation = _validate_candidate(finding.original_text, finding.candidate_text)
    if validation["status"] != "passed":
        finding.validation = validation
        raise NaturalizationError("候选修改了受保护的数字事实")
    source_paragraph_hash = provenance_hash(ref.text)
    provenance_run_id = secrets.token_hex(16)
    generation_run = GenerationRun(
        id=provenance_run_id,
        user_id=run.user_id,
        project_id=run.project_id,
        chapter_id=run.chapter_id,
        task_type="naturalization",
        model_tier="cheap",
        prompt_tokens=0,
        cached_tokens=0,
        completion_tokens=0,
        generated_words=len(finding.candidate_text.strip()),
        accepted_words=0,
        layer_report={
            "naturalization_run_id": run.id,
            "finding_id": finding.id,
            "rule_ids": finding.rule_ids,
            "provenance": {
                "algorithm": PROVENANCE_ALGORITHM,
                "paragraph_hashes": [source_paragraph_hash],
            },
        },
    )
    db.add(generation_run)
    updated_json = _replace_paragraph(
        body.content_json,
        finding.paragraph_id,
        finding.start,
        finding.end,
        finding.original_text,
        finding.candidate_text,
        provenance_run_id=provenance_run_id,
        provenance_source_hash=source_paragraph_hash,
    )
    updated_html = _replace_html_text(
        body.content_html,
        finding.paragraph_id,
        finding.original_text,
        finding.candidate_text,
    )
    try:
        result = await save_chapter_body(
            db, chapter_id=run.chapter_id, base_rev=body.rev,
            content_html=updated_html, content_json=updated_json,
            trigger="naturalization_accept",
        )
    except BodyRevisionConflictError as error:
        raise NaturalizationStaleError(str(error)) from error
    new_rev = int(result["rev"])
    finding.status = "accepted"
    finding.validation = {**validation, "applied_body_rev": new_rev, "deep_consistency": "queued"}
    run.accepted_count += 1
    # Rebase untouched candidates onto the new head.  A same-paragraph edit can
    # shift later offsets, so locate the exact source sentence again; ambiguity is
    # safer to mark stale than to replace the wrong occurrence.
    refs_after = {ref.paragraph_id: ref for ref in _paragraph_refs(updated_json)}
    remaining = list((await db.execute(
        select(NaturalizationFinding).where(
            NaturalizationFinding.run_id == run.id,
            NaturalizationFinding.id != finding.id,
            NaturalizationFinding.status == "pending",
        )
    )).scalars())
    for other in remaining:
        paragraph = refs_after.get(other.paragraph_id)
        offsets = [] if paragraph is None else [match.start() for match in re.finditer(re.escape(other.original_text), paragraph.text)]
        if len(offsets) != 1:
            other.status = "stale"
            continue
        other.start = offsets[0]
        other.end = offsets[0] + len(other.original_text)
    run.source_body_rev = new_rev
    run.source_content_hash = compute_content_hash(updated_json)
    pending = await db.scalar(
        select(NaturalizationFinding.id).where(
            NaturalizationFinding.run_id == run.id,
            NaturalizationFinding.status == "pending",
        ).limit(1)
    )
    if pending is None:
        run.status = "completed"
    await OutboxService.enqueue(
        db, topic="naturalization.accepted", aggregate_id=run.id, aggregate_rev=new_rev,
        payload={"project_id": run.project_id, "chapter_id": run.chapter_id, "run_id": run.id, "finding_id": finding.id, "body_rev": new_rev},
    )
    await db.flush()
    return finding, run, new_rev


async def reject_finding(db: AsyncSession, *, finding_id: str, project_id: str) -> NaturalizationFinding:
    finding = await db.scalar(select(NaturalizationFinding).where(NaturalizationFinding.id == finding_id).with_for_update())
    if finding is None:
        raise NaturalizationError("finding not found")
    run = await db.get(NaturalizationRun, finding.run_id)
    if run is None or run.project_id != project_id:
        raise NaturalizationError("finding not found")
    if await refresh_run_staleness(db, run):
        raise NaturalizationStaleError("naturalization run is stale")
    if finding.status != "pending":
        raise NaturalizationError(f"finding is {finding.status}")
    finding.status = "rejected"
    await db.flush()
    return finding


__all__ = [
    "NaturalizationError", "NaturalizationStaleError", "NaturalizationAnchorError",
    "scan_naturalization", "refresh_run_staleness", "regenerate_finding", "accept_finding", "reject_finding",
]
