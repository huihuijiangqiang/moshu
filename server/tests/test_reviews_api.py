"""End-to-end API checks for version-bound chapter review."""

from datetime import UTC, datetime

from db.models_core import ChapterBody, ChapterVersion
from db.models_org import Org, OrgMember


def document(*paragraphs: tuple[str, str]) -> dict:
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"pid": pid},
                "content": [{"type": "text", "text": text}],
            }
            for pid, text in paragraphs
        ],
    }


async def seed_reviewable_chapter(async_db_session, seed_project) -> None:
    await seed_project(project_id="proj_review", chapter_ids=("ch_review",))
    content_json = document(
        ("p-opening", "许知微推开柴门，雪光落进屋里。"),
        ("p-field", "她要在开春前把两亩荒地整出来。"),
    )
    async_db_session.add_all(
        [
            ChapterBody(
                chapter_id="ch_review",
                content_html="<p>许知微推开柴门，雪光落进屋里。</p><p>她要在开春前把两亩荒地整出来。</p>",
                content_json=content_json,
                rev=1,
            ),
            ChapterVersion(
                chapter_id="ch_review",
                content_html="<p>许知微推开柴门，雪光落进屋里。</p><p>她要在开春前把两亩荒地整出来。</p>",
                content_json=content_json,
                rev=1,
                trigger="manual",
            ),
        ]
    )
    await async_db_session.commit()


async def test_review_round_comment_decision_and_resubmission(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_reviewable_chapter(async_db_session, seed_project)
    headers = auth_headers("user_a")
    base = "/reviews/projects/proj_review"

    empty = await app_client.get(f"{base}/chapters/ch_review", headers=headers)
    assert empty.status_code == 200
    assert empty.json() == {
        "chapter_id": "ch_review",
        "current_body_revision": 1,
        "can_submit": True,
        "can_review": True,
        "can_resolve": True,
        "rounds": [],
    }

    submitted = await app_client.post(
        f"{base}/chapters/ch_review/submit",
        headers=headers,
        json={"submit_note": "请重点看开篇节奏。"},
    )
    assert submitted.status_code == 201
    round_ = submitted.json()["rounds"][0]
    round_id = round_["id"]
    assert round_["submitted_body_revision"] == 1
    assert round_["status"] == "submitted"
    assert round_["stale"] is False

    duplicate = await app_client.post(
        f"{base}/chapters/ch_review/submit", headers=headers, json={}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "NEW_BODY_REVISION_REQUIRED"

    bad_anchor = await app_client.post(
        f"{base}/rounds/{round_id}/comments",
        headers=headers,
        json={"paragraph_id": "p-invented", "content": "这里需要交代动机。"},
    )
    assert bad_anchor.status_code == 422
    assert bad_anchor.json()["detail"]["code"] == "PARAGRAPH_NOT_IN_SUBMITTED_VERSION"

    bad_selection = await app_client.post(
        f"{base}/rounds/{round_id}/comments",
        headers=headers,
        json={
            "paragraph_id": "p-field",
            "selected_text": "并不存在的文字",
            "content": "这句需要更具体。",
        },
    )
    assert bad_selection.status_code == 422
    assert bad_selection.json()["detail"]["code"] == "SELECTED_TEXT_NOT_IN_PARAGRAPH"

    commented = await app_client.post(
        f"{base}/rounds/{round_id}/comments",
        headers=headers,
        json={
            "paragraph_id": "p-field",
            "selected_text": "两亩荒地",
            "content": "补一个必须赶在开春前完成的现实压力。",
        },
    )
    assert commented.status_code == 201
    comment = commented.json()["rounds"][0]["comments"][0]
    comment_id = comment["id"]
    assert comment["paragraph_excerpt"] == "她要在开春前把两亩荒地整出来。"
    assert comment["revision"] == 1

    blocked = await app_client.post(
        f"{base}/rounds/{round_id}/decision",
        headers=headers,
        json={"expected_revision": 1, "decision": "approved"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "OPEN_COMMENTS_PREVENT_APPROVAL"

    returned = await app_client.post(
        f"{base}/rounds/{round_id}/decision",
        headers=headers,
        json={"expected_revision": 1, "decision": "changes_requested"},
    )
    assert returned.status_code == 200
    assert returned.json()["rounds"][0]["status"] == "changes_requested"

    resolved = await app_client.post(
        f"{base}/rounds/{round_id}/comments/{comment_id}/resolve",
        headers=headers,
        json={"expected_revision": 1},
    )
    assert resolved.status_code == 200
    assert resolved.json()["rounds"][0]["comments"][0]["status"] == "resolved"
    assert resolved.json()["rounds"][0]["comments"][0]["revision"] == 2

    body = await async_db_session.get(ChapterBody, "ch_review")
    body.rev = 2
    body.content_json = document(("p-field", "她必须在春汛前整好两亩荒地，否则会失去租契。"))
    body.content_html = "<p>她必须在春汛前整好两亩荒地，否则会失去租契。</p>"
    async_db_session.add(
        ChapterVersion(
            chapter_id="ch_review",
            content_html=body.content_html,
            content_json=body.content_json,
            rev=2,
            trigger="manual",
        )
    )
    await async_db_session.commit()

    resubmitted = await app_client.post(
        f"{base}/chapters/ch_review/submit", headers=headers, json={}
    )
    assert resubmitted.status_code == 201
    rounds = resubmitted.json()["rounds"]
    assert [item["submitted_body_revision"] for item in rounds] == [2, 1]
    assert rounds[1]["stale"] is True


async def test_review_permissions_separate_writing_reviewing_and_viewing(
    app_client, async_db_session, make_user, make_project, make_chapter, auth_headers
):
    users = [make_user(role) for role in ("owner", "writer", "editor", "viewer")]
    async_db_session.add_all(users)
    await async_db_session.flush()
    async_db_session.add(Org(id="org_review", name="审稿组", seats=5, seats_used=4))
    await async_db_session.flush()
    async_db_session.add_all(
        [
            OrgMember(org_id="org_review", user_id="writer", role="writer"),
            OrgMember(org_id="org_review", user_id="editor", role="editor"),
            OrgMember(org_id="org_review", user_id="viewer", role="viewer"),
        ]
    )
    async_db_session.add(make_project("proj_roles", owner_id="owner", org_id="org_review"))
    await async_db_session.flush()
    async_db_session.add(make_chapter("ch_roles", project_id="proj_roles"))
    await async_db_session.flush()
    content_json = document(("p-role", "她将账册放在桌上。"))
    async_db_session.add_all(
        [
            ChapterBody(
                chapter_id="ch_roles", content_html="<p>她将账册放在桌上。</p>", content_json=content_json, rev=1
            ),
            ChapterVersion(
                chapter_id="ch_roles",
                content_html="<p>她将账册放在桌上。</p>",
                content_json=content_json,
                rev=1,
                trigger="manual",
                created_at=datetime.now(UTC),
            ),
        ]
    )
    await async_db_session.commit()
    base = "/reviews/projects/proj_roles"

    writer_view = await app_client.get(
        f"{base}/chapters/ch_roles", headers=auth_headers("writer")
    )
    assert writer_view.status_code == 200
    assert writer_view.json()["can_submit"] is True
    assert writer_view.json()["can_review"] is False

    submitted = await app_client.post(
        f"{base}/chapters/ch_roles/submit", headers=auth_headers("writer"), json={}
    )
    round_id = submitted.json()["rounds"][0]["id"]

    writer_comment = await app_client.post(
        f"{base}/rounds/{round_id}/comments",
        headers=auth_headers("writer"),
        json={"paragraph_id": "p-role", "content": "不应允许作者冒充编辑批注。"},
    )
    assert writer_comment.status_code == 403

    editor_comment = await app_client.post(
        f"{base}/rounds/{round_id}/comments",
        headers=auth_headers("editor"),
        json={"paragraph_id": "p-role", "content": "说明这本账册从哪里得来。"},
    )
    assert editor_comment.status_code == 201

    viewer_submit = await app_client.post(
        f"{base}/chapters/ch_roles/submit", headers=auth_headers("viewer"), json={}
    )
    assert viewer_submit.status_code == 403

    outsider = await app_client.get(
        f"{base}/chapters/ch_roles", headers=auth_headers("owner")
    )
    assert outsider.status_code == 200


async def test_stale_submitted_round_cannot_receive_comments_or_be_approved(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_reviewable_chapter(async_db_session, seed_project)
    headers = auth_headers("user_a")
    base = "/reviews/projects/proj_review"
    submitted = await app_client.post(
        f"{base}/chapters/ch_review/submit", headers=headers, json={}
    )
    round_ = submitted.json()["rounds"][0]

    body = await async_db_session.get(ChapterBody, "ch_review")
    body.rev = 2
    body.content_json = document(("p-field", "她把荒地分成四畦。"))
    body.content_html = "<p>她把荒地分成四畦。</p>"
    async_db_session.add(
        ChapterVersion(
            chapter_id="ch_review",
            content_html=body.content_html,
            content_json=body.content_json,
            rev=2,
            trigger="manual",
        )
    )
    await async_db_session.commit()

    comment = await app_client.post(
        f"{base}/rounds/{round_['id']}/comments",
        headers=headers,
        json={"paragraph_id": "p-field", "content": "旧版意见"},
    )
    assert comment.status_code == 409
    assert comment.json()["detail"]["code"] == "REVIEW_ROUND_STALE"

    decision = await app_client.post(
        f"{base}/rounds/{round_['id']}/decision",
        headers=headers,
        json={"expected_revision": round_["revision"], "decision": "approved"},
    )
    assert decision.status_code == 409
    assert decision.json()["detail"]["code"] == "REVIEW_ROUND_STALE"
