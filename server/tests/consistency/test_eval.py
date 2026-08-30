"""
Evaluation runner for consistency rules - smoke test with fixtures
"""
from typing import Any

from tests.consistency.fixtures_eval import get_all_fixtures


class EvaluationResult:
    """评测结果"""

    def __init__(self):
        self.total_positive = 0
        self.detected_positive = 0
        self.total_hard_negative = 0
        self.false_positives_hard = 0
        self.total_easy_negative = 0
        self.false_positives_easy = 0
        self.details = []

    def add_positive(self, case_id: str, detected: bool, evidence_matched: bool):
        """添加正例结果"""
        self.total_positive += 1
        if detected:
            self.detected_positive += 1
        self.details.append(
            {
                "type": "positive",
                "case_id": case_id,
                "detected": detected,
                "evidence_matched": evidence_matched,
            }
        )

    def add_hard_negative(self, case_id: str, incorrectly_flagged: bool):
        """添加 hard negative 结果"""
        self.total_hard_negative += 1
        if incorrectly_flagged:
            self.false_positives_hard += 1
        self.details.append(
            {
                "type": "hard_negative",
                "case_id": case_id,
                "incorrectly_flagged": incorrectly_flagged,
            }
        )

    def add_easy_negative(self, case_id: str, incorrectly_flagged: bool):
        """添加 easy negative 结果"""
        self.total_easy_negative += 1
        if incorrectly_flagged:
            self.false_positives_easy += 1
        self.details.append(
            {
                "type": "easy_negative",
                "case_id": case_id,
                "incorrectly_flagged": incorrectly_flagged,
            }
        )

    def compute_metrics(self) -> dict[str, Any]:
        """计算评测指标"""
        recall = self.detected_positive / self.total_positive if self.total_positive > 0 else 0.0

        total_negatives = self.total_hard_negative + self.total_easy_negative
        total_false_positives = self.false_positives_hard + self.false_positives_easy
        false_positive_rate = total_false_positives / total_negatives if total_negatives > 0 else 0.0

        hard_precision = (
            1.0 - (self.false_positives_hard / self.total_hard_negative)
            if self.total_hard_negative > 0
            else 1.0
        )

        return {
            "recall": recall,
            "false_positive_rate": false_positive_rate,
            "hard_negative_precision": hard_precision,
            "positive_detected": f"{self.detected_positive}/{self.total_positive}",
            "hard_negatives_correct": f"{self.total_hard_negative - self.false_positives_hard}/{self.total_hard_negative}",
            "easy_negatives_correct": f"{self.total_easy_negative - self.false_positives_easy}/{self.total_easy_negative}",
        }

    def report(self) -> str:
        """生成报告"""
        metrics = self.compute_metrics()
        lines = [
            "=== Consistency Rule Evaluation Report ===",
            "",
            f"Recall: {metrics['recall']:.2%} ({metrics['positive_detected']})",
            f"False Positive Rate: {metrics['false_positive_rate']:.2%}",
            f"Hard Negative Precision: {metrics['hard_negative_precision']:.2%} ({metrics['hard_negatives_correct']})",
            f"Easy Negative Precision: {metrics['easy_negatives_correct']}",
            "",
        ]

        # 按类型分组
        positive_details = [d for d in self.details if d["type"] == "positive"]
        hard_neg_details = [d for d in self.details if d["type"] == "hard_negative"]

        if positive_details:
            lines.append("Positive Cases:")
            for detail in positive_details:
                status = "✓ DETECTED" if detail["detected"] else "✗ MISSED"
                lines.append(f"  {detail['case_id']}: {status}")
            lines.append("")

        if hard_neg_details:
            lines.append("Hard Negatives:")
            for detail in hard_neg_details:
                status = "✗ FALSE POSITIVE" if detail["incorrectly_flagged"] else "✓ CORRECT"
                lines.append(f"  {detail['case_id']}: {status}")
            lines.append("")

        return "\n".join(lines)


def run_smoke_test():
    """运行 smoke test - 不依赖数据库/LLM"""
    fixtures = get_all_fixtures()
    result = EvaluationResult()

    # 模拟评测逻辑
    for case in fixtures["positive"]:
        # 简化检测逻辑：检查是否有冲突的 claim
        detected = case["expected_conflict"]
        evidence_matched = True  # 简化：假设证据匹配
        result.add_positive(case["id"], detected, evidence_matched)

    for case in fixtures["hard_negative"]:
        # 简化逻辑：hard negative 不应被标记为冲突
        incorrectly_flagged = case["expected_conflict"]
        result.add_hard_negative(case["id"], incorrectly_flagged)

    for case in fixtures["easy_negative"]:
        # 简化逻辑：easy negative 不应被标记为冲突
        incorrectly_flagged = case["expected_conflict"]
        result.add_easy_negative(case["id"], incorrectly_flagged)

    return result


def test_smoke_evaluation():
    """Smoke test - 验证评测框架可运行"""
    result = run_smoke_test()
    metrics = result.compute_metrics()

    # 验证指标计算正确
    assert 0.0 <= metrics["recall"] <= 1.0
    assert 0.0 <= metrics["false_positive_rate"] <= 1.0
    assert 0.0 <= metrics["hard_negative_precision"] <= 1.0

    # 打印报告
    print(result.report())

    # Smoke test 只验证框架可运行，不设置上线门槛
    assert result.total_positive > 0
    assert result.total_hard_negative > 0


if __name__ == "__main__":
    test_smoke_evaluation()
