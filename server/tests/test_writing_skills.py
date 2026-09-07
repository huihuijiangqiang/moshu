from services.writing_skills import select_writing_skills


def test_skill_selection_combines_genre_task_and_scene_in_stable_order():
    selection = select_writing_skills(
        genre="女频 · 穿越种田",
        task="chapter",
        outline=["沈禾去粮铺谈判，为新作坊压低米价"],
    )

    assert selection.ids == [
        "base.novel.zh",
        "genre.farming",
        "genre.historical",
        "genre.romance",
        "task.chapter",
        "scene.negotiation",
    ]
    assert selection.scene == "negotiation"
    assert "不擅自改写事实" in selection.prompt()


def test_inline_author_style_skill_is_selected():
    selection = select_writing_skills(
        genre="现实题材",
        task="author_style",
        outline=[],
        instruction="重写这段",
    )

    assert "task.author-style" in selection.ids
    assert selection.ids[-1] == "scene.general"
