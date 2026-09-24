from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from db.models_adaptation import Adaptation
from db.models_codex import CodexEntry
from db.models_core import ChapterBody, Project
from db.models_org import Org, OrgMember


def codex_entry(entry_id: str, project_id: str, kind: str, name: str, status: str = "confirmed") -> CodexEntry:
    return CodexEntry(
        id=entry_id,
        project_id=project_id,
        kind=kind,
        name=name,
        description="",
        attrs={},
        resident=False,
        status=status,
        ref_chapters=[],
        conflicts=[],
    )


@pytest.mark.asyncio
async def test_adaptation_references_must_belong_to_parent_project(
    app_client,
    async_db_session,
    seed_project,
    make_project,
    make_chapter,
    auth_headers,
):
    await seed_project(project_id="project-a", chapter_ids=("chapter-a",))
    async_db_session.add(make_project("project-b", owner_id="user_a"))
    await async_db_session.flush()
    async_db_session.add(make_chapter("chapter-b", project_id="project-b"))
    async_db_session.add_all([
        codex_entry("character-a", "project-a", "character", "阿青"),
        codex_entry("location-a", "project-a", "location", "旧宅"),
        codex_entry("item-a", "project-a", "item", "木簪"),
        codex_entry("character-b", "project-b", "character", "外部人物"),
    ])
    async_db_session.add(Adaptation(id="adaptation-a", project_id="project-a", title="第一季"))
    await async_db_session.commit()
    headers = auth_headers("user_a")

    empty_episode = await app_client.post(
        "/adaptations/adaptation-a/episodes",
        headers=headers,
        json={"number": 1, "title": "没有来源", "source_chapter_ids": [], "target_duration": 90},
    )
    assert empty_episode.status_code == 422

    invalid_episode = await app_client.post(
        "/adaptations/adaptation-a/episodes",
        headers=headers,
        json={"number": 1, "title": "越界来源", "source_chapter_ids": ["chapter-b"], "target_duration": 90},
    )
    assert invalid_episode.status_code == 422
    assert invalid_episode.json()["detail"]["code"] == "invalid_source_chapters"

    episode = await app_client.post(
        "/adaptations/adaptation-a/episodes",
        headers=headers,
        json={"number": 1, "title": "第一集", "source_chapter_ids": ["chapter-a", "chapter-a"], "target_duration": 90},
    )
    assert episode.status_code == 201
    assert episode.json()["source_chapter_ids"] == ["chapter-a"]
    episode_id = episode.json()["id"]

    empty_episode_patch = await app_client.patch(
        f"/episodes/{episode_id}",
        headers=headers,
        json={"source_chapter_ids": []},
    )
    assert empty_episode_patch.status_code == 422

    wrong_location = await app_client.post(
        f"/episodes/{episode_id}/scenes",
        headers=headers,
        json={"order": 1, "purpose": "发现线索", "location_entry_id": "item-a", "character_entry_ids": []},
    )
    assert wrong_location.status_code == 422
    assert wrong_location.json()["detail"]["code"] == "scene_location_must_be_location"

    scene = await app_client.post(
        f"/episodes/{episode_id}/scenes",
        headers=headers,
        json={
            "order": 1,
            "purpose": "发现线索",
            "location_entry_id": "location-a",
            "character_entry_ids": ["character-a", "character-a"],
        },
    )
    assert scene.status_code == 201
    assert scene.json()["character_entry_ids"] == ["character-a"]

    cross_project_character = await app_client.patch(
        f"/scenes/{scene.json()['id']}",
        headers=headers,
        json={"character_entry_ids": ["character-b"]},
    )
    assert cross_project_character.status_code == 422
    assert cross_project_character.json()["detail"]["code"] == "invalid_scene_references"


@pytest.mark.asyncio
async def test_visual_profile_requires_unique_project_character_and_manage_codex_permission(
    app_client,
    async_db_session,
    seed_project,
    make_user,
    auth_headers,
):
    await seed_project(project_id="project-a")
    async_db_session.add(make_user("outsider"))
    async_db_session.add_all([
        codex_entry("character-a", "project-a", "character", "阿青"),
        codex_entry("location-a", "project-a", "location", "旧宅"),
        Adaptation(id="adaptation-a", project_id="project-a", title="第一季"),
    ])
    await async_db_session.commit()

    forbidden = await app_client.post(
        "/adaptations/adaptation-a/visual-profiles",
        headers=auth_headers("outsider"),
        json={"codex_entry_id": "character-a", "display_name": "阿青"},
    )
    assert forbidden.status_code == 403

    wrong_kind = await app_client.post(
        "/adaptations/adaptation-a/visual-profiles",
        headers=auth_headers("user_a"),
        json={"codex_entry_id": "location-a", "display_name": "旧宅"},
    )
    assert wrong_kind.status_code == 422
    assert wrong_kind.json()["detail"]["code"] == "visual_profile_requires_character"

    created = await app_client.post(
        "/adaptations/adaptation-a/visual-profiles",
        headers=auth_headers("user_a"),
        json={"codex_entry_id": "character-a", "display_name": "阿青", "appearance": "杏眼，黑色长发"},
    )
    assert created.status_code == 201
    assert created.json()["codex_entry_id"] == "character-a"

    duplicate = await app_client.post(
        "/adaptations/adaptation-a/visual-profiles",
        headers=auth_headers("user_a"),
        json={"codex_entry_id": "character-a", "display_name": "重复档案"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "visual_profile_already_exists"


@pytest.mark.asyncio
async def test_visual_profile_unique_race_returns_stable_conflict(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
    monkeypatch,
):
    await seed_project(project_id="project-a")
    async_db_session.add_all([
        codex_entry("character-a", "project-a", "character", "阿青"),
        Adaptation(id="adaptation-a", project_id="project-a", title="第一季"),
    ])
    await async_db_session.commit()
    commit = AsyncMock(side_effect=IntegrityError("insert visual profile", {}, Exception("unique violation")))
    rollback = AsyncMock()
    monkeypatch.setattr(async_db_session, "commit", commit)
    monkeypatch.setattr(async_db_session, "rollback", rollback)

    response = await app_client.post(
        "/adaptations/adaptation-a/visual-profiles",
        headers=auth_headers("user_a"),
        json={"codex_entry_id": "character-a", "display_name": "阿青"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "visual_profile_already_exists"
    rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_production_package_is_ordered_and_never_exports_source_body(
    app_client, async_db_session, seed_project, auth_headers,
):
    chapters = await seed_project(project_id="project-a", chapter_ids=("chapter-a", "chapter-b"))
    chapters[0].title = "第一章"
    chapters[1].title = "第二章"
    async_db_session.add(ChapterBody(
        chapter_id="chapter-a", content_html="PRIVATE_NOVEL_PROSE",
        content_json={"type": "doc", "content": []},
    ))
    async_db_session.add(codex_entry("character-a", "project-a", "character", "阿青"))
    await async_db_session.flush()
    async_db_session.add(Adaptation(
        id="adaptation-a", project_id="project-a", title="竖屏第一季",
        style_profile={"label": "水墨", "private_key": "PRIVATE_CONFIG_SECRET"},
    ))
    await async_db_session.commit()
    headers = auth_headers("user_a")
    episode = (await app_client.post("/adaptations/adaptation-a/episodes", headers=headers, json={
        "number": 1, "title": "第一集", "source_chapter_ids": ["chapter-b", "chapter-a"],
        "target_duration": 10,
    })).json()
    scene_late = (await app_client.post(f"/episodes/{episode['id']}/scenes", headers=headers, json={
        "order": 2, "purpose": "收束", "character_entry_ids": ["character-a"],
    })).json()
    scene_early = (await app_client.post(f"/episodes/{episode['id']}/scenes", headers=headers, json={
        "order": 1, "purpose": "开场", "character_entry_ids": ["character-a"],
    })).json()
    for scene_id, order in ((scene_late["id"], 2), (scene_early["id"], 1)):
        shot = (await app_client.post(f"/scenes/{scene_id}/shots", headers=headers, json={
            "order": order, "duration_target": 5, "visual_prompt": "人物站在门前",
        })).json()
        await app_client.patch(f"/shots/{shot['id']}", headers=headers, json={"status": "approved"})
    profile = (await app_client.post("/adaptations/adaptation-a/visual-profiles", headers=headers, json={
        "codex_entry_id": "character-a", "display_name": "阿青", "appearance": "黑发，眉间痣",
    })).json()
    await app_client.patch(f"/visual-profiles/{profile['id']}", headers=headers, json={"locked": True})

    response = await app_client.get(f"/episodes/{episode['id']}/production-package", headers=headers)

    assert response.status_code == 200
    package = response.json()
    assert package["schema_version"] == 1
    assert [chapter["id"] for chapter in package["source_chapters"]] == ["chapter-b", "chapter-a"]
    assert [scene["purpose"] for scene in package["scenes"]] == ["开场", "收束"]
    assert package["visual_profiles"][0]["version"] == 1
    assert package["readiness"] == {"ready": True, "total_duration": 10, "target_duration": 10, "issues": []}
    assert "PRIVATE_NOVEL_PROSE" not in response.text
    assert "PRIVATE_CONFIG_SECRET" not in response.text


@pytest.mark.asyncio
async def test_production_package_reports_blockers_and_requires_export_permission(
    app_client, async_db_session, seed_project, make_user, auth_headers,
):
    await seed_project(project_id="project-a", chapter_ids=("chapter-a",))
    async_db_session.add(make_user("outsider"))
    async_db_session.add(codex_entry("character-a", "project-a", "character", "阿青"))
    await async_db_session.flush()
    async_db_session.add(Adaptation(id="adaptation-a", project_id="project-a", title="第一季"))
    await async_db_session.commit()
    headers = auth_headers("user_a")
    episode = (await app_client.post("/adaptations/adaptation-a/episodes", headers=headers, json={
        "number": 1, "title": "第一集", "source_chapter_ids": ["chapter-a"], "target_duration": 90,
    })).json()
    path = f"/episodes/{episode['id']}/production-package"
    assert (await app_client.get(path, headers=auth_headers("outsider"))).status_code == 403
    empty = (await app_client.get(path, headers=headers)).json()
    assert [item["code"] for item in empty["readiness"]["issues"]] == ["no_scenes"]

    scene = (await app_client.post(f"/episodes/{episode['id']}/scenes", headers=headers, json={
        "order": 1, "purpose": "转折", "character_entry_ids": ["character-a"],
    })).json()
    shot = (await app_client.post(f"/scenes/{scene['id']}/shots", headers=headers, json={
        "order": 1, "duration_target": 4, "visual_prompt": "  ",
    })).json()
    result = (await app_client.get(path, headers=headers)).json()
    codes = {item["code"] for item in result["readiness"]["issues"]}
    assert {"visual_profile_missing", "shot_unapproved", "visual_prompt_missing", "duration_mismatch"} <= codes
    assert not result["readiness"]["ready"]
    assert {item["code"]: item["severity"] for item in result["readiness"]["issues"]}["duration_mismatch"] == "warning"

    profile = (await app_client.post("/adaptations/adaptation-a/visual-profiles", headers=headers, json={
        "codex_entry_id": "character-a", "display_name": "阿青",
    })).json()
    await app_client.patch(f"/shots/{shot['id']}", headers=headers, json={
        "status": "approved", "visual_prompt": "竖屏近景",
    })
    result = (await app_client.get(path, headers=headers)).json()
    codes = {item["code"] for item in result["readiness"]["issues"]}
    assert {"visual_profile_unlocked", "visual_profile_appearance_missing"} <= codes
    await app_client.patch(f"/visual-profiles/{profile['id']}", headers=headers, json={
        "appearance": "黑发", "locked": True,
    })
    final = (await app_client.get(path, headers=headers)).json()
    assert final["readiness"]["ready"]
    assert [item["code"] for item in final["readiness"]["issues"]] == ["duration_mismatch"]


@pytest.mark.asyncio
async def test_production_package_viewer_can_see_adaptation_but_cannot_export(
    app_client, async_db_session, seed_project, make_user, auth_headers,
):
    await seed_project(project_id="project-a", chapter_ids=("chapter-a",))
    async_db_session.add_all([make_user("viewer"), Org(id="org-a", name="制作团队")])
    await async_db_session.flush()
    project = await async_db_session.get(Project, "project-a")
    project.org_id = "org-a"
    async_db_session.add(OrgMember(org_id="org-a", user_id="viewer", role="viewer"))
    async_db_session.add(Adaptation(id="adaptation-a", project_id="project-a", title="第一季"))
    await async_db_session.commit()
    episode = (await app_client.post("/adaptations/adaptation-a/episodes", headers=auth_headers("user_a"), json={
        "number": 1, "title": "第一集", "source_chapter_ids": ["chapter-a"],
    })).json()
    assert (await app_client.get("/projects/project-a/adaptations", headers=auth_headers("viewer"))).status_code == 200
    assert (await app_client.get(
        f"/episodes/{episode['id']}/production-package", headers=auth_headers("viewer")
    )).status_code == 403
