"""Upsert the local long-novel smoke output into a reviewable database project."""

from __future__ import annotations

import argparse
import asyncio
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
from db import Chapter, ChapterBody, ChapterVersion, Project, User, Volume  # noqa: E402
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
