"""Authentication endpoints and authorization dependencies."""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models_core import Project, User
from db.models_org import OrgMember
from db.session import get_db

router = APIRouter()
security = HTTPBearer(auto_error=False)
_PASSWORD_ITERATIONS = 210_000


class UserOut(BaseModel):
    id: str
    name: str
    email: str | None
    plan: str


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


def _encode_token(user_id: str, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "type": token_type,
            "iat": now,
            "exp": now + expires_delta,
            "jti": secrets.token_hex(12),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def _token_response(user: User) -> TokenResponse:
    access_seconds = settings.access_token_expire_minutes * 60
    return TokenResponse(
        access_token=_encode_token(user.id, "access", timedelta(seconds=access_seconds)),
        refresh_token=_encode_token(user.id, "refresh", timedelta(days=settings.refresh_token_expire_days)),
        expires_in=access_seconds,
        user=UserOut(id=user.id, name=user.name, email=user.email, plan=user.plan),
    )


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_token(token: str, expected_type: str) -> str:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        token_type = payload.get("type")
        legacy_access = expected_type == "access" and token_type is None
        if not isinstance(user_id, str) or (token_type != expected_type and not legacy_access):
            raise _credentials_error()
        return user_id
    except JWTError as error:
        raise _credentials_error() from error


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    existing = await db.execute(select(User.id).where(User.email == request.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail={"code": "EMAIL_ALREADY_REGISTERED"})

    user = User(
        id=f"u_{secrets.token_hex(12)}",
        name=request.name.strip(),
        email=request.email,
        password_hash=hash_password(request.password),
        plan="free",
        quota_remaining=0,
        quota_total=0,
    )
    db.add(user)
    await db.commit()
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    email = request.email.strip().lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail={"code": "INVALID_CREDENTIALS"})
    return _token_response(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user_id = _decode_token(request.refresh_token, "refresh")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise _credentials_error()
    return _token_response(user)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Decode an access JWT and return the current user."""
    if credentials is None:
        raise _credentials_error()
    user_id = _decode_token(credentials.credentials, "access")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise _credentials_error()
    return user


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, name=user.name, email=user.email, plan=user.plan)


async def verify_project_access(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """Verify user has access to project as owner or organization member."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id == user.id:
        return project
    if project.org_id:
        org_result = await db.execute(
            select(OrgMember).where(OrgMember.org_id == project.org_id, OrgMember.user_id == user.id)
        )
        if org_result.scalar_one_or_none():
            return project
    raise HTTPException(status_code=403, detail="Access denied")


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
