"""Organization membership and project-sharing APIs."""

import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db.models_core import User
from db.models_org import Org, OrgMember
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
