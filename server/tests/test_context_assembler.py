from db.models_codex import CodexAlias, CodexEntry, CodexStateChange
from db.models_consistency_extended import DocumentSummary
from db.models_core import ChapterBody
from memory.assembler import ContextAssembler
from memory.tokenizer import tokenizer


async def test_context_assembler_builds_all_four_layers(
    async_db_session,
    seed_project,
    make_claim,
):
    chapters = await seed_project(
        user_id="writer",
        project_id="novel",
        chapter_ids=("ch1", "ch2", "ch3"),
        genre="女频 · 穿越种田",
    )
    chapters[0].title = "开荒"
    chapters[1].title = "试种"
    chapters[2].title = "议价"
    chapters[2].outline = ["沈禾带着青谷去粮铺与周掌柜议价"]
    async_db_session.add_all(
        [
            ChapterBody(chapter_id="ch1", content_html="<p>沈禾清出荒地。</p>", content_json={}, rev=1),
            ChapterBody(chapter_id="ch2", content_html="<p>第一茬青谷成熟了。</p>", content_json={}, rev=1),
        ]
    )
    resident = CodexEntry(
        id="codex_resident",
        project_id="novel",
        kind="rule",
        name="穿越限制",
        description="沈禾不能凭空取得现代物资。",
        attrs={},
        resident=True,
        status="confirmed",
        ref_chapters=[],
        conflicts=[],
    )
    retrieved = CodexEntry(
        id="codex_shopkeeper",
        project_id="novel",
        kind="character",
        name="周万成",
        description="镇上粮铺掌柜，重利但守约。",
        attrs={"role": "掌柜"},
        resident=False,
        status="confirmed",
        ref_chapters=[],
        conflicts=[],
    )
    async_db_session.add_all([resident, retrieved])
    async_db_session.add(
        make_claim(
            project_id="novel",
            chapter_id="ch2",
            fingerprint="confirmed-fuzzy-time",
            subject_text="青谷开市",
            timeline_id="main",
            temporal_event_ref="开市",
            temporal_relation="after",
            temporal_relation_ref="试种成功",
            temporal_anchor_text="过几日后",
            temporal_resolution={
                "author_override": {"offset_seconds": 4 * 86400},
                "author_override_version": 1,
            },
            order_basis="relative_to_anchor",
        )
    )
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id=retrieved.id, alias="周掌柜"))
    async_db_session.add(
        DocumentSummary(
            owner_type="chapter",
            owner_id="ch2",
            source_rev=1,
            summary_version="test-v1",
            content="沈禾试种青谷成功，准备去镇上寻找销路。",
            model_id="test-model",
            token_count=30,
            status="active",
        )
    )
    await async_db_session.flush()

    context = await ContextAssembler(async_db_session, tokenizer).build("novel", "ch3", chapters[2].outline)

    assert "穿越限制" in context.layer1_resident.content
    assert "作者确认的时间事实" in context.layer1_resident.content
    assert "作者确认为相对“试种成功”之后4天" in context.layer1_resident.content
    assert "周万成" in context.layer2_retrieved.content
    assert "试种青谷成功" in context.layer3_summary.content
    assert "沈禾清出荒地" in context.layer4_adjacent.content
    assert "第一茬青谷成熟" in context.layer4_adjacent.content
    assert context.total_tokens == sum(
        layer.tokens
        for layer in (
            context.layer1_resident,
            context.layer2_retrieved,
            context.layer3_summary,
            context.layer4_adjacent,
        )
    )


def test_context_trim_uses_exact_token_budget_and_can_keep_tail():
    assembler = ContextAssembler(None, tokenizer)  # type: ignore[arg-type]
    from memory.assembler import ContextLayer

    original = ContextLayer("adjacent", "开头" * 100 + "关键章末", 0, [])
    original.tokens = tokenizer.count(original.content)
    trimmed = assembler._trim_layer(original, 30, keep_tail=True)

    assert trimmed.tokens <= 30
    assert "关键章末" in trimmed.content
    assert trimmed.content.startswith("[前文已截断]")


async def test_context_uses_latest_author_state_without_leaking_future_changes(
    async_db_session,
    seed_project,
):
    await seed_project(
        user_id="writer",
        project_id="novel",
        chapter_ids=("ch1", "ch2", "ch3"),
    )
    resident = CodexEntry(
        id="resident",
        project_id="novel",
        kind="character",
        name="沈禾",
        description="女主",
        attrs={},
        resident=True,
        status="confirmed",
        ref_chapters=[],
        conflicts=[],
    )
    retrieved = CodexEntry(
        id="retrieved",
        project_id="novel",
        kind="item",
        name="田契",
        description="荒田权属凭证",
        attrs={},
        resident=False,
        status="confirmed",
        ref_chapters=[],
        conflicts=[],
    )
    async_db_session.add_all([resident, retrieved])
    await async_db_session.flush()
    async_db_session.add_all(
        [
            CodexStateChange(
                id="state-resident-1",
                project_id="novel",
                entry_id=resident.id,
                chapter_id="ch1",
                state_key="居所",
                value="周家旧屋",
                status="active",
                rev=1,
                created_by="writer",
            ),
            CodexStateChange(
                id="state-resident-3",
                project_id="novel",
                entry_id=resident.id,
                chapter_id="ch3",
                state_key="居所",
                value="村东新宅",
                status="active",
                rev=1,
                created_by="writer",
            ),
            CodexStateChange(
                id="state-retrieved-1",
                project_id="novel",
                entry_id=retrieved.id,
                chapter_id="ch1",
                state_key="持有人",
                value="周地主",
                status="active",
                rev=1,
                created_by="writer",
            ),
        ]
    )
    await async_db_session.flush()

    before = await ContextAssembler(async_db_session, tokenizer).build(
        "novel", "ch2", ["沈禾查验田契"]
    )
    after = await ContextAssembler(async_db_session, tokenizer).build(
        "novel", "ch3", ["沈禾拿到田契"]
    )

    assert "周家旧屋" in before.layer1_resident.content
    assert "村东新宅" not in before.layer1_resident.content
    assert "田契" in before.layer2_retrieved.content
    assert "持有人=周地主" in before.layer2_retrieved.content
    assert "村东新宅" in after.layer1_resident.content
    assert "周家旧屋" not in after.layer1_resident.content
