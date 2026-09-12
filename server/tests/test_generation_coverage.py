from services.generation_coverage import assess_draft_coverage, build_prompt_coverage


def _report(text: str) -> dict:
    result = assess_draft_coverage(
        {"checks": []},
        text,
    )
    assert result is not None
    return result


def _dramatic_report(text: str) -> dict:
    result = assess_draft_coverage(
        {
            "checks": [
                {
                    "id": "scene.1.turn",
                    "checkType": "requirement",
                    "sourceType": "scene",
                    "sourceId": "scene-1",
                    "semanticType": "turn",
                    "expected": ["原计划失效"],
                },
                {
                    "id": "scene.1.hook",
                    "checkType": "requirement",
                    "sourceType": "scene",
                    "sourceId": "scene-1",
                    "semanticType": "hook",
                    "expected": ["明日必须回应谁会来】"],
                },
            ]
        },
        text,
    )
    assert result is not None
    return result


def test_draft_coverage_warns_when_procedure_replaces_drama():
    paragraph = (
        "沈砚秋登记袋号，核验时辰，再把复称结果、保管费用、交接责任和县衙文书逐项写入凭据。"
        "她说明送达不等于执行，复称不等于开袋，保管不等于货权。"
    )
    report = _report(paragraph * 40 + "\n一，明日继续核验费用；二，重新登记袋号。")

    by_id = {item["id"]: item for item in report["checks"]}
    assert by_id["quality.procedural_density"]["status"] == "author_review"
    assert by_id["quality.report_ending"]["status"] == "author_review"
    assert report["status"] == "needs_attention"


def test_draft_coverage_counts_document_workflow_terms():
    paragraph = (
        "沈砚秋继续阅卷，把副本编号写入条款，再请两名见证人复核。"
        "众人随后翻到下一份副本，重新核对编号与见证。"
    )
    report = _report(paragraph * 35)

    check = next(item for item in report["checks"] if item["id"] == "quality.procedural_density")
    assert check["status"] == "author_review"
    assert any(item.startswith("阅卷×") for item in check["evidence"])


def test_draft_coverage_does_not_warn_on_action_and_relationship_scene():
    paragraph = (
        "山火越过田埂时，沈禾把最后一桶水推给嫂子，自己转身去解牛绳。"
        "周万成挡在门口，要她拿祖宅换粮。她当众撕掉契纸，却把唯一的退路也烧了。"
        "嫂子没有道谢，只抓住她被烫伤的手，说今晚一起守田。"
    )
    report = _report(paragraph * 18)

    by_id = {item["id"]: item for item in report["checks"]}
    assert by_id["quality.procedural_density"]["status"] == "evidence_found"
    assert by_id["quality.report_ending"]["status"] == "evidence_found"
    assert report["status"] == "ready"


def test_draft_coverage_surfaces_weak_turn_and_hook_structure():
    report = _dramatic_report("她把账册收好，说明明日继续核验。" * 80)

    by_id = {item["id"]: item for item in report["checks"]}
    assert by_id["quality.turning_point"]["status"] == "author_review"
    assert by_id["quality.chapter_hook"]["status"] == "author_review"
    assert report["method"] == "lexical_evidence_v2_dramatic_contract"


def test_draft_coverage_recognizes_action_backed_turn_and_opening_hook():
    text = (
        "她原本要把契纸送进县衙，却在门口发现印记是假的。"
        "沈禾转身撕掉旧路线，改从河埠头闯出去，身后的差役已经扣住了粮车。"
        "她按住门闩，门外脚步越来越近，明日谁会先拿出第二份契纸？"
    ) * 20
    report = _dramatic_report(text)

    by_id = {item["id"]: item for item in report["checks"]}
    assert by_id["quality.turning_point"]["status"] == "evidence_found"
    assert by_id["quality.chapter_hook"]["status"] == "evidence_found"


def test_prompt_coverage_warns_on_abstract_scene_contract():
    report = build_prompt_coverage(
        {
            "inputChecks": [],
            "requirements": [
                {
                    "id": "scene-1-turn",
                    "checkType": "requirement",
                    "sourceType": "scene",
                    "sourceId": "scene-1",
                    "semanticType": "turn",
                    "sceneOrder": 1,
                    "label": "场景 1 · 转折",
                    "expected": ["本章推进收束，留下下一章必须回应的具体决定"],
                },
                {
                    "id": "scene-1-hook",
                    "checkType": "requirement",
                    "sourceType": "scene",
                    "sourceId": "scene-1",
                    "semanticType": "hook",
                    "sceneOrder": 1,
                    "label": "场景 1 · 钩子",
                    "expected": ["章末留下后果"],
                },
            ],
        },
        included_content="本章推进收束，留下下一章必须回应的具体决定章末留下后果",
        task="chapter",
    )

    contract = next(item for item in report["checks"] if item["id"] == "scene.scene-1.dramatic_contract")
    assert contract["status"] == "author_review"
    assert contract["severity"] == "warning"


def test_prompt_coverage_accepts_concrete_scene_contract():
    report = build_prompt_coverage(
        {
            "inputChecks": [],
            "requirements": [
                {
                    "id": "scene-1-turn",
                    "checkType": "requirement",
                    "sourceType": "scene",
                    "sourceId": "scene-1",
                    "semanticType": "turn",
                    "sceneOrder": 1,
                    "label": "场景 1 · 转折",
                    "expected": ["巡核使扣下田契，沈砚秋撕掉旧账并当众改押自己的名字"],
                },
                {
                    "id": "scene-1-hook",
                    "checkType": "requirement",
                    "sourceType": "scene",
                    "sourceId": "scene-1",
                    "semanticType": "hook",
                    "sceneOrder": 1,
                    "label": "场景 1 · 钩子",
                    "expected": ["巡核使点燃香：香灭前要账还是要人"],
                },
            ],
        },
        included_content="巡核使扣下田契，沈砚秋撕掉旧账并当众改押自己的名字巡核使点燃香：香灭前要账还是要人",
        task="chapter",
    )

    assert not any(item["id"] == "scene.scene-1.dramatic_contract" for item in report["checks"])
