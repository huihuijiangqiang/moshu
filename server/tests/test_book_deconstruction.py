from __future__ import annotations

import io
import zipfile

import pytest

from services.book_deconstruction import DeconstructionError, analyze_book, extract_text


def _zip_file(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def test_analyze_text_extracts_chapters_rhythm_and_payoff_distribution() -> None:
    text = """第一章 雪夜\n\n沈砚走上城墙。\n\n敌军终于发动冲锋，沈砚击败来敌，众人震惊。\n\n第二章 旧盟\n\n老周头揭开秘密，原来玄铁令另有真相。"""

    result = analyze_book(text.encode(), "reference.txt")

    assert result["parser"] == "TXT"
    assert result["stats"]["chapter_count"] == 2
    assert [chapter["title"] for chapter in result["chapters"]] == ["第一章 雪夜", "第二章 旧盟"]
    assert result["rhythm_nodes"]
    assert sum(bucket["score"] for bucket in result["payoff_distribution"]) > 0
    assert result["disclaimer"]


def test_decode_text_accepts_gb18030_and_falls_back_to_one_full_chapter() -> None:
    text = "这是一段没有章节标题的中文稿件。\n\n第二段继续推进。"

    result = analyze_book(text.encode("gb18030"), "reference.txt")

    assert result["stats"]["chapter_count"] == 1
    assert result["chapters"][0]["title"] == "全文"
    assert result["chapters"][0]["confidence"] == "low"
    assert any("未识别" in warning for warning in result["warnings"])


def test_markdown_headings_and_quoted_dialogue_are_measured() -> None:
    result = analyze_book(
        "# 第一章 开门\n\n“你终于来了。”\n\n门外无人回应。\n\n## 第二章 追击\n\n她追了出去。".encode(),
        "reference.md",
    )

    assert [chapter["title"] for chapter in result["chapters"]] == ["第一章 开门", "第二章 追击"]
    assert result["chapters"][0]["dialogue_ratio"] > 0


def test_docx_and_epub_text_are_read_without_extracting_to_disk() -> None:
    docx = _zip_file({
        "word/document.xml": (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>第一章 开始</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>故事内容。</w:t></w:r></w:p></w:body></w:document>"
        ).encode()
    })
    epub = _zip_file({
        "META-INF/container.xml": (
            b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            b'<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'
        ),
        "OEBPS/content.opf": (
            b'<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            b'<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            b"</manifest><spine><itemref idref='chapter'/></spine></package>"
        ),
        "OEBPS/chapter.xhtml": "<html><body><h1>第一章 开始</h1><p>故事内容。</p></body></html>".encode(),
    })

    assert "故事内容" in extract_text(docx, "sample.docx")[0]
    assert "故事内容" in extract_text(epub, "sample.epub")[0]


def test_rejects_unsafe_xml_and_oversized_or_unknown_files() -> None:
    unsafe = _zip_file({
        "word/document.xml": b"<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><root>&xxe;</root>"
    })
    with pytest.raises(DeconstructionError, match="不安全") as unsafe_error:
        analyze_book(unsafe, "unsafe.docx")
    assert unsafe_error.value.code == "UNSAFE_DOCUMENT"

    with pytest.raises(DeconstructionError) as size_error:
        analyze_book(b"x" * (10 * 1024 * 1024 + 1), "large.txt")
    assert size_error.value.code == "FILE_TOO_LARGE"

    with pytest.raises(DeconstructionError) as format_error:
        analyze_book(b"text", "reference.pdf")
    assert format_error.value.code == "UNSUPPORTED_FORMAT"


async def test_deconstruction_endpoint_requires_auth_and_does_not_create_project_data(
    app_client, seed_project, auth_headers, async_db_session
) -> None:
    await seed_project(user_id="reference_reader", project_id="reference_project")
    payload = "第一章\n\n故事终于开始。".encode()

    unauthorized = await app_client.post("/analysis/deconstruct?filename=reference.txt", content=payload)
    assert unauthorized.status_code == 401

    response = await app_client.post(
        "/analysis/deconstruct?filename=reference.txt",
        headers={**auth_headers("reference_reader"), "Content-Type": "application/octet-stream"},
        content=payload,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["stats"]["chapter_count"] == 1
    assert body["disclaimer"]
    assert not body.get("content")

    from db.models_core import Project

    assert (await async_db_session.get(Project, "reference_project")) is not None
