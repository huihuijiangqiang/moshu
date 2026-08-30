"""
Tests for API authentication and project access control
"""
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.auth import get_current_user, verify_project_access
from db import Organization, Project, User
from db.models_core import OrgMember


@pytest.mark.asyncio
async def test_verify_project_access_owner(async_db_session):
    """Test that project owner has access"""
    # Create user
    user = User(
        id="user_owner",
        username="owner",
        email="owner@test.com",
        hashed_password="hash",
    )
    async_db_session.add(user)

    # Create project owned by user
    project = Project(
        id="proj_test",
        owner_id="user_owner",
        name="Test Project",
    )
    async_db_session.add(project)
    await async_db_session.commit()

    # Verify access - should not raise
    await verify_project_access("proj_test", user, async_db_session)


@pytest.mark.asyncio
async def test_verify_project_access_org_member(async_db_session):
    """Test that org member has access to org project"""
    # Create organization
    org = Organization(
        id="org_test",
        name="Test Org",
        owner_id="user_org_owner",
    )
    async_db_session.add(org)

    # Create user who is org member
    user = User(
        id="user_member",
        username="member",
        email="member@test.com",
        hashed_password="hash",
    )
    async_db_session.add(user)

    # Add user to org
    membership = OrgMember(
        org_id="org_test",
        user_id="user_member",
        role="member",
    )
    async_db_session.add(membership)

    # Create project owned by org
    project = Project(
        id="proj_org",
        owner_id="org_test",
        name="Org Project",
    )
    async_db_session.add(project)
    await async_db_session.commit()

    # Verify access - should not raise
    await verify_project_access("proj_org", user, async_db_session)


@pytest.mark.asyncio
async def test_verify_project_access_denied(async_db_session):
    """Test that non-owner/non-member is denied access"""
    # Create project owner
    owner = User(
        id="user_owner",
        username="owner",
        email="owner@test.com",
        hashed_password="hash",
    )
    async_db_session.add(owner)

    # Create different user
    other_user = User(
        id="user_other",
        username="other",
        email="other@test.com",
        hashed_password="hash",
    )
    async_db_session.add(other_user)

    # Create project
    project = Project(
        id="proj_private",
        owner_id="user_owner",
        name="Private Project",
    )
    async_db_session.add(project)
    await async_db_session.commit()

    # Verify access should raise 403
    with pytest.raises(HTTPException) as exc_info:
        await verify_project_access("proj_private", other_user, async_db_session)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_verify_project_access_nonexistent_project(async_db_session):
    """Test that nonexistent project raises 404"""
    user = User(
        id="user_test",
        username="test",
        email="test@test.com",
        hashed_password="hash",
    )
    async_db_session.add(user)
    await async_db_session.commit()

    # Verify access to nonexistent project should raise 404
    with pytest.raises(HTTPException) as exc_info:
        await verify_project_access("proj_nonexistent", user, async_db_session)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_consistency_api_requires_auth(client):
    """Test that consistency API endpoints require authentication"""
    # Attempt to access without auth header should return 403 or 401
    response = await client.post("/consistency/projects/proj_test/scan")
    assert response.status_code in [401, 403]


@pytest.mark.asyncio
async def test_chapters_api_requires_idempotency_key(client, auth_headers):
    """Test that chapter body save requires Idempotency-Key"""
    # Attempt to save without Idempotency-Key should return 422
    response = await client.put(
        "/chapters/ch_test/body",
        json={
            "content_html": "<p>Test</p>",
            "content_json": {},
            "base_rev": 0,
        },
        headers=auth_headers,
    )
    assert response.status_code == 422
