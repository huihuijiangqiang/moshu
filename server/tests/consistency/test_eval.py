"""Quality gate that evaluates the real deterministic consistency scanner."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from db.models_codex import CodexEntry
from db.models_consistency_extended import GuardIssueEvidence
from db.models_guard import Foreshadow, GuardIssue
from services.rule_scanner import RuleScanner
from tests.consistency.fixtures_eval import CORPUS_VERSION, get_all_fixtures


class EvaluationResult:
    def __init__(self) -> None:
        self.total_positive = 0
        self.detected_positive = 0
        self.evidence_matched = 0
        self.total_hard_negative = 0
        self.false_positives_hard = 0
        self.total_easy_negative = 0
        self.false_positives_easy = 0
        self.unexpected_issues = 0
        self.by_rule: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "detected": 0}
        )
        self.details: list[dict[str, Any]] = []

    def add_positive(
        self,
        case_id: str,
        rule: str,
        *,
        detected: bool,
        evidence_matched: bool,
    ) -> None:
        self.total_positive += 1
        self.by_rule[rule]["total"] += 1
        if detected:
            self.detected_positive += 1
            self.by_rule[rule]["detected"] += 1
        if evidence_matched:
            self.evidence_matched += 1
        self.details.append(
            {
                "type": "positive",
                "case_id": case_id,
                "rule": rule,
                "detected": detected,
                "evidence_matched": evidence_matched,
            }
        )

    def add_negative(self, split: str, case_id: str, *, incorrectly_flagged: bool) -> None:
        if split == "hard_negative":
            self.total_hard_negative += 1
            self.false_positives_hard += int(incorrectly_flagged)
        else:
            self.total_easy_negative += 1
            self.false_positives_easy += int(incorrectly_flagged)
        self.details.append(
            {
                "type": split,
                "case_id": case_id,
                "incorrectly_flagged": incorrectly_flagged,
            }
        )

    def compute_metrics(self) -> dict[str, Any]:
        recall = self.detected_positive / self.total_positive if self.total_positive else 0.0
        evidence_recall = self.evidence_matched / self.total_positive if self.total_positive else 0.0
        hard_negative_precision = (
            1.0 - self.false_positives_hard / self.total_hard_negative
            if self.total_hard_negative
            else 1.0
        )
        easy_negative_precision = (
            1.0 - self.false_positives_easy / self.total_easy_negative
            if self.total_easy_negative
            else 1.0
        )
        macro_recall = (
            sum(values["detected"] / values["total"] for values in self.by_rule.values())
            / len(self.by_rule)
            if self.by_rule
            else 0.0
        )
        return {
            "recall": recall,
            "macro_recall": macro_recall,
            "evidence_recall": evidence_recall,
            "hard_negative_precision": hard_negative_precision,
            "easy_negative_precision": easy_negative_precision,
            "positive_detected": f"{self.detected_positive}/{self.total_positive}",
            "hard_negatives_correct": (
                f"{self.total_hard_negative - self.false_positives_hard}/"
                f"{self.total_hard_negative}"
            ),
            "easy_negatives_correct": (
                f"{self.total_easy_negative - self.false_positives_easy}/"
                f"{self.total_easy_negative}"
            ),
            "unexpected_issues": self.unexpected_issues,
        }

    def report(self) -> str:
        metrics = self.compute_metrics()
        rule_lines = [
            f"  {rule}: {values['detected']}/{values['total']}"
            for rule, values in sorted(self.by_rule.items())
        ]
        return "\n".join(
            [
                f"=== Consistency Rule Evaluation ({CORPUS_VERSION}) ===",
                f"Recall: {metrics['recall']:.2%} ({metrics['positive_detected']})",
                f"Macro recall: {metrics['macro_recall']:.2%}",
                f"Evidence recall: {metrics['evidence_recall']:.2%}",
                (
                    "Hard-negative precision: "
                    f"{metrics['hard_negative_precision']:.2%} "
                    f"({metrics['hard_negatives_correct']})"
                ),
                (
                    "Easy-negative precision: "
                    f"{metrics['easy_negative_precision']:.2%} "
                    f"({metrics['easy_negatives_correct']})"
                ),
                f"Unexpected issues: {metrics['unexpected_issues']}",
                "Per-rule recall:",
                *rule_lines,
            ]
        )


async def _seed_corpus(async_db_session, seed_project, make_claim, make_run):
    fixtures = get_all_fixtures()
    await seed_project(
        project_id="proj_eval",
        chapter_ids=("ch_source", "ch_followup", "ch_scan"),
    )

    item_ids = sorted(
        {
            claim["object_entry_id"]
            for cases in fixtures.values()
            for case in cases
            for claim in case["claims"]
            if claim.get("object_entry_id")
        }
    )
    foreshadow_specs = [
        case["foreshadow"]
        for cases in fixtures.values()
        for case in cases
        if case.get("foreshadow")
    ]
    async_db_session.add_all(
        [
            CodexEntry(
                id=item_id,
                project_id="proj_eval",
                kind="item",
                name=item_id,
                description="一致性评测物品",
                attrs={},
                resident=False,
                status="confirmed",
                ref_chapters=[],
                conflicts=[],
            )
            for item_id in item_ids
        ]
        + [
            CodexEntry(
                id=spec["entry_id"],
                project_id="proj_eval",
                kind="event",
                name=spec["entry_id"],
                description="一致性评测伏笔",
                attrs={},
                resident=False,
                status="confirmed",
                ref_chapters=[spec["planted_chapter_id"]],
                conflicts=[],
                planted_at=spec["planted_chapter_id"],
                expected_by=spec["expected_chapter_id"],
            )
            for spec in foreshadow_specs
        ]
    )
    await async_db_session.flush()
    async_db_session.add_all(
        [
            Foreshadow(
                project_id="proj_eval",
                description="一致性评测伏笔",
                **spec,
            )
            for spec in foreshadow_specs
        ]
    )
    await async_db_session.flush()

    case_claim_ids: dict[str, set[int]] = {}
    for cases in fixtures.values():
        for case in cases:
            claims = [
                make_claim(
                    project_id="proj_eval",
                    source_kind="body",
                    extractor_version=CORPUS_VERSION,
                    status="accepted",
                    **claim_data,
                )
                for claim_data in case["claims"]
            ]
            async_db_session.add_all(claims)
            await async_db_session.flush()
            case_claim_ids[case["id"]] = {claim.id for claim in claims}

    run = make_run(
        project_id="proj_eval",
        chapter_id="ch_scan",
        body_rev=1,
        pipeline_version=CORPUS_VERSION,
    )
    async_db_session.add(run)
    await async_db_session.flush()
    return fixtures, case_claim_ids, run


async def run_rule_evaluation(
    async_db_session,
    seed_project,
    make_claim,
    make_run,
) -> EvaluationResult:
    fixtures, case_claim_ids, run = await _seed_corpus(
        async_db_session,
        seed_project,
        make_claim,
        make_run,
    )
    scanner = RuleScanner(rule_version=CORPUS_VERSION)
    await scanner.scan_chapter(
        async_db_session,
        run_id=run.id,
        project_id="proj_eval",
        chapter_id="ch_scan",
        body_rev=1,
    )
    await async_db_session.flush()

    issues = list(
        (
            await async_db_session.execute(
                select(GuardIssue).where(GuardIssue.project_id == "proj_eval")
            )
        )
        .scalars()
        .all()
    )
    evidence_rows = list(
        (
            await async_db_session.execute(
                select(GuardIssueEvidence).where(
                    GuardIssueEvidence.issue_id.in_([issue.id for issue in issues])
                )
            )
        )
        .scalars()
        .all()
    ) if issues else []
    evidence_by_issue: dict[str, set[str]] = defaultdict(set)
    for row in evidence_rows:
        if row.paragraph_id:
            evidence_by_issue[row.issue_id].add(row.paragraph_id)

    issue_claim_ids = {
        issue.id: set((issue.evidence or {}).get("claim_ids", [])) for issue in issues
    }
    matched_issue_ids: set[str] = set()
    result = EvaluationResult()

    for case in fixtures["positive"]:
        claim_ids = case_claim_ids[case["id"]]
        matched = next(
            (
                issue
                for issue in issues
                if issue.issue_type == case["expected_issue_type"]
                and (
                    issue.entry_id == case["expected_entry_id"]
                    if case["expected_entry_id"]
                    else claim_ids == issue_claim_ids[issue.id]
                )
            ),
            None,
        )
        evidence_matched = bool(
            matched
            and set(case["expected_evidence"]) == evidence_by_issue[matched.id]
        )
        if matched:
            matched_issue_ids.add(matched.id)
        result.add_positive(
            case["id"],
            case["rule"],
            detected=matched is not None,
            evidence_matched=evidence_matched,
        )

    for split in ("hard_negative", "easy_negative"):
        for case in fixtures[split]:
            claim_ids = case_claim_ids[case["id"]]
            incorrectly_flagged = any(
                issue.entry_id == case["expected_entry_id"]
                if case["expected_entry_id"]
                else claim_ids == issue_claim_ids[issue.id]
                for issue in issues
            )
            result.add_negative(
                split,
                case["id"],
                incorrectly_flagged=incorrectly_flagged,
            )

    result.unexpected_issues = len({issue.id for issue in issues} - matched_issue_ids)
    return result


async def test_real_rule_evaluation_meets_p0_quality_gate(
    async_db_session,
    seed_project,
    make_claim,
    make_run,
):
    result = await run_rule_evaluation(
        async_db_session,
        seed_project,
        make_claim,
        make_run,
    )
    metrics = result.compute_metrics()
    print(result.report())

    assert result.total_positive >= 280
    assert result.total_hard_negative >= 140
    assert metrics["recall"] >= 0.70
    assert metrics["macro_recall"] >= 0.70
    assert metrics["evidence_recall"] >= 0.90
    assert metrics["hard_negative_precision"] >= 0.80
    assert metrics["unexpected_issues"] == 0


def test_corpus_has_versioned_balanced_rule_coverage():
    fixtures = get_all_fixtures()
    positive_rules = {case["rule"] for case in fixtures["positive"]}
    hard_negative_rules = {case["rule"] for case in fixtures["hard_negative"]}

    assert len(fixtures["positive"]) == 280
    assert len(fixtures["hard_negative"]) == 140
    assert len(fixtures["easy_negative"]) == 20
    assert positive_rules == {
        "alive_conflict",
        "ownership_conflict",
        "knowledge_boundary",
        "timeline_conflict",
        "ability_boundary",
        "location_conflict",
        "foreshadow_overdue",
    }
    assert hard_negative_rules == positive_rules
    assert all(
        case["corpus_version"] == CORPUS_VERSION
        for cases in fixtures.values()
        for case in cases
    )


def test_metric_denominators_with_mixed_results():
    result = EvaluationResult()
    result.add_positive(
        "alive-hit",
        "alive_conflict",
        detected=True,
        evidence_matched=True,
    )
    result.add_positive(
        "alive-miss",
        "alive_conflict",
        detected=False,
        evidence_matched=False,
    )
    result.add_positive(
        "knowledge-hit-wrong-evidence",
        "knowledge_boundary",
        detected=True,
        evidence_matched=False,
    )
    result.add_negative("hard_negative", "hard-clean", incorrectly_flagged=False)
    result.add_negative("hard_negative", "hard-flagged", incorrectly_flagged=True)
    result.add_negative("easy_negative", "easy-clean", incorrectly_flagged=False)
    result.unexpected_issues = 2

    metrics = result.compute_metrics()

    assert metrics["recall"] == 2 / 3
    assert metrics["macro_recall"] == 0.75
    assert metrics["evidence_recall"] == 1 / 3
    assert metrics["hard_negative_precision"] == 0.5
    assert metrics["easy_negative_precision"] == 1.0
    assert metrics["unexpected_issues"] == 2
