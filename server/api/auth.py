"""Authentication endpoints and authorization dependencies."""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models_admin import AuthSession, SystemSetting
from db.models_core import Project, User
from db.models_org import OrgMember
from db.session import get_db
from services.usage import next_month_start

router = APIRouter()
security = HTTPBearer(auto_error=False)
_PASSWORD_ITERATIONS = 210_000


class UserOut(BaseModel):
    id: str
    name: str
    email: str | None
    plan: str
    system_role: str
    is_active: bool


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("email must contain @")
        return normalized


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        scheme, iterations, salt, expected = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.urlsafe_b64decode(salt.encode("ascii")),
            int(iterations),
        )
        actual = base64.urlsafe_b64encode(digest).decode("ascii")
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _encode_token(
    user_id: str,
    token_type: str,
    expires_delta: timedelta,
    *,
    session_id: str,
) -> tuple[str, str]:
    now = datetime.now(UTC)
    token_id = secrets.token_hex(16)
    token = jwt.encode(
        {
            "sub": user_id,
            "type": token_type,
            "iat": now,
            "exp": now + expires_delta,
            "jti": token_id,
            "sid": session_id,
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return token, token_id


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        plan=user.plan,
        system_role=user.system_role,
        is_active=user.is_active,
    )


async def _token_response(
    user: User,
    db: AsyncSession,
    session: AuthSession | None = None,
) -> TokenResponse:
    now = datetime.now(UTC)
    access_seconds = settings.access_token_expire_minutes * 60
    session_id = session.id if session else f"as_{secrets.token_hex(12)}"
    access_token, _ = _encode_token(
        user.id,
        "access",
        timedelta(seconds=access_seconds),
        session_id=session_id,
    )
    refresh_token, refresh_jti = _encode_token(
        user.id,
        "refresh",
        timedelta(days=settings.refresh_token_expire_days),
        session_id=session_id,
    )
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    if session is None:
        session = AuthSession(
            id=session_id,
            user_id=user.id,
            refresh_jti=refresh_jti,
            expires_at=expires_at,
            last_used_at=now,
        )
        db.add(session)
    else:
        session.refresh_jti = refresh_jti
        session.expires_at = expires_at
        session.last_used_at = now
    await db.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=access_seconds,
        user=_user_out(user),
    )


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        token_type = payload.get("type")
        legacy_access = expected_type == "access" and token_type is None
        if not isinstance(user_id, str) or (token_type != expected_type and not legacy_access):
            raise _credentials_error()
        return payload
    except JWTError as error:
        raise _credentials_error() from error


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    registration = await db.get(SystemSetting, "registration_enabled")
    if registration is not None and registration.value.get("enabled") is False:
        raise HTTPException(status_code=403, detail={"code": "REGISTRATION_DISABLED"})
    existing = await db.execute(select(User.id).where(User.email == request.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail={"code": "EMAIL_ALREADY_REGISTERED"})

    defaults = await db.get(SystemSetting, "account_defaults")
    default_values = defaults.value if defaults else {}
    default_plan = default_values.get("plan", "free")
    default_quota = default_values.get("monthly_quota", 0)
    if default_plan not in {"free", "author", "studio"} or not isinstance(default_quota, int):
        default_plan, default_quota = "free", 0
    user = User(
        id=f"u_{secrets.token_hex(12)}",
        name=request.name.strip(),
        email=request.email,
        password_hash=hash_password(request.password),
        plan=default_plan,
        system_role="user",
        is_active=True,
        quota_remaining=max(0, default_quota),
        quota_total=max(0, default_quota),
        quota_resets_at=next_month_start(datetime.now(UTC)),
    )
    db.add(user)
    await db.flush()
    return await _token_response(user, db)


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    email = request.email.strip().lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail={"code": "INVALID_CREDENTIALS"})
    return await _token_response(user, db)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    payload = _decode_token(request.refresh_token, "refresh")
    user_id = payload["sub"]
    session_id = payload.get("sid")
    refresh_jti = payload.get("jti")
    if not isinstance(session_id, str) or not isinstance(refresh_jti, str):
        raise _credentials_error()
    now = datetime.now(UTC)
    result = await db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.id == session_id,
            AuthSession.user_id == user_id,
            AuthSession.refresh_jti == refresh_jti,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        .with_for_update()
    )
    row = result.one_or_none()
    if row is None:
        raise _credentials_error()
    session, user = row
    if not user.is_active:
        raise _credentials_error()
    return await _token_response(user, db, session)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Decode an access JWT and return the current user."""
    if credentials is None:
        raise _credentials_error()
    payload = _decode_token(credentials.credentials, "access")
    user_id = payload["sub"]
    session_id = payload.get("sid")
    if payload.get("type") == "access":
        if not isinstance(session_id, str):
            raise _credentials_error()
        now = datetime.now(UTC)
        active_session = await db.scalar(
            select(AuthSession.id).where(
                AuthSession.id == session_id,
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
        )
        if active_session is None:
            raise _credentials_error()
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise _credentials_error()
    return user


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(user)


@router.post("/logout", status_code=204)
async def logout(
    request: RefreshRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    payload = _decode_token(request.refresh_token, "refresh")
    if payload.get("sub") != user.id:
        raise _credentials_error()
    session_id = payload.get("sid")
    refresh_jti = payload.get("jti")
    if isinstance(session_id, str) and isinstance(refresh_jti, str):
        result = await db.execute(
            select(AuthSession).where(
                AuthSession.id == session_id,
                AuthSession.user_id == user.id,
                AuthSession.refresh_jti == refresh_jti,
                AuthSession.revoked_at.is_(None),
            )
        )
        session = result.scalar_one_or_none()
        if session is not None:
            session.revoked_at = datetime.now(UTC)
            await db.commit()


@router.post("/logout-all", status_code=204)
async def logout_all(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    from sqlalchemy import update

    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.system_role not in {"admin", "super_admin"}:
        raise HTTPException(status_code=403, detail={"code": "ADMIN_REQUIRED"})
    return user


class ProjectPermission(StrEnum):
    VIEW = "view"
    EDIT_BODY = "edit_body"
    MANAGE_OUTLINE = "manage_outline"
    MANAGE_CODEX = "manage_codex"
    RUN_GUARD = "run_guard"
    RESOLVE_GUARD = "resolve_guard"
    GENERATE = "generate"
    EXPORT = "export"
    MANAGE_PROJECT = "manage_project"
    MANAGE_MEMBERS = "manage_members"


_ORG_ROLE_PERMISSIONS: dict[str, frozenset[ProjectPermission]] = {
    "owner": frozenset(ProjectPermission),
    "lead": frozenset({
        ProjectPermission.VIEW,
        ProjectPermission.EDIT_BODY,
        ProjectPermission.MANAGE_OUTLINE,
        ProjectPermission.MANAGE_CODEX,
        ProjectPermission.RUN_GUARD,
        ProjectPermission.RESOLVE_GUARD,
        ProjectPermission.GENERATE,
        ProjectPermission.EXPORT,
    }),
    "writer": frozenset({
        ProjectPermission.VIEW,
        ProjectPermission.EDIT_BODY,
        ProjectPermission.MANAGE_OUTLINE,
        ProjectPermission.GENERATE,
    }),
    "editor": frozenset({
        ProjectPermission.VIEW,
        ProjectPermission.EDIT_BODY,
        ProjectPermission.MANAGE_CODEX,
        ProjectPermission.RESOLVE_GUARD,
        ProjectPermission.EXPORT,
    }),
    "viewer": frozenset({ProjectPermission.VIEW}),
}


async def verify_project_access(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """Backward-compatible read-access dependency."""
    return await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)


async def verify_project_permission(
    project_id: str,
    permission: ProjectPermission,
    user: User,
    db: AsyncSession,
) -> Project:
    """Authorize one concrete action; organization role names are not decorative."""
    project, permissions = await get_project_permissions(project_id, user, db)
    if permission in permissions:
        return project
    raise HTTPException(status_code=403, detail="Access denied")


async def get_project_permissions(
    project_id: str,
    user: User,
    db: AsyncSession,
) -> tuple[Project, frozenset[ProjectPermission]]:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id == user.id:
        return project, frozenset(ProjectPermission)
    if project.org_id:
        org_result = await db.execute(
            select(OrgMember).where(OrgMember.org_id == project.org_id, OrgMember.user_id == user.id)
        )
        membership = org_result.scalar_one_or_none()
        if membership:
            return project, _ORG_ROLE_PERMISSIONS.get(membership.role, frozenset())
    return project, frozenset()


class ProjectAccessChecker:
    """Reusable dependency for project access checking."""

    def __init__(self, project_id_param: str = "project_id"):
        self.project_id_param = project_id_param

    async def __call__(
        self,
        project_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> Project:
        return await verify_project_access(project_id, user, db)
