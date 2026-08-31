"""
分块行为测试：覆盖完整性（尾部不丢）、确定性、段落边界、重叠。
"""
import pytest

from services.chunking import chunk_html, chunk_paragraphs, html_to_paragraphs


def test_html_is_split_into_text_paragraphs():
    """块级标签变段落边界，标签被剥离。"""
    html = "<p>第一段</p><p>第二段</p><div>第三段</div>"

    assert html_to_paragraphs(html) == ["第一段", "第二段", "第三段"]


def test_br_creates_paragraph_break_and_entities_decoded():
    html = "<p>上句<br/>下句 &amp; 结尾</p>"

    assert html_to_paragraphs(html) == ["上句", "下句 & 结尾"]


def test_empty_html_yields_no_paragraphs():
    assert html_to_paragraphs("") == []
    assert chunk_html("", max_chars=100) == []


def test_short_content_stays_in_single_chunk():
    chunks = chunk_html("<p>短文本</p>", max_chars=1000)

    assert len(chunks) == 1
    assert chunks[0].text == "短文本"
    assert chunks[0].index == 0


def test_long_content_is_chunked_without_dropping_the_tail():
    """关键回归：旧实现 [:8000] 截断会丢尾部，分块后尾部必须仍在。"""
    paragraphs = [f"段落{i:03d}" + "文" * 200 for i in range(60)]
    html = "".join(f"<p>{p}</p>" for p in paragraphs)

    chunks = chunk_html(html, max_chars=2000, overlap_chars=200)

    assert len(chunks) > 1
    combined = "\n\n".join(chunk.text for chunk in chunks)
    # 首段和末段都必须出现
    assert "段落000" in combined
    assert "段落059" in combined
    # 每一段都被覆盖到
    for index in range(60):
        assert f"段落{index:03d}" in combined, f"paragraph {index} was dropped"


def test_total_input_far_exceeding_old_truncation_limit_is_fully_covered():
    """输入远超旧的 8000 字符上限时仍然全量覆盖。"""
    paragraphs = [f"P{i}" + "x" * 500 for i in range(80)]  # ~40k 字符
    chunks = chunk_paragraphs(paragraphs, max_chars=4000, overlap_chars=200)

    covered = "\n\n".join(chunk.text for chunk in chunks)
    assert len(covered) > 8000
    for index in range(80):
        assert f"P{index}x" in covered


def test_chunking_is_deterministic():
    """同一输入重复切分结果完全一致。"""
    paragraphs = [f"段{i}" + "字" * 100 for i in range(40)]

    first = chunk_paragraphs(paragraphs, max_chars=1500, overlap_chars=150)
    second = chunk_paragraphs(paragraphs, max_chars=1500, overlap_chars=150)

    assert [c.text for c in first] == [c.text for c in second]
    assert [c.index for c in first] == [c.index for c in second]


def test_chunks_respect_paragraph_boundaries():
    """未超长的段落不会被切断，块内容由完整段落拼成。"""
    paragraphs = ["A" * 300, "B" * 300, "C" * 300]

    chunks = chunk_paragraphs(paragraphs, max_chars=700, overlap_chars=0)

    for chunk in chunks:
        for piece in chunk.text.split("\n\n"):
            assert piece in paragraphs


def test_chunk_size_limit_is_respected_for_normal_paragraphs():
    paragraphs = [f"段落{i}" + "内" * 100 for i in range(30)]

    chunks = chunk_paragraphs(paragraphs, max_chars=1000, overlap_chars=100)

    for chunk in chunks:
        assert len(chunk.text) <= 1000


def test_oversized_single_paragraph_is_hard_split_and_fully_retained():
    """单段超过上限时硬切，但内容不丢。"""
    giant = "".join(str(i % 10) for i in range(5000))

    chunks = chunk_paragraphs([giant], max_chars=1000, overlap_chars=0)

    assert len(chunks) > 1
    assert "".join(chunk.text for chunk in chunks) == giant


def test_overlap_shares_context_between_adjacent_chunks():
    """相邻块之间存在重叠上文。"""
    paragraphs = [f"段落{i}" + "字" * 90 for i in range(20)]

    chunks = chunk_paragraphs(paragraphs, max_chars=600, overlap_chars=300)

    assert len(chunks) > 1
    overlapping = 0
    for previous, following in zip(chunks, chunks[1:]):
        previous_parts = set(previous.text.split("\n\n"))
        following_parts = set(following.text.split("\n\n"))
        if previous_parts & following_parts:
            overlapping += 1
    assert overlapping >= 1


def test_zero_overlap_produces_disjoint_chunks():
    paragraphs = [f"段落{i}" for i in range(10)]

    chunks = chunk_paragraphs(paragraphs, max_chars=30, overlap_chars=0)

    seen = []
    for chunk in chunks:
        seen.extend(chunk.text.split("\n\n"))
    assert len(seen) == len(set(seen))


def test_paragraph_ranges_are_tracked():
    paragraphs = [f"段落{i}" + "字" * 100 for i in range(10)]

    chunks = chunk_paragraphs(paragraphs, max_chars=500, overlap_chars=0)

    assert chunks[0].start_paragraph == 0
    assert chunks[-1].end_paragraph == 9
    for chunk in chunks:
        assert chunk.start_paragraph <= chunk.end_paragraph
        assert chunk.paragraph_count >= 1


def test_invalid_parameters_are_rejected():
    with pytest.raises(ValueError):
        chunk_paragraphs(["x"], max_chars=0)
    with pytest.raises(ValueError):
        chunk_paragraphs(["x"], max_chars=100, overlap_chars=-1)
    with pytest.raises(ValueError):
        chunk_paragraphs(["x"], max_chars=100, overlap_chars=100)


def test_exceeding_max_chunks_raises_instead_of_silently_dropping():
    """超过块数上限要显式报错，绝不静默丢内容。"""
    paragraphs = [f"段落{i}" + "字" * 200 for i in range(50)]

    with pytest.raises(ValueError, match="max_chunks"):
        chunk_paragraphs(paragraphs, max_chars=500, overlap_chars=50, max_chunks=3)
