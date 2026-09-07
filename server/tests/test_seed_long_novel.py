from scripts.seed_long_novel import build_codex_specs, prose_document, upsert_codex_entries


def test_prose_document_uses_backend_paragraph_pid_contract():
    content_html, content_json = prose_document("第一段\n\n第二段")

    assert 'data-paragraph-id="p-0"' in content_html
    assert [node["attrs"]["pid"] for node in content_json["content"]] == ["p-0", "p-1"]


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
