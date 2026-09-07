"""Private, non-persistent reference-book analysis.

The deconstruction feature deliberately stays deterministic in the MVP. It extracts
chapter structure and lightweight pacing signals without sending the uploaded work to
an LLM or writing it to the project database.
"""

from __future__ import annotations

import io
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_ANALYSIS_CHAPTERS = 2_000

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".docx", ".epub"}
_WORD_RE = re.compile(r"[A-Za-z0-9\u3400-\u9fff]")
_CHAPTER_RE = re.compile(
    r"^\s*(?:(第[零一二三四五六七八九十百千万\d]+[章节回部卷].{0,80})"
    r"|(Chapter\s+\d+(?:\s*[-:.].*)?)|(序章|楔子|尾声|番外(?:\s*.*)?))\s*$",
    re.IGNORECASE,
)
_QUOTED_SPEECH_RE = re.compile(r"[「“\"]([^」”\"\n]+)[」”\"]")
_PAYOFF_WORDS = (
    "终于", "逆袭", "突破", "击败", "揭开", "震惊", "反转", "暴涨", "收获", "成功",
    "救下", "复仇", "打脸", "夺得", "晋升", "觉醒", "翻盘", "真相", "冠军", "赢下",
)
_CONFLICT_WORDS = ("杀", "战", "冲突", "追", "逃", "击", "闯", "拦", "逼", "争", "危", "怒")
_REVEAL_WORDS = ("原来", "真相", "秘密", "身份", "发现", "揭示", "没想到", "竟然")
_EMOTION_WORDS = ("哭", "笑", "心疼", "绝望", "愤怒", "震动", "沉默", "颤", "温柔")


class DeconstructionError(ValueError):
    """A user-correctable upload or parsing failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "svg", "math"}:
            self._skip_depth += 1
        elif self.parts and tag.lower() in {"p", "div", "br", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "svg", "math"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag.lower() in {"p", "div", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return _normalize_text("".join(self.parts))


def _normalize_text(value: str) -> str:
    value = value.replace("\x00", "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t]+", " ", value).strip()


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if text and "\ufffd" not in text:
            return _normalize_text(text)
    raise DeconstructionError("ENCODING_ERROR", "无法识别文件编码，请转换为 UTF-8 或 GB18030 后重试。")


def _safe_xml(raw: bytes) -> ElementTree.Element:
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered or b"<![" in lowered:
        raise DeconstructionError("UNSAFE_DOCUMENT", "文档包含不安全的 XML 声明，已拒绝解析。")
    try:
        return ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise DeconstructionError("PARSE_ERROR", "压缩文档中的 XML 无法解析。") from exc


def _read_archive(raw: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise DeconstructionError("PARSE_ERROR", "文件不是有效的 DOCX 或 EPUB 压缩文档。") from exc
    total = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            archive.close()
            raise DeconstructionError("UNSAFE_DOCUMENT", "加密压缩包不能安全分析。")
        total += info.file_size
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            archive.close()
            raise DeconstructionError("ARCHIVE_TOO_LARGE", "压缩文档展开后超过 50 MB，无法分析。")
        if info.compress_size and info.file_size / info.compress_size > 100:
            archive.close()
            raise DeconstructionError("ARCHIVE_SUSPICIOUS", "压缩文档压缩比异常，已拒绝解析。")
    return archive


def _docx_text(raw: bytes) -> str:
    archive = _read_archive(raw)
    try:
        try:
            document = archive.read("word/document.xml")
        except KeyError as exc:
            raise DeconstructionError("PARSE_ERROR", "DOCX 中没有可读取的正文。") from exc
        root = _safe_xml(document)
        paragraphs: list[str] = []
        for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            text = "".join(node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
            if text.strip():
                paragraphs.append(text.strip())
        return _normalize_text("\n\n".join(paragraphs))
    finally:
        archive.close()


def _epub_text(raw: bytes) -> str:
    archive = _read_archive(raw)
    try:
        try:
            container = _safe_xml(archive.read("META-INF/container.xml"))
            rootfile = next(
                element.attrib.get("full-path")
                for element in container.iter()
                if element.tag.rsplit("}", 1)[-1] == "rootfile" and element.attrib.get("full-path")
            )
        except (KeyError, StopIteration) as exc:
            raise DeconstructionError("PARSE_ERROR", "EPUB 缺少有效的目录文件。") from exc
        opf = _safe_xml(archive.read(rootfile))
        base = rootfile.rsplit("/", 1)[0] + "/" if "/" in rootfile else ""
        manifest = {
            item.attrib.get("id"): item.attrib.get("href")
            for item in opf.iter()
            if item.tag.rsplit("}", 1)[-1] == "item" and item.attrib.get("id") and item.attrib.get("href")
        }
        sections: list[str] = []
        for itemref in opf.iter():
            if itemref.tag.rsplit("}", 1)[-1] != "itemref":
                continue
            href = manifest.get(itemref.attrib.get("idref"))
            if not href:
                continue
            path = base + href.split("#", 1)[0]
            try:
                parser = _VisibleTextParser()
                parser.feed(archive.read(path).decode("utf-8", errors="replace"))
                section = parser.text()
            except KeyError:
                continue
            if section:
                sections.append(section)
        if not sections:
            raise DeconstructionError("PARSE_ERROR", "EPUB 中没有可读取的文本章节。")
        return _normalize_text("\n\n".join(sections))
    finally:
        archive.close()


def extract_text(raw: bytes, filename: str) -> tuple[str, str]:
    """Return normalized text and the selected parser label."""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DeconstructionError("UNSUPPORTED_FORMAT", "支持 TXT、Markdown、DOCX 和 EPUB 文件。")
    if suffix in {".txt", ".md", ".markdown"}:
        return _decode_text(raw), suffix.lstrip(".").upper()
    if suffix == ".docx":
        return _docx_text(raw), "DOCX"
    return _epub_text(raw), "EPUB"


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _split_chapters(text: str) -> list[tuple[str, str]]:
    lines = text.split("\n")
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        candidate = re.sub(r"^\s{0,3}#{1,6}\s+", "", line).strip().rstrip("#").strip()
        match = _CHAPTER_RE.match(candidate)
        if match:
            starts.append((index, candidate))
            if len(starts) > MAX_ANALYSIS_CHAPTERS:
                raise DeconstructionError("TOO_MANY_CHAPTERS", "识别到的章节超过 2,000 个，无法生成可靠分析。")
    if not starts:
        return [("全文", text)]
    chapters: list[tuple[str, str]] = []
    for position, (start, title) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start + 1:end]).strip()
        chapters.append((title, body))
    if starts[0][0] > 0:
        prelude = "\n".join(lines[:starts[0][0]]).strip()
        if prelude:
            chapters[0] = (chapters[0][0], prelude + "\n\n" + chapters[0][1])
    return chapters


def _paragraphs(text: str) -> list[str]:
    blocks = [re.sub(r"\s+", " ", block).strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    return blocks or ([text.strip()] if text.strip() else [])


def _segment_score(text: str) -> tuple[int, str]:
    conflict = sum(text.count(word) for word in _CONFLICT_WORDS)
    reveal = sum(text.count(word) for word in _REVEAL_WORDS)
    emotion = sum(text.count(word) for word in _EMOTION_WORDS)
    payoff = sum(text.count(word) for word in _PAYOFF_WORDS)
    score = conflict * 3 + reveal * 2 + emotion + payoff * 4 + text.count("！")
    if payoff or conflict >= 2:
        label = "冲突升级"
    elif reveal:
        label = "信息揭示"
    elif emotion:
        label = "情绪转折"
    else:
        label = "场景推进"
    return score, label


def _chapter_analysis(index: int, title: str, body: str) -> tuple[dict, list[dict], int]:
    paragraphs = _paragraphs(body)
    words = _word_count(body)
    dialogue_words = sum(_word_count(match.group(1)) for match in _QUOTED_SPEECH_RE.finditer(body))
    dialogue_ratio = round(min(1.0, dialogue_words / max(1, words)), 3)
    segments: list[tuple[int, int, str, str]] = []
    segment_size = max(1, (len(paragraphs) + 3) // 4)
    for offset in range(0, len(paragraphs), segment_size):
        sample = " ".join(paragraphs[offset:offset + segment_size])
        score, label = _segment_score(sample)
        segments.append((offset, score, label, sample))
    ranked = sorted(segments, key=lambda item: item[1], reverse=True)
    beats = [
        {"position": round((offset + 1) / max(1, len(paragraphs)), 3), "label": label, "excerpt": sample[:120]}
        for offset, score, label, sample in ranked[:3] if score > 0
    ]
    if not beats and paragraphs:
        beats = [{"position": 0.5, "label": "场景推进", "excerpt": paragraphs[0][:120]}]
    peak = max((score for _, score, _, _ in segments), default=0)
    summary = " ".join(paragraphs[:2])[:180]
    return (
        {
            "index": index,
            "title": title,
            "word_count": words,
            "paragraph_count": len(paragraphs),
            "dialogue_ratio": dialogue_ratio,
            "summary": summary,
            "beats": beats,
            "confidence": "low" if title == "全文" else "high",
        },
        [
            {"chapter_index": index, "position": round((offset + 1) / max(1, len(paragraphs)), 3), "label": label, "intensity": min(100, 20 + score * 5)}
            for offset, score, label, _ in ranked[:3] if score > 0
        ],
        peak,
    )


def _payoff_distribution(chapters: list[dict], scores: list[int]) -> list[dict]:
    total = max(1, len(chapters))
    grouped: list[list[tuple[int, int]]] = [[] for _ in range(10)]
    for index, score in enumerate(scores):
        grouped[min(9, index * 10 // total)].append((index, score))
    buckets: list[dict] = []
    for bucket in range(10):
        items = grouped[bucket]
        values = [score for _, score in items]
        peaks = [chapters[index]["index"] for index, score in items if score > 0]
        buckets.append({
            "range": f"{bucket * 10 + 1}-{(bucket + 1) * 10}%",
            "chapter_from": chapters[items[0][0]]["index"] if items else None,
            "chapter_to": chapters[items[-1][0]]["index"] if items else None,
            "score": sum(values),
            "peak_chapters": peaks[:8],
        })
    return buckets


def analyze_book(raw: bytes, filename: str) -> dict:
    if not raw:
        raise DeconstructionError("EMPTY_FILE", "文件为空，无法分析。")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise DeconstructionError("FILE_TOO_LARGE", "单个文件不能超过 10 MB。")
    text, parser = extract_text(raw, filename)
    if not text:
        raise DeconstructionError("EMPTY_TEXT", "文件中没有可读取的文本。")
    if len(text) > MAX_TEXT_CHARS:
        raise DeconstructionError("TEXT_TOO_LONG", "可分析正文不能超过 200 万字符。")
    raw_chapters = _split_chapters(text)
    chapters: list[dict] = []
    rhythm: list[dict] = []
    scores: list[int] = []
    for index, (title, body) in enumerate(raw_chapters, start=1):
        chapter, nodes, peak = _chapter_analysis(index, title, body)
        chapters.append(chapter)
        rhythm.extend(nodes)
        scores.append(peak)
    total_words = _word_count(text)
    warnings = [
        "本结果由确定性启发式统计生成，仅用于学习参考，不代表版权审查或文学质量判断。",
        "上传内容只在本次请求内处理，不保存原文、不写入作品设定库，刷新页面后结果会清空。",
    ]
    if len(chapters) == 1 and chapters[0]["title"] == "全文":
        warnings.append("未识别到明确的章节标题，已按全文作为单个章节分析。")
    warnings.append("节奏节点与爽点分布基于词汇、对白和段落变化的启发式信号，建议结合原文人工复核。")
    return {
        "parser": parser,
        "stats": {
            "total_words": total_words,
            "chapter_count": len(chapters),
            "average_chapter_words": round(total_words / len(chapters)) if chapters else 0,
            "median_chapter_words": sorted(chapter["word_count"] for chapter in chapters)[len(chapters) // 2] if chapters else 0,
        },
        "chapters": chapters,
        "rhythm_nodes": rhythm[:120],
        "payoff_distribution": _payoff_distribution(chapters, scores),
        "warnings": warnings,
        "disclaimer": "用户需确保对上传内容拥有合法使用权；本工具仅提供结构分析，不构成版权许可、审查或抄袭建议。",
    }


__all__ = [
    "MAX_UPLOAD_BYTES",
    "DeconstructionError",
    "analyze_book",
    "extract_text",
]
