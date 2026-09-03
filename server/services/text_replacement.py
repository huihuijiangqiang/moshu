"""Structure-preserving literal find/replace helpers."""

from __future__ import annotations

import copy
import hashlib
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterator


@dataclass(frozen=True)
class TextMatch:
    id: str
    ordinal: int
    path: str
    paragraph_id: str | None
    before: str
    matched: str
    after: str


def _positions(text: str, query: str, case_sensitive: bool) -> Iterator[tuple[int, int]]:
    flags = 0 if case_sensitive else re.IGNORECASE
    for match in re.finditer(re.escape(query), text, flags):
        yield match.span()


def _text_nodes(content_json: dict) -> Iterator[tuple[dict, str, str | None]]:
    def walk(nodes: object, path: tuple[int, ...], paragraph_id: str | None):
        if not isinstance(nodes, list):
            return
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            node_path = (*path, index)
            current_pid = paragraph_id
            if node.get("type") in {"paragraph", "heading", "listItem"}:
                pid = node.get("attrs", {}).get("pid")
                current_pid = pid if isinstance(pid, str) and pid else None
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                yield node, ".".join(map(str, node_path)), current_pid
            yield from walk(node.get("content"), node_path, current_pid)

    yield from walk(content_json.get("content"), (), None)


def find_matches(
    chapter_id: str,
    content_json: dict,
    query: str,
    *,
    case_sensitive: bool,
) -> list[TextMatch]:
    matches: list[TextMatch] = []
    ordinal = 0
    for node, path, paragraph_id in _text_nodes(content_json):
        text = node["text"]
        for start, end in _positions(text, query, case_sensitive):
            digest = hashlib.sha256(f"{chapter_id}:{path}:{start}:{end}:{text}".encode()).hexdigest()[:24]
            matches.append(
                TextMatch(
                    id=f"m_{digest}",
                    ordinal=ordinal,
                    path=path,
                    paragraph_id=paragraph_id,
                    before=text[max(0, start - 48) : start],
                    matched=text[start:end],
                    after=text[end : end + 48],
                )
            )
            ordinal += 1
    return matches


def replace_document(
    content_json: dict,
    query: str,
    replacement: str,
    selected_ordinals: set[int],
    *,
    case_sensitive: bool,
) -> dict:
    result = copy.deepcopy(content_json)
    ordinal = 0
    for node, _, _ in _text_nodes(result):
        text = node["text"]
        pieces: list[str] = []
        cursor = 0
        for start, end in _positions(text, query, case_sensitive):
            pieces.append(text[cursor:start])
            pieces.append(replacement if ordinal in selected_ordinals else text[start:end])
            cursor = end
            ordinal += 1
        pieces.append(text[cursor:])
        node["text"] = "".join(pieces)
    return result


class _ReplacingHTMLParser(HTMLParser):
    def __init__(self, query: str, replacement: str, selected_ordinals: set[int], case_sensitive: bool):
        super().__init__(convert_charrefs=True)
        self.query = query
        self.replacement = replacement
        self.selected_ordinals = selected_ordinals
        self.case_sensitive = case_sensitive
        self.ordinal = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        pieces: list[str] = []
        cursor = 0
        for start, end in _positions(data, self.query, self.case_sensitive):
            pieces.append(data[cursor:start])
            pieces.append(self.replacement if self.ordinal in self.selected_ordinals else data[start:end])
            cursor = end
            self.ordinal += 1
        pieces.append(data[cursor:])
        self.parts.append(html.escape("".join(pieces), quote=False))

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")


def replace_html(
    content_html: str,
    query: str,
    replacement: str,
    selected_ordinals: set[int],
    *,
    case_sensitive: bool,
) -> tuple[str, int]:
    parser = _ReplacingHTMLParser(query, replacement, selected_ordinals, case_sensitive)
    parser.feed(content_html)
    parser.close()
    return "".join(parser.parts), parser.ordinal
