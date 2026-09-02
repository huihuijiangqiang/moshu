"""Manuscript downloads and non-destructive project backup restoration."""

from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from api.projects import ProjectOut, _project_out
from db.models_core import User
from db.session import get_db
from services.exporting import (
    backup_json,
    collect_project_archive,
    render_chapter_zip,
    render_docx,
    render_epub,
    render_txt_or_markdown,
    restore_project_backup,
)

router = APIRouter()


def _download(content: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/{project_id}/export")
async def export_project(
    project_id: str,
    format: str = Query(default="txt", pattern="^(txt|markdown|docx|epub)$"),
    split: str = Query(default="single", pattern="^(single|zip)$"),
    include_outline: bool = True,
    include_codex: bool = True,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    project = await verify_project_permission(project_id, ProjectPermission.EXPORT, user, db)
    archive = await collect_project_archive(db, project)
    if split == "zip" and format in {"txt", "markdown"}:
        content = render_chapter_zip(archive, markdown=format == "markdown", include_outline=include_outline, include_codex=include_codex)
        return _download(content, "application/zip", f"{project.title}-分章.zip")
    if split == "zip":
        raise HTTPException(status_code=422, detail={"code": "SPLIT_NOT_SUPPORTED"})
    if format in {"txt", "markdown"}:
        content = render_txt_or_markdown(archive, markdown=format == "markdown", include_outline=include_outline, include_codex=include_codex)
        suffix = "md" if format == "markdown" else "txt"
        media_type = "text/markdown; charset=utf-8" if format == "markdown" else "text/plain; charset=utf-8"
        return _download(content, media_type, f"{project.title}.{suffix}")
    if format == "docx":
        return _download(render_docx(archive), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", f"{project.title}.docx")
    return _download(render_epub(archive), "application/epub+zip", f"{project.title}.epub")


@router.get("/{project_id}/backup")
async def backup_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    project = await verify_project_permission(project_id, ProjectPermission.EXPORT, user, db)
    archive = await collect_project_archive(db, project)
    return _download(backup_json(archive), "application/json; charset=utf-8", f"{project.title}-墨枢备份.json")


@router.post("/restore-backup", response_model=ProjectOut, status_code=201)
async def restore_backup(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    try:
        project = await restore_project_backup(db, payload, user.id)
    except (TypeError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"code": "INVALID_BACKUP", "reason": str(error)}) from error
    return await _project_out(db, project)
