from services.generation_coverage import assess_draft_coverage


def _report(text: str) -> dict:
    result = assess_draft_coverage(
        {"checks": []},
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
