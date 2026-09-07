"""Organization membership, project sharing, and studio production APIs."""

import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db.models_core import Chapter, Project, User
from db.models_org import ChapterAssignment, Org, OrgMember
from db.session import get_db

router = APIRouter()
OrgRole = Literal["owner", "lead", "writer", "editor", "viewer"]


class OrgOut(BaseModel):
    id: str
    name: str
    plan: str
    seats: int
    seats_used: int
    role: str


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    role: OrgRole


class MemberPatch(BaseModel):
    role: OrgRole


class MemberOut(BaseModel):
    user_id: str
    name: str
    email: str | None
    role: str


class AssignmentCreate(BaseModel):
    chapter_id: str = Field(min_length=1, max_length=32)
    assigned_to: str = Field(min_length=1, max_length=32)
    notes: str | None = Field(default=None, max_length=500)


class AssignmentPatch(BaseModel):
    status: Literal["claimed", "returned", "completed"]


class AssignmentOut(BaseModel):
    id: int
    chapter_id: str
    chapter_title: str
    chapter_index: int
    assigned_to: str
    assignee_name: str
    assigned_by: str
    assigner_name: str
    status: str
    notes: str | None
    words: int
    updated_at: str


class ProductionMemberOut(BaseModel):
    user_id: str
    name: str
    role: str
    assigned_count: int
    claimed_count: int
    completed_count: int
    returned_count: int
    active_words: int
    completed_words: int


class ProductionBoardOut(BaseModel):
    project_id: str
    org_id: str
    can_manage: bool
    current_user_id: str
    assignments: list[AssignmentOut]
    members: list[ProductionMemberOut]


async def _membership(org_id: str, user_id: str, db: AsyncSession) -> OrgMember | None:
    return await db.scalar(
        select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id == user_id)
    )


async def _require_member(org_id: str, user: User, db: AsyncSession) -> OrgMember:
    member = await _membership(org_id, user.id, db)
    if member is None:
        raise HTTPException(status_code=403, detail={"code": "ORG_ACCESS_DENIED"})
    return member


async def _require_owner(org_id: str, user: User, db: AsyncSession) -> OrgMember:
    member = await _require_member(org_id, user, db)
    if member.role != "owner":
        raise HTTPException(status_code=403, detail={"code": "ORG_OWNER_REQUIRED"})
    return member


async def _require_production_manager(org_id: str, user: User, db: AsyncSession) -> OrgMember:
    member = await _require_member(org_id, user, db)
    if member.role not in {"owner", "lead"}:
        raise HTTPException(status_code=403, detail={"code": "PRODUCTION_MANAGER_REQUIRED"})
    return member


async def _project_in_org(
    org_id: str,
    project_id: str,
    user: User,
    db: AsyncSession,
) -> Project:
    project = await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    if project.org_id != org_id:
        raise HTTPException(status_code=409, detail={"code": "PROJECT_NOT_IN_ORG"})
    return project


async def _production_board(
    org_id: str,
    project: Project,
    user: User,
    db: AsyncSession,
) -> ProductionBoardOut:
    assignee = aliased(User)
    assigner = aliased(User)
    rows = (
        await db.execute(
            select(ChapterAssignment, Chapter, assignee, assigner)
            .join(Chapter, Chapter.id == ChapterAssignment.chapter_id)
            .join(assignee, assignee.id == ChapterAssignment.assigned_to)
            .join(assigner, assigner.id == ChapterAssignment.assigned_by)
            .where(Chapter.project_id == project.id, Chapter.deleted_at.is_(None))
            .order_by(Chapter.idx, ChapterAssignment.id.desc())
        )
    ).all()

    # Early databases did not enforce one row per chapter. Do not let historical
    # duplicates inflate production totals when reading those databases.
    latest_rows: list[tuple[ChapterAssignment, Chapter, User, User]] = []
    seen_chapters: set[str] = set()
    for assignment, chapter, assigned_user, assigning_user in rows:
        if chapter.id in seen_chapters:
            continue
        seen_chapters.add(chapter.id)
        latest_rows.append((assignment, chapter, assigned_user, assigning_user))

    assignments = [
        AssignmentOut(
            id=assignment.id,
            chapter_id=chapter.id,
            chapter_title=chapter.title,
            chapter_index=chapter.idx,
            assigned_to=assigned_user.id,
            assignee_name=assigned_user.name,
            assigned_by=assigning_user.id,
            assigner_name=assigning_user.name,
            status=assignment.status,
            notes=assignment.notes,
            words=chapter.words,
            updated_at=assignment.updated_at.isoformat(),
        )
        for assignment, chapter, assigned_user, assigning_user in latest_rows
    ]

    member_rows = (
        await db.execute(
            select(OrgMember, User)
            .join(User, User.id == OrgMember.user_id)
            .where(OrgMember.org_id == org_id)
            .order_by(User.name, User.id)
        )
    ).all()
    production_members: list[ProductionMemberOut] = []
    for membership, member_user in member_rows:
        owned = [item for item in assignments if item.assigned_to == member_user.id]
        production_members.append(
            ProductionMemberOut(
                user_id=member_user.id,
                name=member_user.name,
                role=membership.role,
                assigned_count=sum(item.status == "assigned" for item in owned),
                claimed_count=sum(item.status == "claimed" for item in owned),
                completed_count=sum(item.status == "completed" for item in owned),
                returned_count=sum(item.status == "returned" for item in owned),
                active_words=sum(item.words for item in owned if item.status in {"assigned", "claimed"}),
                completed_words=sum(item.words for item in owned if item.status == "completed"),
            )
        )
    membership = await _require_member(org_id, user, db)
    return ProductionBoardOut(
        project_id=project.id,
        org_id=org_id,
        can_manage=membership.role in {"owner", "lead"},
        current_user_id=user.id,
        assignments=assignments,
        members=production_members,
    )


@router.get("", response_model=list[OrgOut])
async def list_orgs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[OrgOut]:
    rows = (
        await db.execute(
            select(Org, OrgMember.role)
            .join(OrgMember, OrgMember.org_id == Org.id)
            .where(OrgMember.user_id == user.id)
            .order_by(Org.name, Org.id)
        )
    ).all()
    return [OrgOut(id=org.id, name=org.name, plan=org.plan, seats=org.seats, seats_used=org.seats_used, role=role) for org, role in rows]


@router.post("", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(
    request: OrgCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrgOut:
    org = Org(id=f"org_{secrets.token_hex(10)}", name=request.name.strip(), plan="studio", seats=5, seats_used=1)
    db.add(org)
    await db.flush()
    db.add(OrgMember(org_id=org.id, user_id=user.id, role="owner"))
    await db.commit()
    return OrgOut(id=org.id, name=org.name, plan=org.plan, seats=org.seats, seats_used=1, role="owner")


@router.get("/{org_id}/members", response_model=list[MemberOut])
async def list_members(
    org_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MemberOut]:
    await _require_member(org_id, user, db)
    rows = (
        await db.execute(
            select(OrgMember, User)
            .join(User, User.id == OrgMember.user_id)
            .where(OrgMember.org_id == org_id)
            .order_by(OrgMember.created_at, OrgMember.id)
        )
    ).all()
    return [MemberOut(user_id=member.user_id, name=member_user.name, email=member_user.email, role=member.role) for member, member_user in rows]


@router.post("/{org_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    org_id: str,
    request: MemberCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MemberOut:
    await _require_owner(org_id, user, db)
    org = await db.get(Org, org_id)
    target = await db.scalar(select(User).where(User.email == request.email.strip().lower()))
    if org is None:
        raise HTTPException(status_code=404, detail={"code": "ORG_NOT_FOUND"})
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND"})
    if await _membership(org_id, target.id, db):
        raise HTTPException(status_code=409, detail={"code": "MEMBER_EXISTS"})
    used = await db.scalar(select(func.count(OrgMember.id)).where(OrgMember.org_id == org_id))
    if (used or 0) >= org.seats:
        raise HTTPException(status_code=409, detail={"code": "NO_AVAILABLE_SEATS"})
    member = OrgMember(org_id=org_id, user_id=target.id, role=request.role)
    db.add(member)
    org.seats_used = (used or 0) + 1
    await db.commit()
    return MemberOut(user_id=target.id, name=target.name, email=target.email, role=member.role)


@router.patch("/{org_id}/members/{member_user_id}", response_model=MemberOut)
async def update_member(
    org_id: str,
    member_user_id: str,
    request: MemberPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MemberOut:
    await _require_owner(org_id, user, db)
    member = await _membership(org_id, member_user_id, db)
    target = await db.get(User, member_user_id)
    if member is None or target is None:
        raise HTTPException(status_code=404, detail={"code": "MEMBER_NOT_FOUND"})
    if member.role == "owner" and request.role != "owner":
        owners = await db.scalar(select(func.count(OrgMember.id)).where(OrgMember.org_id == org_id, OrgMember.role == "owner"))
        if (owners or 0) <= 1:
            raise HTTPException(status_code=422, detail={"code": "LAST_ORG_OWNER"})
    member.role = request.role
    await db.commit()
    return MemberOut(user_id=target.id, name=target.name, email=target.email, role=member.role)


@router.delete("/{org_id}/members/{member_user_id}", status_code=204)
async def remove_member(
    org_id: str,
    member_user_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _require_owner(org_id, user, db)
    member = await _membership(org_id, member_user_id, db)
    org = await db.get(Org, org_id)
    if member is None or org is None:
        raise HTTPException(status_code=404, detail={"code": "MEMBER_NOT_FOUND"})
    if member.role == "owner":
        owners = await db.scalar(select(func.count(OrgMember.id)).where(OrgMember.org_id == org_id, OrgMember.role == "owner"))
        if (owners or 0) <= 1:
            raise HTTPException(status_code=422, detail={"code": "LAST_ORG_OWNER"})
    await db.delete(member)
    org.seats_used = max(0, org.seats_used - 1)
    await db.commit()


@router.post("/{org_id}/projects/{project_id}")
async def attach_project(
    org_id: str,
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _require_owner(org_id, user, db)
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_PROJECT, user, db)
    if project.owner_id != user.id:
        raise HTTPException(status_code=403, detail={"code": "PROJECT_OWNER_REQUIRED"})
    project.org_id = org_id
    await db.commit()
    return {"project_id": project.id, "org_id": org_id}


@router.get("/{org_id}/projects/{project_id}/production", response_model=ProductionBoardOut)
async def get_production_board(
    org_id: str,
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductionBoardOut:
    project = await _project_in_org(org_id, project_id, user, db)
    return await _production_board(org_id, project, user, db)


@router.post(
    "/{org_id}/projects/{project_id}/assignments",
    response_model=AssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def assign_chapter(
    org_id: str,
    project_id: str,
    request: AssignmentCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AssignmentOut:
    await _require_production_manager(org_id, user, db)
    project = await _project_in_org(org_id, project_id, user, db)
    chapter = await db.scalar(
        select(Chapter)
        .where(
            Chapter.id == request.chapter_id,
            Chapter.project_id == project.id,
            Chapter.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"})
    target_membership = await _membership(org_id, request.assigned_to, db)
    if target_membership is None or target_membership.role == "viewer":
        raise HTTPException(status_code=422, detail={"code": "ASSIGNEE_CANNOT_WRITE"})

    assignment = await db.scalar(
        select(ChapterAssignment)
        .where(ChapterAssignment.chapter_id == chapter.id)
        .order_by(ChapterAssignment.id.desc())
        .limit(1)
    )
    if assignment is None:
        assignment = ChapterAssignment(
            chapter_id=chapter.id,
            assigned_to=request.assigned_to,
            assigned_by=user.id,
        )
        db.add(assignment)
    else:
        assignment.assigned_to = request.assigned_to
        assignment.assigned_by = user.id
    assignment.status = "assigned"
    assignment.notes = request.notes.strip() if request.notes and request.notes.strip() else None
    await db.commit()

    board = await _production_board(org_id, project, user, db)
    return next(item for item in board.assignments if item.id == assignment.id)


@router.patch(
    "/{org_id}/projects/{project_id}/assignments/{assignment_id}",
    response_model=AssignmentOut,
)
async def update_assignment_status(
    org_id: str,
    project_id: str,
    assignment_id: int,
    request: AssignmentPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AssignmentOut:
    project = await _project_in_org(org_id, project_id, user, db)
    membership = await _require_member(org_id, user, db)
    assignment = await db.scalar(
        select(ChapterAssignment)
        .join(Chapter, Chapter.id == ChapterAssignment.chapter_id)
        .where(
            ChapterAssignment.id == assignment_id,
            Chapter.project_id == project.id,
            Chapter.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail={"code": "ASSIGNMENT_NOT_FOUND"})
    manager = membership.role in {"owner", "lead"}
    if not manager and (assignment.assigned_to != user.id or membership.role == "viewer"):
        raise HTTPException(status_code=403, detail={"code": "ASSIGNMENT_ACCESS_DENIED"})

    allowed = {
        "assigned": {"claimed", "returned"},
        "claimed": {"completed", "returned"},
        "returned": set(),
        "completed": set(),
    }
    if request.status not in allowed.get(assignment.status, set()):
        raise HTTPException(status_code=409, detail={"code": "INVALID_ASSIGNMENT_TRANSITION"})
    assignment.status = request.status
    await db.commit()

    board = await _production_board(org_id, project, user, db)
    return next(item for item in board.assignments if item.id == assignment.id)


@router.delete(
    "/{org_id}/projects/{project_id}/assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_assignment(
    org_id: str,
    project_id: str,
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _require_production_manager(org_id, user, db)
    project = await _project_in_org(org_id, project_id, user, db)
    assignment = await db.scalar(
        select(ChapterAssignment)
        .join(Chapter, Chapter.id == ChapterAssignment.chapter_id)
        .where(ChapterAssignment.id == assignment_id, Chapter.project_id == project.id)
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail={"code": "ASSIGNMENT_NOT_FOUND"})
    await db.delete(assignment)
    await db.commit()
