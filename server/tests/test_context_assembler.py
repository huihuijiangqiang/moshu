from db.models_codex import CodexAlias, CodexEntry
from db.models_consistency_extended import DocumentSummary
from db.models_core import ChapterBody
from memory.assembler import ContextAssembler
from memory.tokenizer import tokenizer


async def test_context_assembler_builds_all_four_layers(
    async_db_session,
    seed_project,
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
