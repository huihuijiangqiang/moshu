from services.continuity_validation import validate_structured_claims


def test_cash_ledger_mismatch_is_hard_issue():
    issues = validate_structured_claims(
        [
            {
                "id": 7,
                "predicate": "cash_balance",
                "object_value": '{"opening": 100, "income": 20, "expense": 10, "closing": 105}',
            }
        ]
    )
    assert len(issues) == 1
    assert issues[0].kind == "ledger_arithmetic"
    assert issues[0].expected == 110
    assert issues[0].actual == 105


def test_wage_and_resource_checks_are_deterministic():
    issues = validate_structured_claims(
        [
            {"id": 1, "predicate": "piece_wage", "object_value": '{"quantity": 8, "rate": 3, "total": 24}'},
            {"id": 2, "predicate": "resource_usage", "object_value": '{"used": 3, "available": 2}'},
        ]
    )
    assert [issue.kind for issue in issues] == ["resource_overuse"]


def test_free_form_claims_are_not_guessed():
    assert validate_structured_claims(
        [{"id": 1, "predicate": "cash_balance", "object_value": "她手里还剩十两银子"}]
    ) == []
