"""Author-maintained Codex state history API and service behavior."""

from sqlalchemy import func, select

from db.models_codex import CodexEntry, CodexStateChange


def codex_entry(entry_id: str, project_id: str, *, status: str = "confirmed") -> CodexEntry:
    return CodexEntry(
        id=entry_id,
        project_id=project_id,
        kind="character",
        name="沈禾",
        description="穿越后在青石村落脚。",
        attrs={},
        resident=True,
        status=status,
        ref_chapters=[],
        conflicts=[],
    )


async def test_state_history_crud_is_versioned_and_soft_deleted(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(chapter_ids=("ch_1", "ch_2"))
    async_db_session.add(codex_entry("cx_main", "proj_a"))
    await async_db_session.commit()

    created = await app_client.post(
        "/codex/proj_a/entries/cx_main/states",
        headers=auth_headers("user_a"),
        json={
            "chapter_id": "ch_1",
            "state_key": "居所",
            "value": "青石村周家旧屋",
            "note": "刚落脚时借住",
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["source"] == "author"
    assert item["editable"] is True
    assert item["chapter_index"] == 1024
    assert item["revision"] == 1

    updated = await app_client.put(
        f"/codex/proj_a/entries/cx_main/states/{item['id']}",
        headers=auth_headers("user_a"),
        json={
            "chapter_id": "ch_2",
            "state_key": "居所",
            "value": "村东新宅",
            "note": "买下荒地后搬入",
            "expected_revision": 1,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["chapter_id"] == "ch_2"

    stale = await app_client.put(
        f"/codex/proj_a/entries/cx_main/states/{item['id']}",
        headers=auth_headers("user_a"),
        json={
            "chapter_id": "ch_1",
            "state_key": "居所",
            "value": "旧值",
            "expected_revision": 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "CODEX_STATE_REVISION_CONFLICT",
        "current_revision": 2,
    }

    deleted = await app_client.request(
        "DELETE",
        f"/codex/proj_a/entries/cx_main/states/{item['id']}",
        headers=auth_headers("user_a"),
        json={"expected_revision": 2},
    )
    assert deleted.status_code == 204
    history = await app_client.get(
        "/codex/proj_a/entries/cx_main/states",
        headers=auth_headers("user_a"),
    )
    assert history.status_code == 200
    assert history.json() == []


async def test_state_history_merges_accepted_claims_with_auditable_source(
    app_client,
    async_db_session,
    seed_project,
    make_claim,
    auth_headers,
):
    await seed_project(chapter_ids=("ch_1", "ch_2"))
    entry = codex_entry("cx_main", "proj_a")
    async_db_session.add(entry)
    await async_db_session.flush()
    async_db_session.add(
        make_claim(
            project_id="proj_a",
            chapter_id="ch_2",
            body_rev=3,
            subject_entry_id=entry.id,
            predicate="身份",
            object_value="县城粮商",
            paragraph_id="p-7",
            confidence=0.91,
            fingerprint="state-history-claim",
        )
    )
    await async_db_session.commit()
    await app_client.post(
        "/codex/proj_a/entries/cx_main/states",
        headers=auth_headers("user_a"),
        json={"chapter_id": "ch_1", "state_key": "身份", "value": "佃户"},
    )

    response = await app_client.get(
        "/codex/proj_a/entries/cx_main/states",
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 200
    author, extracted = response.json()
    assert author["source"] == "author"
    assert extracted == {
        "id": extracted["id"],
        "source": "extracted",
        "editable": False,
        "state_key": "身份",
        "value": "县城粮商",
        "polarity": "positive",
        "note": None,
        "chapter_id": "ch_2",
        "chapter_index": 2048,
        "chapter_title": "Chapter ch_2",
        "body_revision": 3,
        "paragraph_id": "p-7",
        "confidence": 0.91,
        "revision": None,
        "created_at": extracted["created_at"],
    }


async def test_state_writes_are_project_scoped_and_require_confirmed_entry(
    app_client,
    async_db_session,
    seed_project,
    make_user,
    auth_headers,
):
    await seed_project(chapter_ids=("ch_a",))
    await seed_project(user_id="user_b", project_id="proj_b", chapter_ids=("ch_b",))
    async_db_session.add(codex_entry("cx_pending", "proj_a", status="pending"))
    await async_db_session.commit()

    unauthorized = await app_client.post(
        "/codex/proj_a/entries/cx_pending/states",
        headers=auth_headers("user_b"),
        json={"chapter_id": "ch_a", "state_key": "身份", "value": "越界"},
    )
    assert unauthorized.status_code == 403

    pending = await app_client.post(
        "/codex/proj_a/entries/cx_pending/states",
        headers=auth_headers("user_a"),
        json={"chapter_id": "ch_a", "state_key": "身份", "value": "候选"},
    )
    assert pending.status_code == 404

    async_db_session.add(codex_entry("cx_main", "proj_a"))
    await async_db_session.commit()
    foreign_chapter = await app_client.post(
        "/codex/proj_a/entries/cx_main/states",
        headers=auth_headers("user_a"),
        json={"chapter_id": "ch_b", "state_key": "身份", "value": "越界"},
    )
    assert foreign_chapter.status_code == 422
    assert foreign_chapter.json()["detail"]["code"] == "INVALID_CODEX_STATE"


async def test_duplicate_state_key_in_one_chapter_is_rejected(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(chapter_ids=("ch_a",))
    async_db_session.add(codex_entry("cx_main", "proj_a"))
    await async_db_session.commit()
    payload = {"chapter_id": "ch_a", "state_key": "居所", "value": "青石村"}
    first = await app_client.post(
        "/codex/proj_a/entries/cx_main/states",
        headers=auth_headers("user_a"),
        json=payload,
    )
    second = await app_client.post(
        "/codex/proj_a/entries/cx_main/states",
        headers=auth_headers("user_a"),
        json=payload,
    )
    assert first.status_code == 201
    assert second.status_code == 422


async def test_deleting_unused_codex_entry_cascades_author_state_history(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(chapter_ids=("ch_a",))
    async_db_session.add(codex_entry("cx_main", "proj_a"))
    await async_db_session.commit()
    created = await app_client.post(
        "/codex/proj_a/entries/cx_main/states",
        headers=auth_headers("user_a"),
        json={"chapter_id": "ch_a", "state_key": "居所", "value": "青石村"},
    )
    assert created.status_code == 201

    deleted = await app_client.delete(
        "/codex/proj_a/entries/cx_main",
        headers=auth_headers("user_a"),
    )

    assert deleted.status_code == 204, deleted.text
    remaining = await async_db_session.scalar(
        select(func.count(CodexStateChange.id)).where(
            CodexStateChange.entry_id == "cx_main"
        )
    )
    assert remaining == 0
