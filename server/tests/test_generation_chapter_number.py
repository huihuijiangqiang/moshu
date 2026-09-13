from db.models_core import Project
from services.generation import GenerationService


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
