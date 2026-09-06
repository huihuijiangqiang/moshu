"""Character presence statistics and author-managed chapter POV."""

from db.models_codex import CodexEntry, CodexRef
from db.models_core import Chapter


def character(entry_id: str, project_id: str, *, status: str = "confirmed") -> CodexEntry:
    return CodexEntry(
        id=entry_id,
        project_id=project_id,
        kind="character",
        name=f"人物 {entry_id}",
        description="",
        attrs={},
        resident=False,
        status=status,
        ref_chapters=[],
        conflicts=[],
    )


async def test_character_statistics_merge_auditable_sources_without_double_counting(
    app_client,
    async_db_session,
    seed_project,
    make_claim,
    auth_headers,
):
    chapters = await seed_project(chapter_ids=("ch_1", "ch_2", "ch_3"))
    for index, chapter in enumerate(chapters, start=1):
        chapter.words = index * 100
    entry = character("cx_main", "proj_a")
    entry.ref_chapters = ["ch_1"]  # imported legacy reference, same chapter as explicit ref
    async_db_session.add(entry)
    await async_db_session.flush()
    async_db_session.add(CodexRef(chapter_id="ch_1", entry_id=entry.id, count=3))
    async_db_session.add_all(
        [
            make_claim(
                project_id="proj_a",
                chapter_id="ch_2",
                body_rev=1,
                subject_entry_id=entry.id,
                fingerprint="cx-main-subject",
            ),
            make_claim(
                project_id="proj_a",
                chapter_id="ch_3",
                body_rev=1,
                object_entry_id=entry.id,
                fingerprint="cx-main-object",
            ),
            make_claim(
                project_id="proj_a",
                chapter_id="ch_3",
                body_rev=1,
                subject_entry_id=entry.id,
                fingerprint="cx-main-old",
                status="superseded",
            ),
        ]
    )
    chapters[2].pov_entry_id = entry.id
    chapters[2].pov_revision = 1
    await async_db_session.commit()

    response = await app_client.get(
        "/codex/proj_a/entries/cx_main/statistics",
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload == {
        "entry_id": "cx_main",
        "name": "人物 cx_main",
        "appearance_chapters": 3,
        "explicit_references": 3,
        "extracted_claims": 2,
        "pov_chapters": 1,
        "pov_words": 300,
        "first_appearance": 1024,
        "last_appearance": 3072,
        "hiatus_chapters": 0,
        "chapters": [
            {
                "chapter_id": "ch_1",
                "chapter_index": 1024,
                "chapter_title": "Chapter ch_1",
                "words": 100,
                "explicit_references": 3,
                "extracted_claims": 0,
                "is_pov": False,
            },
            {
                "chapter_id": "ch_2",
                "chapter_index": 2048,
                "chapter_title": "Chapter ch_2",
                "words": 200,
                "explicit_references": 0,
                "extracted_claims": 1,
                "is_pov": False,
            },
            {
                "chapter_id": "ch_3",
                "chapter_index": 3072,
                "chapter_title": "Chapter ch_3",
                "words": 300,
                "explicit_references": 0,
                "extracted_claims": 1,
                "is_pov": True,
            },
        ],
    }


async def test_character_statistics_are_scoped_and_require_confirmed_character(
    app_client,
    async_db_session,
    seed_project,
    make_user,
    auth_headers,
):
    await seed_project()
    async_db_session.add(make_user("user_b"))
    async_db_session.add(character("cx_pending", "proj_a", status="pending"))
    await async_db_session.commit()

    assert (
        await app_client.get(
            "/codex/proj_a/entries/cx_pending/statistics",
            headers=auth_headers("user_b"),
        )
    ).status_code == 403
    assert (
        await app_client.get(
            "/codex/proj_a/entries/cx_pending/statistics",
            headers=auth_headers("user_a"),
        )
    ).status_code == 404


async def test_pov_update_is_validated_and_optimistically_locked(
    app_client,
    async_db_session,
    seed_project,
    make_project,
    make_user,
    auth_headers,
):
    await seed_project()
    valid = character("cx_valid", "proj_a")
    pending = character("cx_pending", "proj_a", status="pending")
    not_character = CodexEntry(
        id="cx_place",
        project_id="proj_a",
        kind="location",
        name="县城",
        description="",
        attrs={},
        resident=False,
        status="confirmed",
        ref_chapters=[],
        conflicts=[],
    )
    async_db_session.add(make_user("user_b"))
    await async_db_session.flush()
    async_db_session.add(make_project("proj_b", owner_id="user_b"))
    await async_db_session.flush()
    foreign = character("cx_foreign", "proj_b")
    async_db_session.add_all([valid, pending, not_character, foreign])
    await async_db_session.commit()
    chapter_id = "ch_a"

    unauthorized = await app_client.put(
        f"/chapters/{chapter_id}/pov",
        headers=auth_headers("user_b"),
        json={"entry_id": "cx_valid", "expected_revision": 0},
    )
    assert unauthorized.status_code == 403

    for invalid_id in ("cx_pending", "cx_place", "cx_foreign"):
        invalid = await app_client.put(
            f"/chapters/{chapter_id}/pov",
            headers=auth_headers("user_a"),
            json={"entry_id": invalid_id, "expected_revision": 0},
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "INVALID_POV_CHARACTER"

    first = await app_client.put(
        f"/chapters/{chapter_id}/pov",
        headers=auth_headers("user_a"),
        json={"entry_id": "cx_valid", "expected_revision": 0},
    )
    assert first.status_code == 200
    assert first.json() == {"chapter_id": chapter_id, "entry_id": "cx_valid", "revision": 1}

    stale = await app_client.put(
        f"/chapters/{chapter_id}/pov",
        headers=auth_headers("user_a"),
        json={"entry_id": None, "expected_revision": 0},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "POV_REVISION_CONFLICT",
        "current_entry_id": "cx_valid",
        "current_revision": 1,
    }

    cleared = await app_client.put(
        f"/chapters/{chapter_id}/pov",
        headers=auth_headers("user_a"),
        json={"entry_id": None, "expected_revision": 1},
    )
    assert cleared.status_code == 200
    assert cleared.json() == {"chapter_id": chapter_id, "entry_id": None, "revision": 2}
    persisted = await async_db_session.get(Chapter, chapter_id)
    assert persisted.pov_entry_id is None
    assert persisted.pov_revision == 2


async def test_chapter_list_and_detail_include_pov_metadata(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    chapter = (await seed_project())[0]
    entry = character("cx_main", "proj_a")
    async_db_session.add(entry)
    await async_db_session.flush()
    chapter.pov_entry_id = entry.id
    chapter.pov_revision = 4
    await async_db_session.commit()

    listed = await app_client.get("/projects/proj_a/chapters", headers=auth_headers("user_a"))
    detail = await app_client.get("/chapters/ch_a", headers=auth_headers("user_a"))

    assert listed.status_code == 200
    assert listed.json()[0]["pov_entry_id"] == "cx_main"
    assert listed.json()[0]["pov_revision"] == 4
    assert detail.status_code == 200
    assert detail.json()["pov_entry_id"] == "cx_main"
    assert detail.json()["pov_revision"] == 4


async def test_character_used_as_pov_cannot_be_deleted(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    chapter = (await seed_project())[0]
    entry = character("cx_main", "proj_a")
    async_db_session.add(entry)
    await async_db_session.flush()
    chapter.pov_entry_id = entry.id
    chapter.pov_revision = 1
    await async_db_session.commit()

    response = await app_client.delete(
        "/codex/proj_a/entries/cx_main",
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "CODEX_ENTRY_IN_USE",
        "reference_count": 0,
        "pov_chapter_count": 1,
    }
    assert await async_db_session.get(CodexEntry, entry.id) is not None
