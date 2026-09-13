from db.models_core import Project


async def test_story_hook_lifecycle_is_revision_fenced(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(
        user_id="hook_writer",
        project_id="hook_novel",
        chapter_ids=("hook_ch_1", "hook_ch_2", "hook_ch_3"),
    )
    headers = auth_headers("hook_writer")
    created = await app_client.post(
        "/projects/hook_novel/hooks",
        headers=headers,
        json={
            "sourceChapterId": "hook_ch_1",
            "hookType": "证据缺口",
            "concreteEvent": "账册最后一页已经被人撕走。",
            "unresolvedQuestion": "谁先拿走了名单？",
            "payoffByChapter": 3,
        },
    )

    assert created.status_code == 201
    hook = created.json()
    assert hook["status"] == "open"
    assert hook["payoff_by_chapter"] == 3
    assert len(hook["novelty_signature"]) == 64

    listed = await app_client.get(
        "/projects/hook_novel/hooks?status=open",
        headers=headers,
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [hook["id"]]

    missing_payoff = await app_client.patch(
        f"/projects/hook_novel/hooks/{hook['id']}",
        headers=headers,
        json={
            "expectedRevision": 1,
            "status": "resolved",
            "resolution": "名单藏在粮车夹层。",
        },
    )
    assert missing_payoff.status_code == 422
    assert missing_payoff.json()["detail"]["code"] == "STORY_HOOK_INVALID"

    resolved = await app_client.patch(
        f"/projects/hook_novel/hooks/{hook['id']}",
        headers=headers,
        json={
            "expectedRevision": 1,
            "status": "resolved",
            "payoffChapterId": "hook_ch_3",
            "resolution": "名单藏在粮车夹层，被沈禾当众取出。",
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["revision"] == 2

    stale = await app_client.patch(
        f"/projects/hook_novel/hooks/{hook['id']}",
        headers=headers,
        json={"expectedRevision": 1, "status": "deferred"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "STORY_HOOK_REVISION_CONFLICT",
        "currentRevision": 2,
    }


async def test_story_hook_rejects_duplicate_and_cross_project_chapters(
    app_client,
    async_db_session,
    seed_project,
    make_chapter,
    make_project,
    make_user,
    auth_headers,
):
    await seed_project(
        user_id="hook_scope_writer",
        project_id="hook_scope_novel",
        chapter_ids=("hook_scope_ch",),
    )
    async_db_session.add(make_user("other_hook_writer"))
    await async_db_session.flush()
    async_db_session.add(make_project("other_hook_novel", owner_id="other_hook_writer"))
    await async_db_session.flush()
    async_db_session.add(make_chapter("other_hook_ch", project_id="other_hook_novel", idx=1024))
    await async_db_session.commit()
    assert await async_db_session.get(Project, "other_hook_novel") is not None
    headers = auth_headers("hook_scope_writer")
    payload = {
        "sourceChapterId": "hook_scope_ch",
        "hookType": "倒计时",
        "concreteEvent": "县差已经点燃第一炷香。",
        "unresolvedQuestion": "香灭前能否交出真账？",
        "payoffByChapter": 2,
    }

    first = await app_client.post("/projects/hook_scope_novel/hooks", headers=headers, json=payload)
    duplicate = await app_client.post(
        "/projects/hook_scope_novel/hooks",
        headers=headers,
        json=payload,
    )
    cross_project = await app_client.post(
        "/projects/hook_scope_novel/hooks",
        headers=headers,
        json={**payload, "sourceChapterId": "other_hook_ch"},
    )

    assert first.status_code == 201
    assert duplicate.status_code == 422
    assert cross_project.status_code == 422
