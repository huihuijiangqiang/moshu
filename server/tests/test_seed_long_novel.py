from scripts.seed_long_novel import (
    _chapter_outline_lines,
    build_codex_specs,
    chapter_file_for_number,
    chapter_outlines_from_checkpoint,
    prose_document,
    upsert_codex_entries,
)


def test_prose_document_uses_backend_paragraph_pid_contract():
    content_html, content_json = prose_document("第一段\n\n第二段")

    assert 'data-paragraph-id="p-0"' in content_html
    assert [node["attrs"]["pid"] for node in content_json["content"]] == ["p-0", "p-1"]


def test_chapter_file_lookup_accepts_zero_padded_and_large_numbers(tmp_path):
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    first = chapters_dir / "0001-开篇.md"
    later = chapters_dir / "0111-河港争衡.md"
    first.write_text("第一章", encoding="utf-8")
    later.write_text("第一百一十一章", encoding="utf-8")

    assert chapter_file_for_number(chapters_dir, 1) == first
    assert chapter_file_for_number(chapters_dir, 111) == later
    assert chapter_file_for_number(chapters_dir, 112) is None


def test_checkpoint_outlines_merge_new_volume_schema_in_chapter_order():
    checkpoint = {
        "plan": {"chapters": [{"number": 1, "title": "旧标题"}]},
        "volume_outlines": {
            "2": [{"number": 33, "title": "第二卷"}],
            "1": [
                {"number": 2, "title": "第二章"},
                {"number": 1, "title": "新标题"},
            ],
        },
    }

    outlines = chapter_outlines_from_checkpoint(checkpoint)

    assert [outline["number"] for outline in outlines] == [1, 2, 33]
    assert outlines[0]["title"] == "新标题"


def test_seeded_checkpoint_fills_unopened_volumes_without_overwriting_authored_outline():
    from scripts.generate_million_novel import build_seed_plan

    plan = build_seed_plan(target_words=100_000, chapter_count=20)
    authored = {"number": 1, "title": "作者保留的第一章", "objective": "保住粮种"}
    checkpoint = {
        "plan": {**plan, "chapters": [authored]},
        "volume_outlines": {},
    }

    outlines = chapter_outlines_from_checkpoint(checkpoint)

    assert len(outlines) == 20
    assert outlines[0] is authored
    assert outlines[-1]["number"] == 20
    assert outlines[-1]["scene_mode"]
    assert outlines[-1]["human_stake"]


def test_imported_outline_preserves_dramatic_contract_fields():
    lines = _chapter_outline_lines(
        {
            "objective": "保住粮车",
            "scene_mode": "公开场合的限时对峙",
            "conflict_carrier": "粮车与围观者的判断",
            "emotional_arc": "沈禾从笃定转为焦急",
            "human_stake": "车夫一家会失去当日口粮",
            "opening_hook": "粮车先被扣下",
            "strategy": "公开复称",
            "turn_trigger": "盟友撤走担保",
            "turn": "原策略失效",
            "choice": "保人还是保账",
            "cost": "押上田契",
            "state_before": "证据尚在手中",
            "state_after": "证据公开但田契被扣",
            "hook_type": "倒计时",
            "hook": "一炷香燃尽前必须选择",
            "unresolved_question": "她会保人还是保账？",
            "acceptance_criteria": [],
        }
    )

    assert "冲突场型：公开场合的限时对峙" in lines
    assert "冲突载体：粮车与围观者的判断" in lines
    assert "情绪弧线：沈禾从笃定转为焦急" in lines
    assert "人物代价：车夫一家会失去当日口粮" in lines
    assert "策略失效：盟友撤走担保" in lines
    assert "即时代价：押上田契" in lines
    assert "章末钩子（倒计时）：一炷香燃尽前必须选择" in lines
    assert "章末未决问题：她会保人还是保账？" in lines


def sample_plan():
    return {
        "characters": [
            {
                "name": "许知微",
                "age": "二十四岁",
                "role": "女主",
                "personality": "谨慎务实，重视证据。",
                "skills": "记账、议价",
                "limits": "不熟悉律例",
                "relationships": "与周何氏共同持家。",
            },
            {
                "name": "周何氏",
                "age": "四十八岁",
                "role": "婆母",
                "personality": "节俭坚忍。",
                "skills": "传统腌菜",
                "limits": "不识字",
                "relationships": "审视许知微，也逐渐信任她。",
            },
        ],
        "locations": [
            {"名称": "周家小院", "功能": "家庭生活", "固定条件": "柴棚漏雨"}
        ],
        "era_rules": {"宗族规则": "族长不能取代官府判契", "司法规则": "以契书为证"},
        "economy_rules": {"经营品类": "只经营当地已有食货", "货币": "一贯等于一千文"},
        "style_rules": {"连续性": "钱粮逐章继承"},
        "fixed_facts": ["许知微没有系统"],
    }


def test_codex_specs_are_grounded_and_linked_to_completed_chapters():
    specs = build_codex_specs(
        sample_plan(),
        [("p1-ch01", "许知微在周家小院记账。"), ("p1-ch02", "周何氏去了祠堂。")],
        project_id="p1",
    )

    assert len(specs) == 10
    heroine = next(spec for spec in specs if spec["name"] == "许知微")
    assert heroine["id"] == "p1-cx-char-01"
    assert heroine["ref_chapters"] == ["p1-ch01"]
    assert heroine["attrs"]["relations"][0]["target_id"] == "p1-cx-char-02"
    assert next(spec for spec in specs if spec["name"] == "全书不可违背事实")[
        "resident"
    ]


def test_codex_specs_accept_current_long_novel_plan_schema():
    plan = {
        "characters": {
            "沈砚秋": {
                "role": "女主/审计师",
                "personality": "冷静、记账",
                "skills": "核账",
                "limits": "缺乏古代身份",
            }
        },
        "locations": [{"name": "沈家村", "description": "临青沅河的旱地村"}],
        "factions": [
            {"name": "沈氏宗族", "public_goal": "保田保族", "resources": "土地、乡约"}
        ],
        "style_rules": "限知贴近女主",
        "fixed_facts": ["所有判断必须有证据"],
    }

    specs = build_codex_specs(
        plan,
        [("chapter-1", "沈砚秋回到沈家村，先去沈氏宗族核账。")],
        project_id="novel",
    )

    assert {spec["name"] for spec in specs} >= {"沈砚秋", "沈家村", "沈氏宗族"}
    assert next(spec for spec in specs if spec["name"] == "沈氏宗族")["ref_chapters"] == [
        "chapter-1"
    ]


async def test_reimport_preserves_a_fresh_embedding_when_search_text_is_unchanged(
    async_db_session, seed_project
):
    await seed_project()
    plan = sample_plan()
    chapters = [("ch_a", "许知微在周家小院记账。")]
    await upsert_codex_entries(async_db_session, plan, chapters, project_id="proj_a")
    await async_db_session.flush()

    from sqlalchemy import select

    from db.models_codex import CodexEntry

    entry = (
        await async_db_session.execute(
            select(CodexEntry).where(CodexEntry.id == "proj_a-cx-char-01")
        )
    ).scalar_one()
    entry.embedding = [0.25] * 2048
    entry.embedding_text_hash = "fresh-hash"
    entry.attrs = {"old": True}
    await async_db_session.flush()

    await upsert_codex_entries(async_db_session, plan, chapters, project_id="proj_a")
    await async_db_session.flush()

    assert entry.embedding is not None
    assert entry.embedding_text_hash == "fresh-hash"
    assert entry.attrs["role"] == "女主"
