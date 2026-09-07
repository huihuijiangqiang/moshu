"""Real manuscript export formats and non-destructive backup restore."""

import io
import json
import zipfile

from sqlalchemy import func, select

from db.models_codex import CodexAlias, CodexEntry
from db.models_core import Chapter, ChapterBody, Project, ProjectNote, Volume


async def _seed_export_project(db, make_user, make_project, make_chapter):
    db.add_all([make_user("export_owner"), make_user("export_outsider")])
    await db.flush()
    db.add(make_project("export_project", owner_id="export_owner", title="山河账本"))
    await db.flush()
    volume = Volume(id="export_volume", project_id="export_project", title="第一卷", idx=1024)
    db.add(volume)
    await db.flush()
    first = make_chapter("export_ch1", project_id="export_project", volume_id=volume.id, idx=1024, title="第一章 开田", words=7, outline=["进山", "立契"])
    second = make_chapter("export_ch2", project_id="export_project", volume_id=volume.id, idx=2048, title="第二章 赶集", words=4, outline=["卖货"])
    db.add_all([first, second])
    await db.flush()
    db.add_all([
        ChapterBody(chapter_id=first.id, content_html="<p>春雨落在新田。</p><p>她写下契书。</p>", content_json={"type": "doc"}, rev=3),
        ChapterBody(chapter_id=second.id, content_html="<p>天未亮便出门。</p>", content_json={"type": "doc"}, rev=1),
    ])
    entry = CodexEntry(id="export_entry", project_id="export_project", kind="character", name="沈青禾", description="穿越后主持农事。", attrs={"age": 19}, resident=True, status="confirmed", ref_chapters=[first.id], conflicts=[])
    db.add(entry)
    await db.flush()
    db.add(CodexAlias(entry_id=entry.id, alias="青禾"))
    db.add_all(
        [
            ProjectNote(
                id="export_owner_note",
                project_id="export_project",
                user_id="export_owner",
                chapter_id=first.id,
                content="让青禾在雨夜想起旧宅。",
            ),
            ProjectNote(
                id="export_outsider_note",
                project_id="export_project",
                user_id="export_outsider",
                chapter_id=second.id,
                content="不应进入所有者备份。",
            ),
        ]
    )
    await db.commit()


async def test_txt_export_reads_every_body_from_database(
    app_client, async_db_session, make_user, make_project, make_chapter, auth_headers
):
    await _seed_export_project(async_db_session, make_user, make_project, make_chapter)

    response = await app_client.get(
        "/projects/export_project/export?format=txt&include_outline=false&include_codex=false",
        headers=auth_headers("export_owner"),
    )

    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert text.index("第一章 开田") < text.index("第二章 赶集")
    assert "春雨落在新田。" in text
    assert "天未亮便出门。" in text
    assert "正文未加载" not in text


async def test_split_zip_docx_and_epub_are_valid_archives(
    app_client, async_db_session, make_user, make_project, make_chapter, auth_headers
):
    await _seed_export_project(async_db_session, make_user, make_project, make_chapter)
    headers = auth_headers("export_owner")

    split = await app_client.get(
        "/projects/export_project/export?format=markdown&split=zip", headers=headers
    )
    docx = await app_client.get(
        "/projects/export_project/export?format=docx", headers=headers
    )
    epub = await app_client.get(
        "/projects/export_project/export?format=epub", headers=headers
    )

    assert split.status_code == docx.status_code == epub.status_code == 200
    with zipfile.ZipFile(io.BytesIO(split.content)) as bundle:
        names = bundle.namelist()
        assert "000-大纲.md" in names
        assert "000-设定库.md" in names
        assert len([name for name in names if name.endswith(".md")]) == 4
    with zipfile.ZipFile(io.BytesIO(docx.content)) as bundle:
        assert "word/document.xml" in bundle.namelist()
        assert "春雨落在新田。" in bundle.read("word/document.xml").decode("utf-8")
    with zipfile.ZipFile(io.BytesIO(epub.content)) as bundle:
        assert bundle.read("mimetype") == b"application/epub+zip"
        assert "OEBPS/content.opf" in bundle.namelist()


async def test_backup_restores_a_new_owned_project_with_revisions_and_codex(
    app_client, async_db_session, make_user, make_project, make_chapter, auth_headers
):
    await _seed_export_project(async_db_session, make_user, make_project, make_chapter)
    headers = auth_headers("export_owner")

    backup = await app_client.get("/projects/export_project/backup", headers=headers)
    assert backup.status_code == 200
    payload = json.loads(backup.content)
    assert payload["schema_version"] == 1
    assert payload["chapters"][0]["body"]["rev"] == 3
    assert "embedding" not in payload["codex_entries"][0]
    assert payload["project_notes"] == [
        {"chapter_id": "export_ch1", "content": "让青禾在雨夜想起旧宅。"}
    ]

    restored = await app_client.post(
        "/projects/restore-backup", json=payload, headers=headers
    )

    assert restored.status_code == 201
    restored_id = restored.json()["id"]
    assert restored_id != "export_project"
    assert restored.json()["title"] == "山河账本（恢复）"
    assert await async_db_session.scalar(
        select(func.count(Chapter.id)).where(Chapter.project_id == restored_id)
    ) == 2
    restored_chapter = await async_db_session.scalar(
        select(Chapter).where(Chapter.project_id == restored_id).order_by(Chapter.idx)
    )
    restored_body = await async_db_session.get(ChapterBody, restored_chapter.id)
    assert restored_body is not None
    assert restored_body.rev == 3
    assert await async_db_session.scalar(
        select(func.count(CodexEntry.id)).where(CodexEntry.project_id == restored_id)
    ) == 1
    restored_note = await async_db_session.scalar(
        select(ProjectNote).where(ProjectNote.project_id == restored_id)
    )
    assert restored_note is not None
    assert restored_note.user_id == "export_owner"
    assert restored_note.chapter_id == restored_chapter.id
    assert restored_note.content == "让青禾在雨夜想起旧宅。"
    assert (await async_db_session.get(Project, "export_project")).title == "山河账本"


async def test_export_rejects_cross_tenant_access_and_invalid_backup(
    app_client, async_db_session, make_user, make_project, make_chapter, auth_headers
):
    await _seed_export_project(async_db_session, make_user, make_project, make_chapter)

    forbidden = await app_client.get(
        "/projects/export_project/export", headers=auth_headers("export_outsider")
    )
    invalid = await app_client.post(
        "/projects/restore-backup",
        json={"schema_version": 99},
        headers=auth_headers("export_owner"),
    )

    assert forbidden.status_code == 403
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "INVALID_BACKUP"
