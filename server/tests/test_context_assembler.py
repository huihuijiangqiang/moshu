from unittest.mock import AsyncMock

from db.models_codex import CodexAlias, CodexEntry, CodexRelation, CodexStateChange
from db.models_consistency_extended import DocumentSummary
from db.models_core import ChapterBody
from db.models_positioning import ProjectPositioning
from db.models_scene_cards import ChapterScene
from memory.assembler import ContextAssembler, ContextBudgetPolicy, ContextLayer
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
    original = ContextLayer("adjacent", "开头" * 100 + "关键章末", 0, [])
    original.tokens = tokenizer.count(original.content)
    trimmed = assembler._trim_layer(original, 30, keep_tail=True)

    assert trimmed.tokens <= 30
    assert "关键章末" in trimmed.content
    assert trimmed.content.startswith("[前文已截断]")


def test_context_policy_scales_smart_mode_and_respects_small_byok_windows():
    platform = ContextBudgetPolicy.create()

    assert platform.target_for(30_000) == 64_000
    assert platform.target_for(90_000) == 128_000
    assert platform.target_for(180_000) == 208_000

    byok = ContextBudgetPolicy.create(
        model_window_tokens=32_768,
        reserved_output_tokens=4_096,
        safety_margin_tokens=2_048,
    )
    assert byok.max_context_tokens == 26_624
    assert byok.target_for(180_000) == 26_624


def test_context_trim_never_decodes_a_partial_sentence_or_unbroken_unit():
    assembler = ContextAssembler(None, tokenizer)  # type: ignore[arg-type]
    content = "第一句。第二句。" + "无标点长段" * 200
    original = ContextLayer("summary", content, tokenizer.count(content), [])
    target = tokenizer.count("第一句。第二句。\n[已截断]") + 2

    trimmed = assembler._trim_layer(original, target)

    assert trimmed.tokens <= target
    assert "第一句。第二句。" in trimmed.content
    assert "无标点长段" not in trimmed.content
    assert trimmed.content.endswith("[已截断]")


async def test_context_excludes_summary_from_an_old_body_revision(
    async_db_session, seed_project
):
    chapters = await seed_project(
        user_id="summary_writer",
        project_id="summary_novel",
        chapter_ids=("summary_old", "summary_target"),
    )
    async_db_session.add(
        ChapterBody(
            chapter_id="summary_old",
            content_html="<p>正文已经改成第二版。</p>",
            content_json={},
            rev=2,
        )
    )
    async_db_session.add(
        DocumentSummary(
            owner_type="chapter",
            owner_id="summary_old",
            source_rev=1,
            summary_version="test-v1",
            content="第一版中主角已经离开村庄。",
            model_id="test-model",
            token_count=20,
            status="active",
        )
    )
    await async_db_session.flush()

    context = await ContextAssembler(async_db_session, tokenizer).build(
        "summary_novel", "summary_target", chapters[1].outline
    )

    assert "第一版中主角已经离开村庄" not in context.layer3_summary.content


async def test_context_build_routes_structured_distant_evidence_into_summary(
    async_db_session, seed_project
):
    chapters = await seed_project(
        user_id="rag_writer",
        project_id="rag_novel",
        chapter_ids=("rag_old", "rag_target"),
    )
    chapters[1].outline = ["沈禾追查旧田契的下落"]
    await async_db_session.flush()
    assembler = ContextAssembler(async_db_session, tokenizer)
    structured = AsyncMock(
        return_value=[
            {
                "chunk_id": "chunk-old",
                "entry_id": None,
                "content_text": "她曾在雨夜看见田契上的暗记。",
                "content_hash": "hash-old",
                "chapter_id": "rag_old",
                "chapter_index": 1024,
                "body_rev": 3,
                "similarity": 0.91,
                "source": "chapter_chunk",
                "reason": "semantic_chapter:scenes",
            }
        ]
    )
    assembler._retrieve_structured_evidence = structured  # type: ignore[method-assign]

    context = await assembler.build("rag_novel", "rag_target", chapters[1].outline)

    assert "她曾在雨夜看见田契上的暗记" in context.layer3_summary.content
    assert any(item["id"] == "chunk:chunk-old" for item in context.layer3_summary.items)
    structured.assert_awaited_once()
    assert structured.await_args.kwargs["near_window"] == 12


async def test_context_includes_positioning_and_ordered_scene_cards_in_resident_and_retrieval_layers(
    async_db_session,
    seed_project,
):
    chapters = await seed_project(
        user_id="planner",
        project_id="planned_novel",
        chapter_ids=("planned_ch",),
    )
    chapters[0].outline = ["沈禾到镇上寻找销路"]
    shopkeeper = CodexEntry(
        id="planned_shopkeeper",
        project_id="planned_novel",
        kind="character",
        name="周万成",
        description="粮铺掌柜，善于压价。",
        attrs={},
        resident=False,
        status="confirmed",
        ref_chapters=[],
        conflicts=[],
    )
    async_db_session.add_all(
        [
            shopkeeper,
            ProjectPositioning(
                id="planned_positioning",
                project_id="planned_novel",
                platform="fanqie",
                selling_point="落魄农女用现代农学带全村度过饥荒",
                synopsis="沈禾从荒地起步。",
                tags=["穿越", "种田"],
                protagonist_dilemma="既要隐藏来历，又要说服村民改变旧法",
                first_payoff="第一茬青谷增产",
                long_term_arc="从自救走向建立公平粮食秩序",
                revision=2,
                status="active",
            ),
            ChapterScene(
                id="planned_scene_2",
                chapter_id="planned_ch",
                order=2,
                goal="签下青谷长期契约",
                obstacle="周万成仍想压低收购价",
                turn="竞争粮商当场抬价",
                hook="周万成追出门提出秘密条件",
                status="ready",
                rev=1,
                outline_rev=0,
            ),
            ChapterScene(
                id="planned_scene_1",
                chapter_id="planned_ch",
                order=1,
                pov_entry_id="planned_shopkeeper",
                goal="摸清粮铺真实底价",
                obstacle="周万成故意报出行情低价",
                turn="沈禾拿出其他粮商的报价单",
                info_gain="周万成急需稳定青谷货源",
                emotion_shift="试探转为紧张",
                status="planning",
                rev=1,
                outline_rev=0,
            ),
            ChapterScene(
                id="planned_scene_old",
                chapter_id="planned_ch",
                order=3,
                goal="不应进入提示词",
                status="archived",
                rev=2,
                outline_rev=0,
            ),
        ]
    )
    await async_db_session.flush()

    context = await ContextAssembler(async_db_session, tokenizer).build(
        "planned_novel", "planned_ch", chapters[0].outline
    )

    resident = context.layer1_resident.content
    assert "作品定位与读者承诺" in resident
    assert "落魄农女用现代农学带全村度过饥荒" in resident
    assert resident.index("## 场景 1") < resident.index("## 场景 2")
    assert "签下青谷长期契约" in resident
    assert "不应进入提示词" not in resident
    # Scene-card text augments the existing retrieval query, so a referenced
    # non-resident entry is available without another retrieval subsystem.
    assert "周万成" in context.layer2_retrieved.content
    assert {item["kind"] for item in context.layer1_resident.items} >= {"positioning", "scene"}
    assert {requirement["id"] for requirement in context.guidance["requirements"]} >= {
        "positioning.selling_point",
        "scene.planned_scene_1.goal",
        "scene.planned_scene_2.turn",
    }


async def test_context_includes_confirmed_relations_without_leaking_invalid_targets(
    async_db_session, seed_project, make_project
):
    chapters = await seed_project(
        user_id="writer",
        project_id="novel",
        chapter_ids=("ch1", "ch2"),
    )
    chapters[1].outline = ["沈禾带着田契回到青河村"]
    async_db_session.add(make_project("other_novel", owner_id="writer"))
    await async_db_session.flush()
    resident = CodexEntry(
        id="resident_source", project_id="novel", kind="character", name="沈禾",
        description="女主", attrs={}, resident=True, status="confirmed",
        ref_chapters=[], conflicts=[],
    )
    retrieved = CodexEntry(
        id="retrieved_source", project_id="novel", kind="item", name="田契",
        description="荒田权属凭证", attrs={}, resident=False, status="confirmed",
        ref_chapters=[], conflicts=[],
    )
    confirmed_target = CodexEntry(
        id="confirmed_target", project_id="novel", kind="location", name="青河村",
        description="故事起点", attrs={}, resident=False, status="confirmed",
        ref_chapters=[], conflicts=[],
    )
    pending_target = CodexEntry(
        id="pending_target", project_id="novel", kind="character", name="未确认掌柜",
        description="不应进入上下文", attrs={}, resident=False, status="pending",
        ref_chapters=[], conflicts=[],
    )
    cross_project_target = CodexEntry(
        id="cross_target", project_id="other_novel", kind="location", name="越界县城",
        description="不应进入上下文", attrs={}, resident=False, status="confirmed",
        ref_chapters=[], conflicts=[],
    )
    async_db_session.add_all(
        [resident, retrieved, confirmed_target, pending_target, cross_project_target]
    )
    await async_db_session.flush()
    async_db_session.add_all(
        [
            CodexRelation(
                id="rel_resident", from_id=resident.id, to_id=confirmed_target.id,
                relation_type="居住于", description="在村东落脚",
            ),
            CodexRelation(
                id="rel_retrieved", from_id=retrieved.id, to_id=confirmed_target.id,
                relation_type="属于", description="对应村东荒田",
            ),
            CodexRelation(
                id="rel_pending", from_id=resident.id, to_id=pending_target.id,
                relation_type="认识", description="不应泄漏",
            ),
            CodexRelation(
                id="rel_cross", from_id=resident.id, to_id=cross_project_target.id,
                relation_type="前往", description="不应泄漏",
            ),
        ]
    )
    await async_db_session.commit()

    context = await ContextAssembler(async_db_session, tokenizer).build(
        "novel", "ch2", chapters[1].outline
    )

    assert "- 居住于 -> 青河村 (location) - 在村东落脚" in context.layer1_resident.content
    assert "- 属于 -> 青河村 (location) - 对应村东荒田" in context.layer2_retrieved.content
    assert "未确认掌柜" not in context.layer1_resident.content
    assert "越界县城" not in context.layer1_resident.content


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
