import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_million_novel.py"
SPEC = importlib.util.spec_from_file_location("generate_million_novel", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_checkpoint_covers_million_character_target():
    checkpoint = MODULE.new_checkpoint(
        target_words=1_000_000,
        chapter_words=3_200,
        model="test-model",
    )
    assert checkpoint["schema_version"] == MODULE.CHECKPOINT_SCHEMA
    assert checkpoint["chapter_count"] == 313
    assert checkpoint["generated_words"] == 0


def test_chapter_quality_blocks_short_and_model_meta():
    short = MODULE.chapter_quality("太短了", 1_000)
    leaked = MODULE.chapter_quality("以下是本章正文。" + "正文" * 400, 800)
    narrative_leak = MODULE.chapter_quality("前一章的痕迹已经说明有人来过。" + "正文" * 400, 800)
    assert short["status"] == "blocked"
    assert leaked["status"] == "blocked"
    assert next(item for item in leaked["checks"] if item["id"] == "no_model_meta")["ok"] is False
    assert next(item for item in narrative_leak["checks"] if item["id"] == "no_model_meta")["ok"] is False


def test_chapter_quality_accepts_substantial_prose():
    text = "“先把粮账给我。”\n" + "\n".join(
        f"她将晒干的菜叶分装入第{i}只筐，按斤记下盐钱和脚力。"
        for i in range(45)
    )
    result = MODULE.chapter_quality(text, 1_000)
    assert result["status"] == "ready"


def test_chapter_quality_blocks_formulaic_not_is_comparison():
    text = '她手里不是空账，而是四枚旧铜钱。\n' + '她把钱压在账册上，等着对方开口。\n' * 30
    result = MODULE.chapter_quality(text, 1_000)
    check = next(item for item in result["checks"] if item["id"] == "no_formulaic_comparison")
    assert check["ok"] is False
    assert result["status"] == "blocked"


def test_chapter_quality_blocks_bare_reverse_not_is_comparison():
    text = '门外不是一辆，是两辆。\n' + '车夫把缰绳绕在木桩上，等着验货。\n' * 30
    result = MODULE.chapter_quality(text, 1_000)
    check = next(item for item in result["checks"] if item["id"] == "no_formulaic_comparison")
    assert check["ok"] is False
    assert "不是一辆，是" in check["matches"][0]
    assert result["status"] == "blocked"


def test_chapter_quality_blocks_reverse_not_is_comparison():
    text = '周七要的是船期，不是真想接下一船来路有争议的粮。\n' + '他把缆绳重新打结，等着验货。\n' * 30
    result = MODULE.chapter_quality(text, 1_000)
    check = next(
        item for item in result["checks"] if item["id"] == "no_reverse_formulaic_comparison"
    )
    assert check["ok"] is False
    assert "是船期，不是" in check["matches"][0]
    assert result["status"] == "blocked"


def test_chapter_quality_allows_formulaic_dialogue_when_quoted():
    text = '“周七要的是船期，不是真想接下一船粮。”\n' + "\n".join(
        f'她把第{i}笔运费写进账册，等着船帮复核。'
        for i in range(45)
    )
    result = MODULE.chapter_quality(text, 1_000)
    assert next(item for item in result["checks"] if item["id"] == "no_reverse_formulaic_comparison")["ok"]
    assert result["status"] == "ready"


def test_chapter_quality_blocks_negation_parade():
    text = '没有船号全称，没有验收人，也没有村方印。\n' + '她把收条压在账册下，等着对方补齐。\n' * 30
    result = MODULE.chapter_quality(text, 1_000)
    check = next(item for item in result["checks"] if item["id"] == "no_negation_parade")
    assert check["ok"] is False
    assert "没有船号全称，没有" in check["matches"][0]
    assert result["status"] == "blocked"


def test_chapter_quality_allows_two_negations_joined_by_connector():
    text = '里正没有答应，也没有拒绝。\n' + "\n".join(
        f'他把第{i}枚印泥推到桌角，等着沈砚秋补完第{i}笔账。'
        for i in range(45)
    )
    result = MODULE.chapter_quality(text, 1_000)
    check = next(item for item in result["checks"] if item["id"] == "no_negation_parade")
    assert check["ok"] is True
    assert result["status"] == "ready"


def test_chapter_quality_blocks_em_dash_dialogue_shortcuts():
    text = "“你先听我——”\n" + "她把粮袋重新称量一遍，逐项记下经手人和斤两。\n" * 30
    result = MODULE.chapter_quality(text, 1_000)
    check = next(item for item in result["checks"] if item["id"] == "no_em_dash")
    assert check["ok"] is False
    assert check["matches"] == ["—", "—"]
    assert result["status"] == "blocked"


def test_chapter_quality_blocks_accidental_sentence_repetition():
    sentence = "她把账册翻到最后一页，确认每一笔粮款。"
    result = MODULE.chapter_quality((sentence + "\n") * 120, 1_000)
    check = next(item for item in result["checks"] if item["id"] == "no_repeated_sentences")
    assert check["ok"] is False
    assert result["status"] == "blocked"


def test_extract_json_object_tolerates_fences_and_prefix():
    assert MODULE.extract_json_object('```json\n{"chapters": []}\n```') == {"chapters": []}
    assert MODULE.extract_json_object('结果如下：\n{"title":"书名"}\n完成') == {"title": "书名"}


def _valid_analysis():
    return {
        "summary": "沈砚秋核对船粮票据，留下可复核的差额证据。",
        "character_updates": {
            "沈砚秋": {
                "state": "暂时掌握票据副本",
                "knows": ["船脚存在差额"],
                "does_not_know": ["赵七的去向"],
                "public_goal": "保住村中粮种",
                "hidden_goal": "追查旧账来源",
            }
        },
        "faction_updates": {},
        "new_facts": [],
        "foreshadow_updates": [],
        "timeline_events": [{"event": "船粮票据被公开复核"}],
        "contract_checks": [{"item": "required_outcome", "ok": True, "evidence": "留下差额记录"}],
        "quality": {"continuity": 8, "character": 8, "plot": 7, "prose": 7, "hook": 8},
    }


def _contract_analysis(contract):
    value = _valid_analysis()
    value["contract_checks"] = [
        {"item": item, "ok": True, "evidence": f"正文证据：{item}"}
        for item in [
            "required_outcome",
            *(
                f"acceptance_criteria:{index}"
                for index in range(1, len(contract["acceptance_criteria"]) + 1)
            ),
            "reveal",
            "hide",
            "foreshadow",
            "hook",
        ]
    ]
    return value


def test_validate_chapter_analysis_accepts_complete_state_delta():
    value = _valid_analysis()
    assert MODULE.validate_chapter_analysis(value) is value


def test_validate_chapter_analysis_rejects_missing_or_malformed_state():
    value = _valid_analysis()
    value.pop("timeline_events")
    try:
        MODULE.validate_chapter_analysis(value)
    except ValueError as exc:
        assert "timeline_events" in str(exc)
    else:
        raise AssertionError("missing analysis fields must be rejected")

    value = _valid_analysis()
    value["character_updates"]["沈砚秋"]["knows"] = "船粮"
    try:
        MODULE.validate_chapter_analysis(value)
    except ValueError as exc:
        assert "knows" in str(exc)
    else:
        raise AssertionError("malformed cognition state must be rejected")


def _valid_chapter_contract(number=1):
    return {
        "number": number,
        "title": "盘账",
        "objective": "核清救命粮的实际库存",
        "conflict": "族人要求先分粮",
        "turn": "旧账的经手人主动改口",
        "required_outcome": "女主取得可复核的账簿副本",
        "acceptance_criteria": ["有具体行动与代价", "出现数字证据", "认知不越界"],
        "reveal": "粮袋重量与账面不符",
        "hide": "幕后总账的最终主使",
        "foreshadow": "缺角收条将在后续复核",
        "hook": "账簿最后一页出现陌生私印",
        "pov": "沈砚秋",
        "time_anchor": "昭宁二十七年春荒第一日",
    }


def test_validate_chapter_contracts_requires_exact_ordered_coverage():
    contracts = [_valid_chapter_contract(1), _valid_chapter_contract(2)]
    assert MODULE.validate_chapter_contracts(
        contracts,
        chapter_from=1,
        chapter_to=2,
    ) == contracts

    contracts[1]["number"] = 3
    try:
        MODULE.validate_chapter_contracts(contracts, chapter_from=1, chapter_to=2)
    except ValueError as exc:
        assert "number mismatch" in str(exc)
    else:
        raise AssertionError("out-of-order chapter contracts must be rejected")


def test_validate_chapter_contract_rejects_incomplete_acceptance_gate():
    contract = _valid_chapter_contract()
    contract["acceptance_criteria"] = ["只有一项"]
    try:
        MODULE.validate_chapter_contract(contract, expected_number=1)
    except ValueError as exc:
        assert "acceptance_criteria" in str(exc)
    else:
        raise AssertionError("incomplete chapter acceptance gate must be rejected")

    contract = _valid_chapter_contract()
    contract.pop("hide")
    try:
        MODULE.validate_chapter_contract(contract, expected_number=1)
    except ValueError as exc:
        assert "hide" in str(exc)
    else:
        raise AssertionError("missing chapter constraints must be rejected")


def test_editorial_gate_requires_complete_evidenced_contract_review():
    contract = _valid_chapter_contract()
    analysis = _contract_analysis(contract)
    assert MODULE.chapter_editorial_gate(contract, analysis)["status"] == "ready"

    analysis["contract_checks"].pop()
    result = MODULE.chapter_editorial_gate(contract, analysis)
    assert result["status"] == "blocked"
    coverage = next(item for item in result["checks"] if item["id"] == "contract_coverage")
    assert coverage["missing"] == ["hook"]


def test_editorial_gate_blocks_failed_evidence_and_low_scores():
    contract = _valid_chapter_contract()
    analysis = _contract_analysis(contract)
    analysis["contract_checks"][0]["ok"] = False
    analysis["contract_checks"][1]["evidence"] = ""
    analysis["quality"]["continuity"] = 6.5

    result = MODULE.chapter_editorial_gate(contract, analysis)
    assert result["status"] == "blocked"
    evidence = next(item for item in result["checks"] if item["id"] == "contract_evidence")
    scores = next(item for item in result["checks"] if item["id"] == "editorial_scores")
    assert evidence["failed"] == ["required_outcome", "acceptance_criteria:1"]
    assert scores["lowScores"] == {"continuity": 6.5}


async def test_run_does_not_advance_canon_when_editorial_gate_blocks(tmp_path):
    class FakeClient:
        retry_count = 0

    checkpoint = MODULE.new_checkpoint(target_words=100_000, chapter_words=800, model="m")
    checkpoint["plan"] = MODULE.build_seed_plan(
        target_words=checkpoint["target_words"],
        chapter_count=checkpoint["chapter_count"],
    )
    runner = MODULE.LongNovelRun(tmp_path, FakeClient(), checkpoint)

    async def write_chapter(outline, target_words):
        del target_words
        prose = "\n".join(
            f"沈砚秋把第{index}袋粮称过一遍，赵顺随后记下{index}号袋的斤两。"
            for index in range(42)
        )
        path = tmp_path / "chapters" / f"0001-{MODULE.safe_filename(outline['title'])}.md"
        MODULE.atomic_write_text(path, prose + "\n")
        return prose, {}

    async def analyze_chapter(outline, prose):
        del prose
        analysis = _contract_analysis(outline)
        analysis["contract_checks"][0]["ok"] = False
        return analysis, {}

    runner.write_chapter = write_chapter
    runner.analyze_chapter = analyze_chapter

    try:
        await runner.run(max_chapters=1)
    except ValueError as exc:
        assert "editorial gate blocked" in str(exc)
    else:
        raise AssertionError("failed editorial review must stop the run")

    assert checkpoint["canon_revision"] == 0
    assert checkpoint["generated_words"] == 0
    assert checkpoint["chapters"][0]["status"] == "review_blocked"
    assert checkpoint["chapters"][0]["editorial_gate"]["status"] == "blocked"


async def test_run_recovers_and_normalizes_orphan_chapter(tmp_path):
    class FakeClient:
        retry_count = 0

    checkpoint = MODULE.new_checkpoint(target_words=100_000, chapter_words=800, model="m")
    checkpoint["plan"] = MODULE.build_seed_plan(
        target_words=checkpoint["target_words"],
        chapter_count=checkpoint["chapter_count"],
    )
    outline = MODULE.build_seed_chapter_outline(1, checkpoint["plan"]["volumes"][0])
    prose = "\n".join(
        ["“账还没核完——”沈砚秋按住被风吹起的纸角。"]
        + [
            f"她把第{index}袋粮重新称量，赵顺逐项记下经手人和斤两。"
            for index in range(41)
        ]
    )
    path = tmp_path / "chapters" / f"0001-{MODULE.safe_filename(outline['title'])}.md"
    MODULE.atomic_write_text(path, prose + "\n")
    runner = MODULE.LongNovelRun(tmp_path, FakeClient(), checkpoint)

    async def unexpected_write(*args, **kwargs):
        del args, kwargs
        raise AssertionError("orphan prose must be reused without another model call")

    async def analyze_chapter(chapter_outline, chapter_prose):
        assert "—" not in chapter_prose
        return _contract_analysis(chapter_outline), {}

    runner.write_chapter = unexpected_write
    runner.analyze_chapter = analyze_chapter
    await runner.run(max_chapters=1)

    assert checkpoint["chapters"][0]["status"] == "accepted"
    assert "——" not in path.read_text(encoding="utf-8")
    assert "……" in path.read_text(encoding="utf-8")


def test_safe_filename_removes_windows_reserved_characters():
    assert MODULE.safe_filename("账册:谁拿走了?/\\*") == "账册-谁拿走了----"


def test_normalize_blocking_punctuation_preserves_interrupted_rhythm():
    assert MODULE.normalize_blocking_punctuation("“等等——”她追出去。甲–乙") == "“等等……”她追出去。甲…乙"


def test_compact_canon_does_not_include_full_prose():
    checkpoint = MODULE.new_checkpoint(target_words=1_000_000, chapter_words=3_200, model="m")
    checkpoint["plan"] = {"fixed_facts": ["不能凭空暴富"]}
    checkpoint["chapters"] = [{"number": 1, "status": "accepted", "summary": "女主核对粮账", "prose": "不应进入上下文"}]
    packed = MODULE.compact_canon(checkpoint)
    assert "女主核对粮账" in packed
    assert "不应进入上下文" not in packed


def test_apply_analysis_persists_new_facts_in_canon():
    checkpoint = MODULE.new_checkpoint(target_words=1_000_000, chapter_words=3_200, model="m")
    runner = MODULE.LongNovelRun(Path("."), None, checkpoint)
    runner.apply_analysis(
        {
            "character_updates": {},
            "faction_updates": {},
            "new_facts": ["船粮票据存在一百二十文差额", {"fact": "县仓封门三日", "evidence": "皂役口谕"}],
            "foreshadow_updates": [],
            "timeline_events": [],
        }
    )
    facts = checkpoint["canon"]["facts"]
    assert len(facts) == 2
    assert any(item["fact"] == "县仓封门三日" and item["evidence"] == "皂役口谕" for item in facts.values())


def test_compact_canon_remains_valid_json_when_state_is_large():
    checkpoint = MODULE.new_checkpoint(target_words=1_000_000, chapter_words=3_200, model="m")
    checkpoint["canon"]["characters"] = {
        f"人物{i}": {"state": "在青河村处理粮账" * 80, "knows": ["旧契" * 20]}
        for i in range(100)
    }
    packed = MODULE.compact_canon(checkpoint, max_chars=1_200)
    parsed = MODULE.json.loads(packed)
    assert isinstance(parsed, dict)
    assert len(packed) <= 1_200
    assert "人物" in packed


def test_chapter_quality_records_explainable_prose_metrics():
    text = "“先记账。”\n她把麦粒倒进木斗，逐行核对重量。\n" * 20
    result = MODULE.chapter_quality(text, 300, min_accept_ratio=0.5)
    assert result["status"] == "ready"
    assert result["metrics"]["paragraphs"] == 40
    assert 0 < result["metrics"]["dialogue_ratio"] < 1
    assert "unique_sentence_ratio" in result["metrics"]


def test_retry_policy_only_retries_transient_statuses():
    transient = MODULE.GatewayRequestError("busy", status_code=503)
    gateway_timeout = MODULE.GatewayRequestError("upstream timeout", status_code=524)
    overloaded = MODULE.GatewayRequestError("Our servers are currently overloaded")
    client_error = MODULE.GatewayRequestError("bad request", status_code=400)
    stream_error = MODULE.GatewayRequestError("stream interrupted")
    assert transient.retryable is True
    assert gateway_timeout.retryable is True
    assert gateway_timeout.retry_delay_floor == MODULE.OVERLOAD_RETRY_FLOOR_SECONDS
    assert overloaded.retry_delay_floor == MODULE.OVERLOAD_RETRY_FLOOR_SECONDS
    assert transient.retry_delay_floor == 0
    assert client_error.retryable is False
    assert stream_error.retryable is True


def test_client_applies_overload_retry_floor(monkeypatch):
    client = object.__new__(MODULE.CompatibleChatClient)
    client.max_retries = 1
    client.retry_backoff_seconds = 0.01
    client.retry_count = 0
    attempts = 0
    delays = []

    async def complete_once(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MODULE.GatewayRequestError("Our servers are currently overloaded")
        return "recovered", {}

    async def record_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(client, "_complete_once", complete_once)
    monkeypatch.setattr(MODULE.asyncio, "sleep", record_sleep)
    result = MODULE.asyncio.run(client.complete([], max_tokens=32))

    assert result == ("recovered", {})
    assert delays == [MODULE.OVERLOAD_RETRY_FLOOR_SECONDS]
    assert client.retry_count == 1


def test_run_lock_prevents_duplicate_processes(tmp_path):
    first = MODULE.RunLock(tmp_path / "run.lock")
    second = MODULE.RunLock(tmp_path / "run.lock")
    first.acquire()
    try:
        try:
            second.acquire()
        except MODULE.RunAlreadyActiveError as exc:
            assert "already active" in str(exc)
        else:
            raise AssertionError("a second process must not acquire the run lock")
    finally:
        first.release()
    second.acquire()
    second.release()


def test_gateway_error_keeps_partial_diagnostics_without_secrets():
    error = MODULE.GatewayRequestError(
        "stream interrupted",
        partial_text="已经生成的一段",
        usage={"completion_tokens": 12},
    )
    assert error.partial_text == "已经生成的一段"
    assert error.usage["completion_tokens"] == 12
