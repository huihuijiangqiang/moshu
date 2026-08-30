"""
Authentication and authorization dependencies
"""
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models_core import Project, User
from db.models_org import OrgMember
from db.session import get_db

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Decode JWT and return current user"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        token = credentials.credentials
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    return user


async def verify_project_access(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """Verify user has access to project (owner or org member)"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Check if user is owner
    if project.owner_id == user.id:
        return project

    # Check if user is org member (if project has org)
    if project.org_id:
        org_result = await db.execute(
            select(OrgMember)
            .where(OrgMember.org_id == project.org_id)
            .where(OrgMember.user_id == user.id)
        )
        if org_result.scalar_one_or_none():
            return project

    raise HTTPException(status_code=403, detail="Access denied")


class ProjectAccessChecker:
    """Reusable dependency for project access checking"""

    def __init__(self, project_id_param: str = "project_id"):
        self.project_id_param = project_id_param

    async def __call__(
        self,
        project_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> Project:
        return await verify_project_access(project_id, user, db)
