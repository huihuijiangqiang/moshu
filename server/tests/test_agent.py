"""Approval-gated Agent harness API tests."""

from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from api.agent import ActionProposal
from db.models_agent import AgentAction


def _proposal(action_type: str, parameters: dict, title: str = "测试动作"):
    return ActionProposal(type=action_type, title=title, parameters=parameters)


async def _new_session(app_client, auth_headers, project_id=None, user_id="user_a"):
    response = await app_client.post(
        "/agent/sessions",
        headers=auth_headers(user_id),
        json={"project_id": project_id, "title": "验收会话"},
    )
    assert response.status_code == 201
    return response.json()["id"]


async def test_agent_requires_authentication(app_client):
    assert (await app_client.post("/agent/sessions", json={"title": "匿名"})).status_code == 401


async def test_agent_session_is_private_to_owner(app_client, seed_project, auth_headers, make_user, async_db_session):
    await seed_project()
    async_db_session.add(make_user("user_b"))
    await async_db_session.commit()
    session_id = await _new_session(app_client, auth_headers, "proj_a")

    response = await app_client.get(f"/agent/sessions/{session_id}/messages", headers=auth_headers("user_b"))

    assert response.status_code == 404


async def test_invalid_model_action_is_dropped_without_breaking_chat(app_client, auth_headers, make_user, async_db_session):
    async_db_session.add(make_user("user_a"))
    await async_db_session.commit()
    session_id = await _new_session(app_client, auth_headers)
    with patch("api.agent._model_reply", new=AsyncMock(return_value=("已收到", [
        _proposal("create_project", {"title": "越界", "target_words_daily": 999_999}),
        _proposal("create_codex_entry", {"kind": "sql", "name": "不应落库"}),
    ]))):
        response = await app_client.post(
            f"/agent/sessions/{session_id}/messages",
            headers=auth_headers("user_a"),
            json={"content": "执行危险动作"},
        )

    assert response.status_code == 200
    assert response.json()["actions"] == []


async def test_rejected_action_cannot_be_approved_again(app_client, auth_headers, make_user, async_db_session):
    async_db_session.add(make_user("user_a"))
    await async_db_session.commit()
    session_id = await _new_session(app_client, auth_headers)
    proposal = _proposal("create_project", {"title": "被拒绝的书", "target_words_daily": 3000})
    with patch("api.agent._model_reply", new=AsyncMock(return_value=("请确认", [proposal]))):
        chat = await app_client.post(
            f"/agent/sessions/{session_id}/messages",
            headers=auth_headers("user_a"),
            json={"content": "建一本书"},
        )
    action_id = chat.json()["actions"][0]["id"]

    rejected = await app_client.post(
        f"/agent/actions/{action_id}/decision",
        headers=auth_headers("user_a"),
        json={"decision": "reject"},
    )
    again = await app_client.post(
        f"/agent/actions/{action_id}/decision",
        headers=auth_headers("user_a"),
        json={"decision": "approve"},
    )

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert again.status_code == 409


async def test_project_action_rechecks_permission_at_approval(app_client, seed_project, auth_headers, make_user, async_db_session):
    await seed_project()
    async_db_session.add(make_user("user_b"))
    await async_db_session.commit()
    session_id = await _new_session(app_client, auth_headers, "proj_a")
    proposal = _proposal("create_codex_entry", {
        "project_id": "proj_a", "kind": "character", "name": "不应写入", "description": "越权测试",
    })
    with patch("api.agent._model_reply", new=AsyncMock(return_value=("请确认", [proposal]))):
        chat = await app_client.post(
            f"/agent/sessions/{session_id}/messages", headers=auth_headers("user_a"), json={"content": "新增人物"}
        )
    action_id = chat.json()["actions"][0]["id"]

    denied = await app_client.post(
        f"/agent/actions/{action_id}/decision", headers=auth_headers("user_b"), json={"decision": "approve"}
    )

    assert denied.status_code == 404


async def test_update_outline_with_stale_revision_fails_without_mutating_outline(
    app_client, seed_project, auth_headers, async_db_session
):
    chapters = await seed_project(chapter_ids=("ch_outline",))
    session_id = await _new_session(app_client, auth_headers, "proj_a")
    proposal = _proposal("update_outline", {
        "chapter_id": chapters[0].id,
        "title": "新标题",
        "nodes": ["新节点"],
        "base_outline_revision": 99,
        "body_policy": "plan_only",
    })
    with patch("api.agent._model_reply", new=AsyncMock(return_value=("请确认", [proposal]))):
        chat = await app_client.post(
            f"/agent/sessions/{session_id}/messages", headers=auth_headers("user_a"), json={"content": "更新章纲"}
        )
    action_id = chat.json()["actions"][0]["id"]
    result = await app_client.post(
        f"/agent/actions/{action_id}/decision", headers=auth_headers("user_a"), json={"decision": "approve"}
    )
    await async_db_session.refresh(chapters[0])

    assert result.status_code == 200
    assert result.json()["status"] == "failed"
    assert chapters[0].title == "Chapter ch_outline"


async def test_approved_create_project_is_idempotent_on_second_decision(
    app_client, auth_headers, make_user, async_db_session
):
    async_db_session.add(make_user("user_a"))
    await async_db_session.commit()
    session_id = await _new_session(app_client, auth_headers)
    proposal = _proposal("create_project", {"title": "幂等测试", "target_words_daily": 3000})
    with patch("api.agent._model_reply", new=AsyncMock(return_value=("请确认", [proposal]))):
        chat = await app_client.post(
            f"/agent/sessions/{session_id}/messages", headers=auth_headers("user_a"), json={"content": "创建"}
        )
    action_id = chat.json()["actions"][0]["id"]
    first = await app_client.post(
        f"/agent/actions/{action_id}/decision", headers=auth_headers("user_a"), json={"decision": "approve"}
    )
    second = await app_client.post(
        f"/agent/actions/{action_id}/decision", headers=auth_headers("user_a"), json={"decision": "approve"}
    )
    projects = (await async_db_session.execute(select(AgentAction).where(AgentAction.id == action_id))).scalar_one()

    assert first.json()["status"] == "succeeded"
    assert second.status_code == 409
    assert projects.status == "succeeded"
