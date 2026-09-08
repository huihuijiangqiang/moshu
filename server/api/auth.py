"""Authentication endpoints and authorization dependencies."""

import asyncio
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
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models_admin import AdminAuditLog, AuthSession, SystemSetting
from db.models_auth_security import PasswordResetToken
from db.models_core import Chapter, Project, User
from db.models_org import ChapterAssignment, OrgMember
from db.session import get_db
from services.usage import next_month_start

router = APIRouter()
security = HTTPBearer(auto_error=False)
_PASSWORD_ITERATIONS = 210_000
# SQLite has no transaction-level advisory lock.  This lock still makes the
# bootstrap endpoint safe in the supported single-process SQLite deployment;
# PostgreSQL uses pg_advisory_xact_lock below for multi-worker safety.
_BOOTSTRAP_SQLITE_LOCK = asyncio.Lock()


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


class BootstrapRequest(RegisterRequest):
    """Credentials for the one-time first administrator bootstrap."""

    bootstrap_token: str = Field(min_length=16, max_length=512)


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


class AuthSessionOut(BaseModel):
    id: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    current: bool
    active: bool


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    new_password: str = Field(min_length=8, max_length=128)


class PasswordResetRequestOut(BaseModel):
    accepted: bool = True


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


def _session_out(session: AuthSession, current_session_id: str | None, now: datetime) -> AuthSessionOut:
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return AuthSessionOut(
        id=session.id,
        created_at=session.created_at,
        last_used_at=session.last_used_at,
        expires_at=expires_at,
        revoked_at=session.revoked_at,
        current=session.id == current_session_id,
        active=session.revoked_at is None and expires_at > now,
    )


def _token_digest(token: str) -> str:
    """Hash reset credentials before persistence; never log or store plaintext."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_id_from_credentials(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None:
        raise _credentials_error()
    payload = _decode_token(credentials.credentials, "access")
    session_id = payload.get("sid")
    if payload.get("type") != "access" or not isinstance(session_id, str):
        raise _credentials_error()
    return session_id


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


async def _register_user(request: RegisterRequest, db: AsyncSession) -> TokenResponse:
    """Create a regular account and commit its session."""
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


async def _run_bootstrap_locked(db: AsyncSession, operation):
    """Run an account-creation operation under the bootstrap serialization lock."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('moshu:auth-bootstrap'))"))
        try:
            return await operation()
        except Exception:
            await db.rollback()
            raise

    async with _BOOTSTRAP_SQLITE_LOCK:
        try:
            return await operation()
        except Exception:
            await db.rollback()
            raise


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    # Once a deployment exposes the bootstrap endpoint, ordinary registration
    # cannot race it for the empty database.  After the first admin exists,
    # registration continues under the same lock and retains its old policy.
    if settings.bootstrap_token:
        async def guarded_register() -> TokenResponse:
            user_count = await db.scalar(select(func.count(User.id)))
            if not user_count:
                raise HTTPException(status_code=409, detail={"code": "BOOTSTRAP_REQUIRED"})
            return await _register_user(request, db)

        return await _run_bootstrap_locked(db, guarded_register)
    return await _register_user(request, db)


async def _bootstrap_user(request: BootstrapRequest, db: AsyncSession) -> TokenResponse:
    """Create the first account while the caller holds the bootstrap lock."""
    user_count = await db.scalar(select(func.count(User.id)))
    if user_count:
        raise HTTPException(status_code=409, detail={"code": "BOOTSTRAP_ALREADY_COMPLETED"})

    user = User(
        id=f"u_{secrets.token_hex(12)}",
        name=request.name.strip(),
        email=request.email,
        password_hash=hash_password(request.password),
        plan="studio",
        system_role="super_admin",
        is_active=True,
        quota_remaining=0,
        quota_total=0,
        quota_resets_at=next_month_start(datetime.now(UTC)),
    )
    db.add(user)
    await db.flush()
    db.add(
        AdminAuditLog(
            # No authenticated actor exists during bootstrap.  Keep the
            # nullable actor field empty and identify the bootstrap action in
            # the immutable detail payload instead of attributing it to the
            # account being created.
            actor_id=None,
            action="auth.bootstrap",
            target_type="user",
            target_id=user.id,
            detail={"method": "environment_token", "system_role": "super_admin", "plan": "studio"},
            created_at=datetime.now(UTC),
        )
    )
    return await _token_response(user, db)


@router.post("/bootstrap", response_model=TokenResponse, status_code=201)
async def bootstrap(request: BootstrapRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Atomically create the first system super administrator.

    This endpoint is intentionally unavailable unless ``BOOTSTRAP_TOKEN`` is
    configured in the process environment.  It never accepts an existing JWT;
    after any user exists it permanently returns ``BOOTSTRAP_ALREADY_COMPLETED``.
    Remove the environment variable after successful setup.
    """
    configured = settings.bootstrap_token
    if not configured:
        raise HTTPException(status_code=503, detail={"code": "BOOTSTRAP_NOT_CONFIGURED"})
    if not hmac.compare_digest(request.bootstrap_token, configured):
        raise HTTPException(status_code=401, detail={"code": "INVALID_BOOTSTRAP_TOKEN"})

    # PostgreSQL's transaction-scoped advisory lock serializes all workers.
    # SQLite is only used for local/single-process tests and development.
    return await _run_bootstrap_locked(db, lambda: _bootstrap_user(request, db))


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


@router.get("/sessions", response_model=list[AuthSessionOut])
async def list_auth_sessions(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AuthSessionOut]:
    """List only the authenticated user's browser sessions."""
    current_session_id = _session_id_from_credentials(credentials)
    now = datetime.now(UTC)
    result = await db.execute(
        select(AuthSession)
        .where(AuthSession.user_id == user.id)
        .order_by(AuthSession.created_at.desc())
        .limit(100)
    )
    return [_session_out(row, current_session_id, now) for row in result.scalars().all()]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_auth_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke one session owned by the current account (including current)."""
    session = await db.scalar(
        select(AuthSession).where(AuthSession.id == session_id, AuthSession.user_id == user.id)
    )
    if session is None:
        raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND"})
    if session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        await db.commit()


@router.post("/password/change", status_code=204)
async def change_password(
    request: PasswordChangeRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Change the password and invalidate every other browser session."""
    if not verify_password(request.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail={"code": "CURRENT_PASSWORD_INVALID"})
    if request.current_password == request.new_password:
        raise HTTPException(status_code=400, detail={"code": "PASSWORD_UNCHANGED"})
    current_session_id = _session_id_from_credentials(credentials)
    user.password_hash = hash_password(request.new_password)
    await db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user.id,
            AuthSession.id != current_session_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()


# Password reset delivery and endpoints are defined below; credentials stay out of storage and logs.


async def _deliver_password_reset_token(email: str, token: str, expires_at: datetime) -> None:
    """Delivery seam for a mail provider; plaintext is never persisted or logged."""
    return None


@router.post("/password-reset/request", response_model=PasswordResetRequestOut, status_code=202)
async def request_password_reset(
    request: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
) -> PasswordResetRequestOut:
    """Start reset flow with a generic response to prevent email enumeration."""
    email = request.email.strip().lower()
    user = await db.scalar(
        select(User)
        .where(User.email == email, User.is_active.is_(True))
        .with_for_update()
    )
    if user is not None:
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=20)
        token = secrets.token_urlsafe(32)
        await db.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
            .values(used_at=now)
        )
        db.add(
            PasswordResetToken(
                id=f"prt_{secrets.token_hex(12)}",
                user_id=user.id,
                token_hash=_token_digest(token),
                expires_at=expires_at,
            )
        )
        await db.commit()
        await _deliver_password_reset_token(email, token, expires_at)
    else:
        await db.rollback()
    return PasswordResetRequestOut()


@router.post("/password-reset/confirm", status_code=204)
async def confirm_password_reset(
    request: PasswordResetConfirmRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Consume one unexpired reset credential and revoke all sessions."""
    now = datetime.now(UTC)
    row = await db.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == _token_digest(request.token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=400, detail={"code": "INVALID_RESET_TOKEN"})
    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail={"code": "INVALID_RESET_TOKEN"})
    user.password_hash = hash_password(request.new_password)
    row.used_at = now
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
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
    MANAGE_TIMELINE = "manage_timeline"
    RUN_GUARD = "run_guard"
    RESOLVE_GUARD = "resolve_guard"
    GENERATE = "generate"
    EXPORT = "export"
    MANAGE_PROJECT = "manage_project"
    MANAGE_MEMBERS = "manage_members"
    REVIEW_CHAPTER = "review_chapter"


_ORG_ROLE_PERMISSIONS: dict[str, frozenset[ProjectPermission]] = {
    "owner": frozenset(ProjectPermission),
    "lead": frozenset({
        ProjectPermission.VIEW,
        ProjectPermission.EDIT_BODY,
        ProjectPermission.MANAGE_OUTLINE,
        ProjectPermission.MANAGE_CODEX,
        ProjectPermission.MANAGE_TIMELINE,
        ProjectPermission.RUN_GUARD,
        ProjectPermission.RESOLVE_GUARD,
        ProjectPermission.GENERATE,
        ProjectPermission.EXPORT,
        ProjectPermission.REVIEW_CHAPTER,
    }),
    "writer": frozenset({
        ProjectPermission.VIEW,
        ProjectPermission.EDIT_BODY,
        ProjectPermission.MANAGE_OUTLINE,
        ProjectPermission.MANAGE_TIMELINE,
        ProjectPermission.GENERATE,
    }),
    "editor": frozenset({
        ProjectPermission.VIEW,
        ProjectPermission.EDIT_BODY,
        ProjectPermission.MANAGE_CODEX,
        ProjectPermission.MANAGE_TIMELINE,
        ProjectPermission.RESOLVE_GUARD,
        ProjectPermission.EXPORT,
        ProjectPermission.REVIEW_CHAPTER,
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


async def verify_chapter_assignment(
    chapter: Chapter,
    user: User,
    db: AsyncSession,
) -> None:
    """Keep an active studio assignment exclusive to its writer.

    Unassigned chapters remain editable for backward compatibility. Owners, leads,
    and editors may still intervene; only a writer assigned to a different active
    chapter task is rejected.
    """
    project = await db.get(Project, chapter.project_id)
    if project is None or project.owner_id == user.id or not project.org_id:
        return
    membership = await db.scalar(
        select(OrgMember).where(
            OrgMember.org_id == project.org_id,
            OrgMember.user_id == user.id,
        )
    )
    if membership is None or membership.role != "writer":
        return
    assignment = await db.scalar(
        select(ChapterAssignment)
        .where(
            ChapterAssignment.chapter_id == chapter.id,
            ChapterAssignment.status.in_(["assigned", "claimed"]),
        )
        .order_by(ChapterAssignment.id.desc())
        .limit(1)
    )
    if assignment is not None and assignment.assigned_to != user.id:
        raise HTTPException(
            status_code=403,
            detail={"code": "CHAPTER_ASSIGNED_TO_ANOTHER_WRITER"},
        )


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
