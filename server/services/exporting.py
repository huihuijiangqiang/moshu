"""Database-backed manuscript export and lossless project backup helpers."""

from __future__ import annotations

import io
import json
import re
import secrets
import zipfile
from datetime import UTC, datetime
from html import escape
from html.parser import HTMLParser
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexAlias, CodexEntry, CodexRelation
from db.models_consistency import ChapterOutlineRevision, ChapterOutlineState
from db.models_core import Chapter, ChapterBody, ChapterVersion, Project, Volume

BACKUP_SCHEMA_VERSION = 1


class _PlainTextParser(HTMLParser):
    blocks = {"p", "div", "h1", "h2", "h3", "h4", "li", "blockquote", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.blocks and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.blocks:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()


def html_to_text(value: str) -> str:
    parser = _PlainTextParser()
    parser.feed(value or "")
    parser.close()
    return parser.text()


async def collect_project_archive(db: AsyncSession, project: Project) -> dict[str, Any]:
    volumes = (
        await db.execute(select(Volume).where(Volume.project_id == project.id).order_by(Volume.idx, Volume.id))
    ).scalars().all()
    chapters = (
        await db.execute(select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.idx, Chapter.id))
    ).scalars().all()
    chapter_ids = [chapter.id for chapter in chapters]
    bodies = {}
    states = {}
    revisions: dict[str, list[ChapterOutlineRevision]] = {}
    versions: dict[str, list[ChapterVersion]] = {}
    if chapter_ids:
        bodies = {
            body.chapter_id: body
            for body in (await db.execute(select(ChapterBody).where(ChapterBody.chapter_id.in_(chapter_ids)))).scalars()
        }
        states = {
            state.chapter_id: state
            for state in (
                await db.execute(select(ChapterOutlineState).where(ChapterOutlineState.chapter_id.in_(chapter_ids)))
            ).scalars()
        }
        for row in (
            await db.execute(
                select(ChapterOutlineRevision)
                .where(ChapterOutlineRevision.chapter_id.in_(chapter_ids))
                .order_by(ChapterOutlineRevision.chapter_id, ChapterOutlineRevision.revision)
            )
        ).scalars():
            revisions.setdefault(row.chapter_id, []).append(row)
        for row in (
            await db.execute(
                select(ChapterVersion)
                .where(ChapterVersion.chapter_id.in_(chapter_ids))
                .order_by(ChapterVersion.chapter_id, ChapterVersion.rev, ChapterVersion.id)
            )
        ).scalars():
            versions.setdefault(row.chapter_id, []).append(row)

    entries = (
        await db.execute(
            select(CodexEntry).where(CodexEntry.project_id == project.id).order_by(CodexEntry.kind, CodexEntry.name)
        )
    ).scalars().all()
    entry_ids = [entry.id for entry in entries]
    aliases: dict[str, list[str]] = {}
    relations: list[CodexRelation] = []
    if entry_ids:
        for alias in (
            await db.execute(select(CodexAlias).where(CodexAlias.entry_id.in_(entry_ids)).order_by(CodexAlias.id))
        ).scalars():
            aliases.setdefault(alias.entry_id, []).append(alias.alias)
        relations = (
            await db.execute(select(CodexRelation).where(CodexRelation.from_id.in_(entry_ids)).order_by(CodexRelation.id))
        ).scalars().all()

    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "project": {
            "id": project.id,
            "title": project.title,
            "genre": project.genre,
            "status": project.status,
            "target_words_daily": project.target_words_daily,
            "inspiration": project.inspiration,
            "synopsis": project.synopsis,
            "story_settings": project.story_settings or {},
        },
        "volumes": [
            {"id": volume.id, "title": volume.title, "idx": volume.idx, "summary": volume.summary}
            for volume in volumes
        ],
        "chapters": [
            {
                "id": chapter.id,
                "volume_id": chapter.volume_id,
                "title": chapter.title,
                "idx": chapter.idx,
                "words": chapter.words,
                "outline": chapter.outline or [],
                "summary": chapter.summary,
                "body": (
                    {
                        "content_html": bodies[chapter.id].content_html,
                        "content_json": bodies[chapter.id].content_json,
                        "rev": bodies[chapter.id].rev,
                    }
                    if chapter.id in bodies
                    else None
                ),
                "outline_state": (
                    {
                        "revision": states[chapter.id].revision,
                        "note": states[chapter.id].note,
                        "body_needs_revision": states[chapter.id].body_needs_revision,
                        "marked_outline_rev": states[chapter.id].marked_outline_rev,
                        "marked_body_rev": states[chapter.id].marked_body_rev,
                    }
                    if chapter.id in states
                    else None
                ),
                "outline_revisions": [
                    {
                        "revision": row.revision,
                        "title": row.title,
                        "nodes": row.nodes,
                        "note": row.note,
                        "body_policy": row.body_policy,
                        "body_rev_at_change": row.body_rev_at_change,
                    }
                    for row in revisions.get(chapter.id, [])
                ],
                "versions": [
                    {
                        "content_html": row.content_html,
                        "content_json": row.content_json,
                        "rev": row.rev,
                        "trigger": row.trigger,
                        "content_hash": row.content_hash,
                    }
                    for row in versions.get(chapter.id, [])[-50:]
                ],
            }
            for chapter in chapters
        ],
        "codex_entries": [
            {
                "id": entry.id,
                "kind": entry.kind,
                "name": entry.name,
                "description": entry.description,
                "attrs": entry.attrs or {},
                "resident": entry.resident,
                "status": entry.status,
                "ref_chapters": entry.ref_chapters or [],
                "conflicts": entry.conflicts or [],
                "planted_at": entry.planted_at,
                "expected_by": entry.expected_by,
                "aliases": aliases.get(entry.id, []),
            }
            for entry in entries
        ],
        "codex_relations": [
            {
                "from_id": row.from_id,
                "to_id": row.to_id,
                "relation_type": row.relation_type,
                "description": row.description,
            }
            for row in relations
        ],
    }


def _outline_markdown(archive: dict[str, Any]) -> str:
    lines = [f"# {archive['project']['title']} · 大纲", ""]
    for chapter in archive["chapters"]:
        lines.extend([f"## {chapter['title']}", *[f"- {node}" for node in chapter["outline"]], ""])
    return "\n".join(lines).strip() + "\n"


def _codex_markdown(archive: dict[str, Any]) -> str:
    lines = [f"# {archive['project']['title']} · 设定库", ""]
    for entry in archive["codex_entries"]:
        lines.extend([f"## {entry['name']}", f"类型：{entry['kind']}", "", entry["description"], ""])
        if entry["aliases"]:
            lines.extend([f"别名：{'、'.join(entry['aliases'])}", ""])
        if entry["attrs"]:
            lines.extend(["```json", json.dumps(entry["attrs"], ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines).strip() + "\n"


def _chapter_text(chapter: dict[str, Any], markdown: bool) -> str:
    heading = f"# {chapter['title']}" if markdown else chapter["title"]
    body = html_to_text((chapter.get("body") or {}).get("content_html", ""))
    return f"{heading}\n\n{body}".strip() + "\n"


def render_txt_or_markdown(
    archive: dict[str, Any], *, markdown: bool, include_outline: bool, include_codex: bool
) -> bytes:
    title = archive["project"]["title"]
    parts = [f"# {title}" if markdown else title]
    if include_outline:
        parts.append(_outline_markdown(archive))
    if include_codex:
        parts.append(_codex_markdown(archive))
    parts.extend(_chapter_text(chapter, markdown) for chapter in archive["chapters"])
    return "\n\n".join(parts).encode("utf-8")


def render_chapter_zip(
    archive: dict[str, Any], *, markdown: bool, include_outline: bool, include_codex: bool
) -> bytes:
    buffer = io.BytesIO()
    suffix = "md" if markdown else "txt"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        if include_outline:
            bundle.writestr("000-大纲.md", _outline_markdown(archive))
        if include_codex:
            bundle.writestr("000-设定库.md", _codex_markdown(archive))
        for index, chapter in enumerate(archive["chapters"], start=1):
            safe_title = re.sub(r"[\\/:*?\"<>|]", "_", chapter["title"]).strip()[:80]
            bundle.writestr(f"{index:03d}-{safe_title}.{suffix}", _chapter_text(chapter, markdown))
    return buffer.getvalue()


def render_docx(archive: dict[str, Any]) -> bytes:
    paragraphs: list[tuple[str, bool]] = [(archive["project"]["title"], True)]
    for chapter in archive["chapters"]:
        paragraphs.append((chapter["title"], True))
        paragraphs.extend((line, False) for line in html_to_text((chapter.get("body") or {}).get("content_html", "")).splitlines())
    body = []
    for text, heading in paragraphs:
        style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else ""
        body.append(f'<w:p>{style}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}<w:sectPr/></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        docx.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        docx.writestr("word/document.xml", document)
    return buffer.getvalue()


def render_epub(archive: dict[str, Any]) -> bytes:
    title = escape(archive["project"]["title"])
    sections = []
    nav = []
    for index, chapter in enumerate(archive["chapters"], start=1):
        chapter_id = f"chapter-{index}"
        nav.append(f'<li><a href="content.xhtml#{chapter_id}">{escape(chapter["title"])}</a></li>')
        paragraphs = "".join(
            f"<p>{escape(line)}</p>"
            for line in html_to_text((chapter.get("body") or {}).get("content_html", "")).splitlines()
            if line.strip()
        )
        sections.append(f'<section id="{chapter_id}"><h2>{escape(chapter["title"])}</h2>{paragraphs}</section>')
    content = f'<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head><body><h1>{title}</h1>{"".join(sections)}</body></html>'
    nav_doc = f'<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title></head><body><nav epub:type="toc"><h1>目录</h1><ol>{"".join(nav)}</ol></nav></body></html>'
    identifier = f"urn:uuid:{secrets.token_hex(16)}"
    opf = f'<?xml version="1.0" encoding="UTF-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book-id">{identifier}</dc:identifier><dc:title>{title}</dc:title><dc:language>zh-CN</dc:language></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="content" href="content.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="content"/></spine></package>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        epub.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>', compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr("OEBPS/nav.xhtml", nav_doc, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr("OEBPS/content.xhtml", content, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def backup_json(archive: dict[str, Any]) -> bytes:
    return json.dumps(archive, ensure_ascii=False, indent=2).encode("utf-8")


def validate_backup(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != BACKUP_SCHEMA_VERSION:
        raise ValueError("unsupported_schema_version")
    project = payload.get("project")
    chapters = payload.get("chapters")
    volumes = payload.get("volumes")
    entries = payload.get("codex_entries")
    if not isinstance(project, dict) or not isinstance(project.get("title"), str) or not project["title"].strip():
        raise ValueError("invalid_project")
    if not isinstance(chapters, list) or not isinstance(volumes, list) or not isinstance(entries, list):
        raise ValueError("invalid_collections")
    if len(chapters) > 2000 or len(volumes) > 100 or len(entries) > 10000:
        raise ValueError("backup_too_large")
    total_html = sum(len(str((chapter.get("body") or {}).get("content_html", ""))) for chapter in chapters if isinstance(chapter, dict))
    if total_html > 100_000_000:
        raise ValueError("backup_too_large")


async def restore_project_backup(db: AsyncSession, payload: dict[str, Any], owner_id: str) -> Project:
    validate_backup(payload)
    source_project = payload["project"]
    project = Project(
        id=f"p_{secrets.token_hex(12)}",
        owner_id=owner_id,
        title=f"{source_project['title'].strip()}（恢复）",
        genre=source_project.get("genre"),
        status=source_project.get("status", "ongoing"),
        target_words_daily=max(100, int(source_project.get("target_words_daily") or 3000)),
        inspiration=source_project.get("inspiration"),
        synopsis=source_project.get("synopsis"),
        story_settings=source_project.get("story_settings") or {},
    )
    db.add(project)
    await db.flush()

    volume_ids: dict[str, str] = {}
    for index, source in enumerate(payload["volumes"], start=1):
        old_id = str(source.get("id", index))
        new_id = f"v_{secrets.token_hex(12)}"
        volume_ids[old_id] = new_id
        db.add(Volume(id=new_id, project_id=project.id, title=str(source.get("title") or f"第{index}卷")[:200], idx=int(source.get("idx") or index * 1024), summary=source.get("summary")))
    await db.flush()

    chapter_ids: dict[str, str] = {}
    for index, source in enumerate(payload["chapters"], start=1):
        if not isinstance(source, dict):
            raise ValueError("invalid_chapter")
        old_id = str(source.get("id", index))
        new_id = f"ch_{secrets.token_hex(12)}"
        chapter_ids[old_id] = new_id
        volume_id = volume_ids.get(str(source.get("volume_id")))
        chapter = Chapter(id=new_id, project_id=project.id, volume_id=volume_id, title=str(source.get("title") or f"第{index}章")[:200], idx=int(source.get("idx") or index), words=max(0, int(source.get("words") or 0)), outline=source.get("outline") or [], summary=source.get("summary"))
        db.add(chapter)
        await db.flush()
        body = source.get("body")
        if isinstance(body, dict):
            db.add(ChapterBody(chapter_id=new_id, content_html=str(body.get("content_html") or ""), content_json=body.get("content_json") or {"type": "doc", "content": []}, rev=max(1, int(body.get("rev") or 1))))
        state = source.get("outline_state")
        if isinstance(state, dict):
            db.add(ChapterOutlineState(chapter_id=new_id, revision=max(0, int(state.get("revision") or 0)), note=str(state.get("note") or ""), body_needs_revision=bool(state.get("body_needs_revision")), marked_outline_rev=state.get("marked_outline_rev"), marked_body_rev=state.get("marked_body_rev")))
        for revision in source.get("outline_revisions") or []:
            db.add(ChapterOutlineRevision(chapter_id=new_id, revision=max(1, int(revision.get("revision") or 1)), title=str(revision.get("title") or chapter.title)[:200], nodes=revision.get("nodes") or [], note=str(revision.get("note") or ""), body_policy=revision.get("body_policy") if revision.get("body_policy") in {"plan_only", "mark_body_for_revision"} else "plan_only", body_rev_at_change=revision.get("body_rev_at_change"), created_by=owner_id))
        for version in (source.get("versions") or [])[-50:]:
            db.add(ChapterVersion(chapter_id=new_id, content_html=str(version.get("content_html") or ""), content_json=version.get("content_json") or {"type": "doc", "content": []}, rev=max(1, int(version.get("rev") or 1)), trigger=str(version.get("trigger") or "manual")[:50], content_hash=version.get("content_hash")))

    entry_ids: dict[str, str] = {}
    for index, source in enumerate(payload["codex_entries"], start=1):
        old_id = str(source.get("id", index))
        new_id = f"ce_{secrets.token_hex(12)}"
        entry_ids[old_id] = new_id
        entry = CodexEntry(id=new_id, project_id=project.id, kind=str(source.get("kind") or "rule")[:20], name=str(source.get("name") or f"设定{index}")[:200], description=str(source.get("description") or ""), attrs=source.get("attrs") or {}, resident=bool(source.get("resident")), status=source.get("status") if source.get("status") in {"confirmed", "pending"} else "pending", ref_chapters=[chapter_ids[item] for item in source.get("ref_chapters") or [] if item in chapter_ids], conflicts=[], planted_at=chapter_ids.get(source.get("planted_at")), expected_by=chapter_ids.get(source.get("expected_by")), embedding=None, embedding_text_hash=None)
        db.add(entry)
        await db.flush()
        for alias in source.get("aliases") or []:
            db.add(CodexAlias(entry_id=new_id, alias=str(alias)[:200]))
    for source in payload.get("codex_relations") or []:
        from_id = entry_ids.get(str(source.get("from_id")))
        to_id = entry_ids.get(str(source.get("to_id")))
        if from_id and to_id:
            db.add(CodexRelation(id=f"cr_{secrets.token_hex(12)}", from_id=from_id, to_id=to_id, relation_type=str(source.get("relation_type") or "related")[:50], description=source.get("description")))
    await db.commit()
    return project
