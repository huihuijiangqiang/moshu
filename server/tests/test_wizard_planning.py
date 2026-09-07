import json

import httpx
from sqlalchemy import select

from api.projects import get_wizard_planner
from db.models_core import Chapter
from db.models_usage import UsageLog
from main import app
from services.provider_usage import provider_usage_event
from services.wizard_planning import (
    WizardPlanner,
    WizardPlanningError,
    WizardStoryPlan,
)

PLAN = {
    "title": "禾下新朝",
    "protagonist": "沈青禾，农学研究员，想让全村熬过荒年，却不愿信任任何合作者。",
    "coreHook": "随身种植实验室；每次调用都会消耗她在现代的一段记忆。",
    "synopsis": "沈青禾穿越荒年，从改良土壤开始组织村民自救，并逐步触碰旧有粮权。",
    "volumes": [
        {"title": "第一卷 · 荒年落脚", "summary": "活下来并建立第一支互助队。"},
        {"title": "第二卷 · 水渠新约", "summary": "围绕水权建立新的村庄秩序。"},
    ],
    "chapters": [
        {"title": "第 1 章 · 醒在荒田", "outline": ["确认荒年处境", "用残种完成第一次试种", "招来里正质疑"]},
        {"title": "第 2 章 · 一碗种粮", "outline": ["保护最后的种粮", "说服寡妇结盟", "发现粮仓账目异常"]},
        {"title": "第 3 章 · 夜开旧仓", "outline": ["追查失粮", "与宗族管事交锋", "在旧仓发现现代包装袋"]},
    ],
}


class FakePlanner:
    def __init__(self):
        self.calls = []
        self.usage_events = [
            provider_usage_event(
                model="planner-model",
                usage={"prompt_tokens": 120, "completion_tokens": 240},
                prompt_text="prompt",
                completion_text="completion",
            )
        ]

    async def plan(self, **kwargs):
        self.calls.append(kwargs)
        return WizardStoryPlan.model_validate(PLAN)


async def test_wizard_planner_parses_strict_plan_and_tracks_usage():
    response = {
        "choices": [{"message": {"content": f"```json\n{json.dumps(PLAN, ensure_ascii=False)}\n```"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    async with httpx.AsyncClient(transport=transport) as client:
        planner = WizardPlanner(client)
        result = await planner.plan(
            inspiration="她带着种子穿越到荒年。",
            audience="女频",
            genre="穿越种田",
            tags=["经营"],
            template="群像经营",
        )

    assert result.title == "禾下新朝"
    assert len(result.chapters) == 3
    assert planner.usage_events[0]["prompt_tokens"] == 100


async def test_wizard_planner_rejects_incomplete_model_output():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        try:
            await WizardPlanner(client).plan(
                inspiration="足够长的一句话灵感",
                audience="通用",
                genre="悬疑",
                tags=[],
                template="谜团追索",
            )
        except WizardPlanningError:
            pass
        else:
            raise AssertionError("incomplete model response was accepted")


async def test_wizard_plan_api_is_authenticated_and_records_platform_usage(
    app_client, async_db_session, make_user, auth_headers
):
    async_db_session.add(make_user("wizard_user"))
    await async_db_session.commit()
    fake = FakePlanner()
    app.dependency_overrides[get_wizard_planner] = lambda: fake
    try:
        unauthenticated = await app_client.post(
            "/projects/wizard/plan",
            json={
                "inspiration": "她带着种子穿越到荒年。",
                "audience": "女频",
                "genre": "穿越种田",
                "tags": ["经营"],
                "template": "群像经营",
            },
        )
        response = await app_client.post(
            "/projects/wizard/plan",
            headers=auth_headers("wizard_user"),
            json={
                "inspiration": "她带着种子穿越到荒年。",
                "audience": "女频",
                "genre": "穿越种田",
                "tags": ["经营"],
                "template": "群像经营",
            },
        )
    finally:
        app.dependency_overrides.pop(get_wizard_planner, None)

    assert unauthenticated.status_code == 401
    assert response.status_code == 200
    assert response.json()["coreHook"].startswith("随身种植实验室")
    assert fake.calls[0]["genre"] == "穿越种田"
    usage = (await async_db_session.execute(select(UsageLog))).scalar_one()
    assert usage.user_id == "wizard_user"
    assert usage.project_id is None
    assert usage.feature == "wizard_plan"
    assert usage.platform_event_id is not None
    assert usage.credits == 0


async def test_project_creation_persists_three_generated_chapter_plans(
    app_client, async_db_session, make_user, auth_headers
):
    async_db_session.add(make_user("wizard_creator"))
    await async_db_session.commit()
    response = await app_client.post(
        "/projects",
        headers=auth_headers("wizard_creator"),
        json={
            "title": PLAN["title"],
            "genre": "女频 · 穿越种田",
            "inspiration": "她带着种子穿越到荒年。",
            "synopsis": PLAN["synopsis"],
            "protagonist": PLAN["protagonist"],
            "core_hook": PLAN["coreHook"],
            "audience": "女频",
            "template": "群像经营",
            "tags": ["经营"],
            "volumes": PLAN["volumes"],
            "chapters": PLAN["chapters"],
        },
    )
    assert response.status_code == 201
    chapters = list(
        (
            await async_db_session.execute(
                select(Chapter).where(Chapter.project_id == response.json()["id"]).order_by(Chapter.idx)
            )
        ).scalars()
    )
    assert [chapter.title for chapter in chapters] == [item["title"] for item in PLAN["chapters"]]
    assert chapters[2].outline[-1] == "在旧仓发现现代包装袋"
