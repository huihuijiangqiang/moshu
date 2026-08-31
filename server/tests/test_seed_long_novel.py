from scripts.seed_long_novel import prose_document


def test_prose_document_uses_backend_paragraph_pid_contract():
    content_html, content_json = prose_document("第一段\n\n第二段")

    assert 'data-paragraph-id="p-0"' in content_html
    assert [node["attrs"]["pid"] for node in content_json["content"]] == ["p-0", "p-1"]
