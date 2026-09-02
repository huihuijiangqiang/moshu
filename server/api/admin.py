"""Administrator APIs. Secrets are intentionally never returned by this module."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_admin
from config import settings
from db.models_admin import AdminAuditLog, AuthSession, SystemSetting
from db.models_consistency_extended import ConsistencyRun
from db.models_core import Project, User
from db.models_guard import GuardIssue
from db.session import get_db

router = APIRouter()


class AdminUserOut(BaseModel):
    id: str
    name: str
    email: str | None
    plan: str
    system_role: str
    is_active: bool
    quota_remaining: int
    quota_total: int
    created_at: str


class AdminUserPatch(BaseModel):
    plan: Literal["free", "author", "studio"] | None = None
    system_role: Literal["user", "admin", "super_admin"] | None = None
    is_active: bool | None = None
    quota_remaining: int | None = Field(default=None, ge=0, le=100_000_000)
    quota_total: int | None = Field(default=None, ge=0, le=100_000_000)


class AdminSettingsOut(BaseModel):
    registration_enabled: bool
    default_plan: str
    default_monthly_quota: int
    generation_model: str
    consistency_model: str
    embedding_model: str
    generation_gateway_configured: bool
    embedding_gateway_configured: bool


class AdminSettingsPatch(BaseModel):
    registration_enabled: bool | None = None
    default_plan: Literal["free", "author", "studio"] | None = None
    default_monthly_quota: int | None = Field(default=None, ge=0, le=100_000_000)


def _user_out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        plan=user.plan,
        system_role=user.system_role,
        is_active=user.is_active,
        quota_remaining=user.quota_remaining,
        quota_total=user.quota_total,
        created_at=user.created_at.isoformat(),
    )


async def _setting(db: AsyncSession, key: str, field: str, fallback):
    row = await db.get(SystemSetting, key)
    return row.value.get(field, fallback) if row else fallback


def _audit(actor: User, action: str, target_type: str, target_id: str | None, detail: dict) -> AdminAuditLog:
    return AdminAuditLog(
        actor_id=actor.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        created_at=datetime.now(UTC),
    )


@router.get("/overview")
async def overview(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    now = datetime.now(UTC)
    result = await db.execute(
        select(
            select(func.count(User.id)).scalar_subquery(),
            select(func.count(Project.id)).scalar_subquery(),
            select(func.count(ConsistencyRun.id))
            .where(ConsistencyRun.status.in_(["queued", "extracting", "summarizing", "scanning"]))
            .scalar_subquery(),
            select(func.count(GuardIssue.id)).where(GuardIssue.status == "open").scalar_subquery(),
            select(func.count(AuthSession.id))
            .where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)
            .scalar_subquery(),
        )
    )
    users, projects, running, open_issues, sessions = result.one()
    return {
        "users": users,
        "projects": projects,
        "active_consistency_runs": running,
        "open_guard_issues": open_issues,
        "active_sessions": sessions,
    }


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    search: str = Query(default="", max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminUserOut]:
    query = select(User).order_by(User.created_at.desc(), User.id).offset(offset).limit(limit)
    if search.strip():
        term = f"%{search.strip()}%"
        query = query.where(or_(User.name.ilike(term), User.email.ilike(term)))
    rows = (await db.execute(query)).scalars().all()
    return [_user_out(user) for user in rows]


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: str,
    patch: AdminUserPatch,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserOut:
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND"})
    if target.system_role == "super_admin" and admin.system_role != "super_admin":
        raise HTTPException(status_code=403, detail={"code": "SUPER_ADMIN_REQUIRED"})
    if patch.system_role is not None and admin.system_role != "super_admin":
        raise HTTPException(status_code=403, detail={"code": "SUPER_ADMIN_REQUIRED"})
    if patch.is_active is False and target.id == admin.id:
        raise HTTPException(status_code=422, detail={"code": "CANNOT_DISABLE_SELF"})
    if patch.system_role != "super_admin" and target.system_role == "super_admin":
        count = await db.scalar(select(func.count(User.id)).where(User.system_role == "super_admin"))
        if (count or 0) <= 1:
            raise HTTPException(status_code=422, detail={"code": "LAST_SUPER_ADMIN"})

    changes = patch.model_dump(exclude_none=True)
    before = {key: getattr(target, key) for key in changes}
    for key, value in changes.items():
        setattr(target, key, value)
    db.add(_audit(admin, "user.update", "user", target.id, {"before": before, "after": changes}))
    await db.commit()
    return _user_out(target)


@router.get("/settings", response_model=AdminSettingsOut)
async def get_settings(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminSettingsOut:
    return AdminSettingsOut(
        registration_enabled=await _setting(db, "registration_enabled", "enabled", True),
        default_plan=await _setting(db, "account_defaults", "plan", "free"),
        default_monthly_quota=await _setting(db, "account_defaults", "monthly_quota", 0),
        generation_model=settings.resolved_generation_model,
        consistency_model=settings.consistency_extraction_model,
        embedding_model=settings.embedding_model,
        generation_gateway_configured=bool(settings.model_gateway_main_url and settings.model_gateway_main_key),
        embedding_gateway_configured=bool(settings.embedding_gateway_url and settings.embedding_gateway_key),
    )


@router.patch("/settings", response_model=AdminSettingsOut)
async def update_settings(
    patch: AdminSettingsPatch,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminSettingsOut:
    changed = patch.model_dump(exclude_none=True)
    if "registration_enabled" in changed:
        row = await db.get(SystemSetting, "registration_enabled")
        if row is None:
            row = SystemSetting(key="registration_enabled", value={}, updated_by=admin.id)
            db.add(row)
        row.value = {"enabled": changed["registration_enabled"]}
        row.updated_by = admin.id
    if "default_plan" in changed or "default_monthly_quota" in changed:
        row = await db.get(SystemSetting, "account_defaults")
        if row is None:
            row = SystemSetting(key="account_defaults", value={}, updated_by=admin.id)
            db.add(row)
        value = dict(row.value or {})
        if "default_plan" in changed:
            value["plan"] = changed["default_plan"]
        if "default_monthly_quota" in changed:
            value["monthly_quota"] = changed["default_monthly_quota"]
        row.value = value
        row.updated_by = admin.id
    if changed:
        db.add(_audit(admin, "settings.update", "system", None, changed))
        await db.commit()
    return await get_settings(admin, db)
