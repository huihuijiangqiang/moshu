"""Reference-book deconstruction without persistence or model calls."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from api.auth import get_current_user
from db.models_core import User
from services.book_deconstruction import MAX_UPLOAD_BYTES, DeconstructionError, analyze_book

router = APIRouter()


class DeconstructionStats(BaseModel):
    total_words: int
    chapter_count: int
    average_chapter_words: int
    median_chapter_words: int


class DeconstructionBeat(BaseModel):
    position: float
    label: str
    excerpt: str


class DeconstructionChapter(BaseModel):
    index: int
    title: str
    word_count: int
    paragraph_count: int
    dialogue_ratio: float
    summary: str
    beats: list[DeconstructionBeat]
    confidence: str


class RhythmNode(BaseModel):
    chapter_index: int
    position: float
    label: str
    intensity: int


class PayoffBucket(BaseModel):
    range: str
    chapter_from: int | None
    chapter_to: int | None
    score: int
    peak_chapters: list[int]


class DeconstructionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parser: str
    stats: DeconstructionStats
    chapters: list[DeconstructionChapter]
    rhythm_nodes: list[RhythmNode]
    payoff_distribution: list[PayoffBucket]
    warnings: list[str]
    disclaimer: str


@router.post("/deconstruct", response_model=DeconstructionResponse)
async def deconstruct_reference_book(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    _user: User = Depends(get_current_user),
) -> DeconstructionResponse:
    """Analyze a reference manuscript in memory; never persist or enqueue its text."""
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422, detail={"code": "FILE_TOO_LARGE", "reason": "单个文件不能超过 10 MB。"})
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=422, detail={"code": "FILE_TOO_LARGE", "reason": "单个文件不能超过 10 MB。"})
        raw.extend(chunk)
    try:
        result = analyze_book(bytes(raw), filename)
    except DeconstructionError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "reason": str(exc)}) from exc
    except Exception as exc:  # Keep malformed archives from becoming an opaque 500.
        raise HTTPException(status_code=422, detail={"code": "PARSE_ERROR", "reason": "文件无法安全解析。"}) from exc
    return DeconstructionResponse.model_validate(result)


__all__ = ["router"]
