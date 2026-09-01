"""Upsert the local long-novel smoke output into a reviewable database project."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jose import jwt
from sqlalchemy import select

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from config import settings  # noqa: E402
from db import (  # noqa: E402
    Chapter,
    ChapterBody,
    ChapterVersion,
    CodexEntry,
    Project,
    User,
    Volume,
)
from db.session import AsyncSessionLocal  # noqa: E402
from services.body import compute_content_hash  # noqa: E402


def visible_chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def prose_document(text: str) -> tuple[str, dict[str, Any]]:
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    html_parts: list[str] = []
    nodes: list[dict[str, Any]] = []
    for index, paragraph in enumerate(paragraphs):
        paragraph_id = f"p-{index}"
        html_parts.append(
            f'<p data-paragraph-id="{paragraph_id}">{html.escape(paragraph)}</p>'
        )
        nodes.append(
            {
                "type": "paragraph",
                "attrs": {"pid": paragraph_id},
                "content": [{"type": "text", "text": paragraph}],
            }
        )
    return "\n".join(html_parts), {"type": "doc", "content": nodes}


def _entry_id_prefix(project_id: str) -> str:
    if len(project_id) <= 10:
        return project_id
    return hashlib.sha1(project_id.encode("utf-8")).hexdigest()[:10]


def _refs_for_terms(
    completed_chapters: list[tuple[str, str]], terms: list[str]
) -> list[str]:
    usable_terms = [term for term in terms if term]
    return [
        chapter_id
        for chapter_id, text in completed_chapters
        if any(term in text for term in usable_terms)
    ]


def _facts(values: dict[str, Any] | list[str]) -> list[dict[str, str]]:
    if isinstance(values, dict):
        return [{"label": str(label), "value": str(value)} for label, value in values.items()]
    return [
        {"label": f"固定事实 {index}", "value": str(value)}
        for index, value in enumerate(values, start=1)
    ]


def build_codex_specs(
    plan: dict[str, Any],
    completed_chapters: list[tuple[str, str]],
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    """把小说计划转换为稳定、可重复导入的设定条目。"""
    prefix = _entry_id_prefix(project_id)
    specs: list[dict[str, Any]] = []
    characters = plan.get("characters", [])
    character_names = [str(character["name"]) for character in characters]

    for index, character in enumerate(characters, start=1):
        name = str(character["name"])
        relation_specs = []
        for clause in re.split(r"[；;]", str(character.get("relationships", ""))):
            clause = clause.strip("。 ")
            target = next(
                (
                    candidate
                    for candidate in character_names
                    if candidate != name and candidate in clause
                ),
                None,
            )
            if target:
                relation_specs.append(
                    {
                        "target_id": f"{prefix}-cx-char-{character_names.index(target) + 1:02d}",
                        "name": target,
                        "relation": "人物关系",
                        "note": clause,
                    }
                )

        personality = [
            part.strip()
            for part in re.split(r"[，；。]", str(character.get("personality", "")))
            if part.strip()
        ]
        role = str(character.get("role", ""))
        specs.append(
            {
                "id": f"{prefix}-cx-char-{index:02d}",
                "kind": "character",
                "name": name,
                "description": "。".join(part for part in (role, str(character.get("personality", ""))) if part),
                "attrs": {
                    "role": role,
                    "age": str(character.get("age", "")),
                    "personality": personality,
                    "ability": str(character.get("skills", "")),
                    "limitation": str(character.get("limits", "")),
                    "relations": relation_specs,
                },
                "resident": index == 1,
                "ref_chapters": _refs_for_terms(completed_chapters, [name]),
            }
        )

    for index, location in enumerate(plan.get("locations", []), start=1):
        name = str(location["名称"])
        function = str(location.get("功能", ""))
        conditions = str(location.get("固定条件", ""))
        specs.append(
            {
                "id": f"{prefix}-cx-place-{index:02d}",
                "kind": "location",
                "name": name,
                "description": "。".join(part for part in (function, conditions) if part),
                "attrs": {"facts": _facts({"用途": function, "固定条件": conditions})},
                "resident": False,
                "ref_chapters": _refs_for_terms(completed_chapters, [name, name.replace("周家", "")]),
            }
        )

    faction_specs = [
        (
            "周氏宗族",
            str(plan.get("era_rules", {}).get("宗族规则", "")),
            ["周氏", "宗族", "族长", "祠堂"],
        ),
        (
            "周家食货作坊",
            str(plan.get("economy_rules", {}).get("经营品类", "")),
            ["作坊", "晒菜", "腌菜", "分拣"],
        ),
        (
            "青溪县衙",
            str(plan.get("era_rules", {}).get("司法规则", "")),
            ["县衙", "户房", "田册", "契税"],
        ),
    ]
    for index, (name, description, terms) in enumerate(faction_specs, start=1):
        specs.append(
            {
                "id": f"{prefix}-cx-faction-{index:02d}",
                "kind": "faction",
                "name": name,
                "description": description,
                "attrs": {"facts": _facts({"职责与边界": description})},
                "resident": False,
                "ref_chapters": _refs_for_terms(completed_chapters, terms),
            }
        )

    rule_groups = [
        ("时代与制度边界", plan.get("era_rules", {}), False),
        ("经营与账目规则", plan.get("economy_rules", {}), False),
        ("叙事与风格规则", plan.get("style_rules", {}), False),
        ("全书不可违背事实", plan.get("fixed_facts", []), True),
    ]
    all_completed_refs = [chapter_id for chapter_id, _ in completed_chapters]
    for index, (name, values, resident) in enumerate(rule_groups, start=1):
        facts = _facts(values)
        specs.append(
            {
                "id": f"{prefix}-cx-rule-{index:02d}",
                "kind": "rule",
                "name": name,
                "description": "；".join(fact["value"] for fact in facts),
                "attrs": {"facts": facts},
                "resident": resident,
                "ref_chapters": all_completed_refs,
            }
        )

    return specs


async def upsert_codex_entries(
    session, plan: dict[str, Any], completed_chapters: list[tuple[str, str]], *, project_id: str
) -> int:
    specs = build_codex_specs(plan, completed_chapters, project_id=project_id)
    existing = {
        entry.id: entry
        for entry in (
            await session.execute(
                select(CodexEntry).where(CodexEntry.project_id == project_id)
            )
        )
        .scalars()
        .all()
    }

    for spec in specs:
        entry = existing.get(spec["id"])
        if entry is None:
            entry = CodexEntry(
                id=spec["id"],
                project_id=project_id,
                kind=spec["kind"],
                name=spec["name"],
                description=spec["description"],
                attrs=spec["attrs"],
                resident=spec["resident"],
                status="confirmed",
                ref_chapters=spec["ref_chapters"],
                conflicts=[],
            )
            session.add(entry)
            continue

        searchable_changed = any(
            getattr(entry, field) != spec[field]
            for field in ("kind", "name", "description")
        )
        entry.kind = spec["kind"]
        entry.name = spec["name"]
        entry.description = spec["description"]
        entry.attrs = spec["attrs"]
        entry.resident = spec["resident"]
        entry.status = "confirmed"
        entry.ref_chapters = spec["ref_chapters"]
        if searchable_changed:
            entry.embedding = None
            entry.embedding_text_hash = None

    return len(specs)


async def upsert_novel(output_dir: Path, *, project_id: str, user_id: str) -> int:
    checkpoint = json.loads((output_dir / "checkpoint.json").read_text(encoding="utf-8"))
    plan = checkpoint["plan"]
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            user = User(
                id=user_id,
                name="本地审核",
                email="review@localhost.invalid",
                plan="studio",
                quota_remaining=1_000_000,
                quota_total=1_000_000,
            )
            session.add(user)

        project = await session.get(Project, project_id)
        if project is None:
            project = Project(id=project_id, owner_id=user_id, title=plan["title"])
            session.add(project)
        project.owner_id = user_id
        project.title = plan["title"]
        project.genre = "女频 · 穿越种田"
        project.status = "ongoing"
        project.target_words_daily = 3300

        volume_id = f"{project_id}-v1"
        volume = await session.get(Volume, volume_id)
        if volume is None:
            volume = Volume(id=volume_id, project_id=project_id, title="立业卷", idx=1)
            session.add(volume)
        volume.title = "立业卷"
        volume.summary = plan.get("premise")
        await session.flush()

        imported = 0
        completed_chapters: list[tuple[str, str]] = []
        for outline in plan["chapters"]:
            number = int(outline["number"])
            chapter_id = f"{project_id}-ch{number:02d}"
            chapter = await session.get(Chapter, chapter_id)
            if chapter is None:
                chapter = Chapter(
                    id=chapter_id,
                    project_id=project_id,
                    volume_id=volume_id,
                    title=outline["title"],
                    idx=number,
                )
                session.add(chapter)
            chapter.volume_id = volume_id
            chapter.title = outline["title"]
            chapter.idx = number
            chapter.outline = [
                outline["objectives"],
                outline["conflict"],
                *outline.get("continuity_constraints", []),
                f"章末：{outline['hook']}",
            ]

            matches = sorted((output_dir / "chapters").glob(f"{number:02d}-*.md"))
            if not matches:
                chapter.words = 0
                continue

            text = matches[0].read_text(encoding="utf-8")
            completed_chapters.append((chapter_id, text))
            content_html, content_json = prose_document(text)
            chapter.words = visible_chars(text)
            summary_path = output_dir / "analysis" / f"{number:02d}-summary.txt"
            chapter.summary = (
                summary_path.read_text(encoding="utf-8").strip()
                if summary_path.exists()
                else None
            )
            body = await session.get(ChapterBody, chapter_id)
            content_changed = body is None or body.content_html != content_html or body.content_json != content_json
            if body is None:
                body = ChapterBody(
                    chapter_id=chapter_id,
                    content_html=content_html,
                    content_json=content_json,
                    rev=1,
                )
                session.add(body)
            elif content_changed:
                body.content_html = content_html
                body.content_json = content_json
                body.rev += 1

            snapshot = (
                await session.execute(
                    select(ChapterVersion).where(
                        ChapterVersion.chapter_id == chapter_id,
                        ChapterVersion.rev == body.rev,
                    )
                )
            ).scalar_one_or_none()
            if snapshot is None:
                session.add(
                    ChapterVersion(
                        chapter_id=chapter_id,
                        content_html=content_html,
                        content_json=content_json,
                        rev=body.rev,
                        trigger="import",
                        content_hash=compute_content_hash(content_json),
                    )
                )
            imported += 1

        imported_codex = await upsert_codex_entries(
            session, plan, completed_chapters, project_id=project_id
        )

        await session.commit()

    token = jwt.encode(
        {
            "sub": user_id,
            "exp": datetime.now(timezone.utc) + timedelta(days=30),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    print(f"imported_chapters={imported}")
    print(f"imported_codex_entries={imported_codex}")
    print(f"project_id={project_id}")
    print(f"review_token={token}")
    return imported


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--project-id", default="p1")
    parser.add_argument("--user-id", default="local-review")
    args = parser.parse_args()
    await upsert_novel(
        args.output_dir.resolve(), project_id=args.project_id, user_id=args.user_id
    )


if __name__ == "__main__":
    asyncio.run(main())
