from db.models_core import Project
from services.generation import GenerationService
from services.story_hooks import create_story_hook


async def test_generation_uses_visible_chapter_number_instead_of_sparse_sort_key(
    async_db_session,
    seed_project,
):
    chapters = await seed_project(
        user_id="chapter_number_writer",
        project_id="chapter_number_novel",
        chapter_ids=("chapter_number_1", "chapter_number_2", "chapter_number_3"),
    )
    project = await async_db_session.get(Project, "chapter_number_novel")
    assert project is not None
    service = GenerationService(async_db_session)

    number = await service._chapter_number(project.id, chapters[-1])
    patterns = await service._recent_dramatic_patterns(
        project,
        chapters[-1],
        chapter_number=number,
    )

    assert [chapter.idx for chapter in chapters] == [1024, 2048, 3072]
    assert number == 3
    assert [item["chapterIndex"] for item in patterns] == [1, 2]
    assert [item["variationEngine"] for item in patterns] == ["公开对峙", "移动追索"]


async def test_generation_exposes_due_hook_debt_with_visible_source_number(
    async_db_session,
    seed_project,
):
    chapters = await seed_project(
        user_id="hook_context_writer",
        project_id="hook_context_novel",
        chapter_ids=("hook_context_1", "hook_context_2", "hook_context_3"),
    )
    await create_story_hook(
        async_db_session,
        project_id="hook_context_novel",
        source_chapter_id=chapters[0].id,
        hook_type="倒计时",
        concrete_event="县差已经点燃第一炷香。",
        unresolved_question="香灭前能否交出真账？",
        payoff_by_chapter=3,
    )
    project = await async_db_session.get(Project, "hook_context_novel")
    assert project is not None

    debts = await GenerationService(async_db_session)._active_hook_debts(
        project.id,
        chapter_number=3,
    )

    assert debts == [
        {
            "id": debts[0]["id"],
            "sourceChapter": 1,
            "sourceTitle": chapters[0].title,
            "hookType": "倒计时",
            "concreteEvent": "县差已经点燃第一炷香。",
            "unresolvedQuestion": "香灭前能否交出真账？",
            "payoffByChapter": 3,
            "urgency": "due",
            "status": "open",
        }
    ]
