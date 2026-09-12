from services.writing_skills import build_chapter_variation_contract, select_writing_skills


def test_chapter_variation_contract_rotates_story_engine_and_hook():
    contract = build_chapter_variation_contract(
        2,
        outline=["沈禾去粮铺谈价"],
        recent_patterns=[
            {"variationEngine": "公开对峙", "hookType": "倒计时"},
        ],
    )

    assert "移动追索" in contract
    assert "未完成动作" in contract
    assert "近期已用叙事发动机：公开对峙" in contract
    assert "最后120到250字" in contract


def test_explicit_outline_hook_type_wins_over_rotating_default():
    contract = build_chapter_variation_contract(
        1,
        outline=["章末钩子（证据缺口）：账册最后一页被刮掉"],
    )

    assert "本章指定章尾钩子类型：证据缺口" in contract


def test_variation_contract_skips_recent_engine_and_hook_instead_of_only_warning():
    contract = build_chapter_variation_contract(
        1,
        recent_patterns=[
            {"variationEngine": "公开对峙", "hookType": "倒计时"},
            {"variationEngine": "移动追索", "hookType": "未完成动作"},
        ],
    )

    assert "本章指定发动机：关系交换" in contract
    assert "本章指定章尾钩子类型：关系威胁" in contract
    assert "公开对峙" in contract and "移动追索" in contract


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
        "craft.dramatic-motion",
        "task.chapter",
        "scene.negotiation",
    ]
    assert selection.scene == "negotiation"
    assert "不擅自改写事实" in selection.prompt()
    assert "小说首先写人" in selection.prompt()
    assert "禁止连续多段只做" in selection.prompt()


def test_inline_author_style_skill_is_selected():
    selection = select_writing_skills(
        genre="现实题材",
        task="author_style",
        outline=[],
        instruction="重写这段",
    )

    assert "task.author-style" in selection.ids
    assert selection.ids[-1] == "scene.general"


def test_political_intrigue_is_not_flattened_into_a_generic_investigation():
    selection = select_writing_skills(
        genre="女频 · 穿越种田",
        task="chapter",
        outline=["京中派系借县衙名册逼迫女主交出合作社控制权"],
    )

    assert selection.scene == "political"
    assert selection.ids[-1] == "scene.political"
    assert "权谋不是比谁更懂手续" in selection.prompt()
