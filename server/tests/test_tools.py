"""Project writing utility endpoints."""

import pytest


@pytest.mark.asyncio
async def test_name_generator_is_deterministic_for_same_request(app_client, seed_project, auth_headers):
    await seed_project()
    payload = {"style": "古典", "gender": "女", "seed": "春山", "count": 4}
    first = await app_client.post("/projects/proj_a/tools/names", json=payload, headers=auth_headers("user_a"))
    second = await app_client.post("/projects/proj_a/tools/names", json=payload, headers=auth_headers("user_a"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["names"] == second.json()["names"]
    assert len(first.json()["names"]) == 4


@pytest.mark.asyncio
async def test_map_draft_is_saved_in_project_settings(app_client, seed_project, auth_headers):
    await seed_project()
    payload = {
        "seed": "柳溪",
        "terrain": "群岛",
        "regions": [{"name": "柳溪", "x": 20, "y": 30}, {"name": "望潮港", "x": 70, "y": 60}],
    }

    saved = await app_client.put(
        "/projects/proj_a/tools/map-draft", json=payload, headers=auth_headers("user_a")
    )
    loaded = await app_client.get("/projects/proj_a/tools/map-draft", headers=auth_headers("user_a"))

    assert saved.status_code == 200
    assert loaded.status_code == 200
    assert loaded.json()["terrain"] == "群岛"
    assert loaded.json()["regions"] == payload["regions"]


@pytest.mark.asyncio
async def test_map_generator_returns_requested_region_count(app_client, seed_project, auth_headers):
    await seed_project()
    generated = await app_client.post(
        "/projects/proj_a/tools/maps",
        json={"seed": "北境", "terrain": "山河", "region_count": 8},
        headers=auth_headers("user_a"),
    )

    assert generated.status_code == 200
    assert generated.json()["saved"] is False
    assert len(generated.json()["regions"]) == 8
