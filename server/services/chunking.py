"""
确定性分块 - 长章节必须整体处理，不能截断丢尾部。

之前 provider 用 content_html[:8000] / [:4000] 截断，超长章节的后半部分
永远不会被抽取或摘要，这会静默漏掉一致性冲突。这里按段落边界切块并保留
重叠，保证：

* 覆盖完整 —— 所有输入文本都落在至少一个块里（尾部不丢）；
* 结果确定 —— 同样输入始终得到同样切分（无随机、无时间依赖）；
* 尊重段落 —— 优先在段落边界切开，段落本身超长才做硬切；
* 有重叠 —— 相邻块共享一段上文，避免跨边界的句子被切断。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 块之间的段落分隔符（规范化后统一用两个换行）。
PARAGRAPH_SEPARATOR = "\n\n"

_BLOCK_TAG_RE = re.compile(
    r"</\s*(?:p|div|h[1-6]|li|blockquote|section|article|tr|table|pre)\s*>",
    re.IGNORECASE,
)
_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


@dataclass(frozen=True)
class TextChunk:
    """一个待送模型处理的文本块。

    paragraph_positions 与 text 里的各段一一对应，给出每段在**整章**里的全局
    段落序号。这个字段是叙事顺序与来源身份的基础：块是切分产物，块内序号在跨块
    时没有共同标尺，只有全局段落号才能在整章范围内标识同一处正文。重叠区域里
    同一段在相邻两块中拿到的全局段落号相同，所以重复抽到的同一事实能被识别成
    同一条，而不同段落里的同语义陈述不会被并成一条。

    超长段落被硬切成多片时，各片共享同一个全局段落号 —— 它们本来就是一段。
    """

    index: int
    text: str
    start_paragraph: int
    end_paragraph: int
    paragraph_positions: tuple[int, ...] = ()

    @property
    def paragraph_count(self) -> int:
        return self.end_paragraph - self.start_paragraph + 1

    def labeled_text(self) -> str:
        """带全局段落号的文本，供提示词直接使用。

        没有 paragraph_positions（历史构造）时退回原文，不加标号 —— 宁可让模型
        报不出来源，也不能编造段落号。
        """
        pieces = self.text.split(PARAGRAPH_SEPARATOR)
        if len(self.paragraph_positions) != len(pieces):
            return self.text
        return PARAGRAPH_SEPARATOR.join(
            f"[P{position}] {piece}"
            for position, piece in zip(self.paragraph_positions, pieces)
        )


def html_to_paragraphs(content_html: str) -> list[str]:
    """把 HTML 拆成纯文本段落列表（确定性，不依赖外部解析器）。"""
    if not content_html:
        return []

    # 块级结束标签与 <br> 变成段落分隔，其余标签去掉
    normalized = _BLOCK_TAG_RE.sub(PARAGRAPH_SEPARATOR, content_html)
    normalized = _BR_RE.sub(PARAGRAPH_SEPARATOR, normalized)
    normalized = _TAG_RE.sub("", normalized)
    normalized = (
        normalized.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")

    paragraphs = []
    for raw in normalized.split("\n"):
        cleaned = _WS_RE.sub(" ", raw).strip()
        if cleaned:
            paragraphs.append(cleaned)
    return paragraphs


def _split_oversized(paragraph: str, limit: int, overlap: int) -> list[str]:
    """段落本身超过块上限时硬切，同样带重叠。"""
    if len(paragraph) <= limit:
        return [paragraph]

    step = max(1, limit - overlap)
    pieces = []
    start = 0
    while start < len(paragraph):
        pieces.append(paragraph[start : start + limit])
        if start + limit >= len(paragraph):
            break
        start += step
    return pieces


def chunk_paragraphs(
    paragraphs: list[str],
    *,
    max_chars: int,
    overlap_chars: int = 0,
    max_chunks: int | None = None,
) -> list[TextChunk]:
    """把段落聚成块。

    Args:
        paragraphs: 段落列表
        max_chars: 单块字符上限（必须 > 0）
        overlap_chars: 相邻块重叠的字符数（必须 < max_chars）
        max_chunks: 块数上限；超出时抛错而不是静默丢内容

    Raises:
        ValueError: 参数非法，或块数超过 max_chunks
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must not be negative")
    if overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be smaller than max_chars")

    if not paragraphs:
        return []

    # 先把超长段落拆开，并记住每片属于原来的第几段
    units: list[tuple[int, str]] = []
    for position, paragraph in enumerate(paragraphs):
        for piece in _split_oversized(paragraph, max_chars, overlap_chars):
            units.append((position, piece))

    chunks: list[TextChunk] = []
    current: list[tuple[int, str]] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        chunks.append(
            TextChunk(
                index=len(chunks),
                text=PARAGRAPH_SEPARATOR.join(text for _, text in current),
                start_paragraph=current[0][0],
                end_paragraph=current[-1][0],
                paragraph_positions=tuple(position for position, _ in current),
            )
        )
        if overlap_chars <= 0:
            current, current_len = [], 0
            return
        # 保留尾部若干段作为下一块的重叠上文
        carried: list[tuple[int, str]] = []
        carried_len = 0
        for unit in reversed(current):
            addition = len(unit[1]) + (len(PARAGRAPH_SEPARATOR) if carried else 0)
            if carried_len + addition > overlap_chars:
                break
            carried.insert(0, unit)
            carried_len += addition
        # 整块只有一个单元时不带重叠，否则会原地循环
        current = carried if len(carried) < len(current) else []
        current_len = carried_len if current else 0

    for unit in units:
        addition = len(unit[1]) + (len(PARAGRAPH_SEPARATOR) if current else 0)
        if current and current_len + addition > max_chars:
            flush()
            addition = len(unit[1]) + (len(PARAGRAPH_SEPARATOR) if current else 0)
        current.append(unit)
        current_len += addition

    if current:
        chunks.append(
            TextChunk(
                index=len(chunks),
                text=PARAGRAPH_SEPARATOR.join(text for _, text in current),
                start_paragraph=current[0][0],
                end_paragraph=current[-1][0],
                paragraph_positions=tuple(position for position, _ in current),
            )
        )

    if max_chunks is not None and len(chunks) > max_chunks:
        raise ValueError(
            f"content produced {len(chunks)} chunks, exceeding max_chunks={max_chunks}; "
            "raise consistency_max_chunks or split the chapter"
        )
    return chunks


def chunk_html(
    content_html: str,
    *,
    max_chars: int,
    overlap_chars: int = 0,
    max_chunks: int | None = None,
) -> list[TextChunk]:
    """HTML -> 段落 -> 块，供 provider 直接使用。"""
    return chunk_paragraphs(
        html_to_paragraphs(content_html),
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        max_chunks=max_chunks,
    )
