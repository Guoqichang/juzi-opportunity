from datetime import UTC, datetime
from itertools import product

import pytest
from pydantic import ValidationError

from compute_policy.contracts import Fact, PolicyDraft, Rule, Window
from compute_policy.evaluator import evaluate, rule_result, window_status

AT = datetime(2026, 9, 23, 4, tzinfo=UTC)


@pytest.mark.parametrize("op", ["all", "any"])
@pytest.mark.parametrize("left,right", list(product(["true", "false", "unknown"], repeat=2)))
def test_three_value_logic(op, left, right):
    states = {"true": True, "false": False}
    facts = {
        k: Fact(value=states[v], status="confirmed", evidence="test")
        for k, v in {"a": left, "b": right}.items()
        if v != "unknown"
    }
    rule = Rule(
        id="r",
        label="r",
        op=op,
        children=[Rule(id=k, label=k, op="eq", field=k, value=True) for k in ("a", "b")],
    )
    expected = (
        ("false" if "false" in [left, right] else "true" if left == right == "true" else "unknown")
        if op == "all"
        else ("true" if "true" in [left, right] else "false" if left == right == "false" else "unknown")
    )
    assert rule_result(rule, facts, AT)["result"] == expected


def test_estimates_and_expired_facts_never_pass():
    rule = Rule(id="spend", label="支出", op="gte", field="spend", value=300000)
    for fact in [
        Fact(value=500000, status="estimate"),
        Fact(value=500000, status="confirmed", evidence="old invoice", valid_to="2026-08-01"),
    ]:
        assert rule_result(rule, {"spend": fact}, AT)["result"] == "unknown"


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "unknown", []])
def test_bad_numeric_facts_are_unknown(value):
    rule = Rule(id="spend", label="支出", op="gte", field="spend", value=1)
    assert (
        rule_result(rule, {"spend": Fact(value=value, status="confirmed", evidence="test")}, AT)["result"]
        == "unknown"
    )


def test_not_unknown_and_date_comparison():
    child = Rule(id="a", label="a", op="manual")
    assert rule_result(Rule(id="not", label="not", op="not", children=[child]), {}, AT)["result"] == "unknown"
    date_rule = Rule(
        id="r", label="注册日期", op="between", field="registered", value=["2020-01-01", "2026-01-01"]
    )
    assert (
        rule_result(
            date_rule, {"registered": Fact(value="2025-01-01", status="confirmed", evidence="test")}, AT
        )["result"]
        == "true"
    )


@pytest.mark.parametrize(
    "window,expected",
    [
        ({"start": "2026-09-01", "end": "2026-09-30"}, "open"),
        ({"start": "2026-10-01", "end": "2026-11-01"}, "upcoming"),
        ({"end": "2026-09-22"}, "closed"),
        ({"end": "2026-09-23"}, "unknown"),
        ({"end": "2026-09-24"}, "unknown"),
        ({"ongoing": True}, "open"),
        ({"start": "2026-09-01T00:00:00+08:00", "end": "2026-09-23T12:00:00+08:00"}, "unknown"),
    ],
)
def test_window_precision(window, expected):
    assert window_status(Window(**window), AT) == expected


def test_invalid_bounds_and_rule_tree():
    with pytest.raises(ValidationError):
        Window(start="2026-10-01", end="2026-09-01")
    with pytest.raises(ValidationError):
        Rule(id="empty", label="empty", op="all")
    with pytest.raises(ValidationError):
        Fact(value=3, status="confirmed")


def test_evidence_health_overrides_positive_match():
    draft = PolicyDraft(
        title="x",
        window=Window(ongoing=True),
        eligibility=Rule(id="a", label="location", op="eq", field="location", value="北京"),
    )
    facts = {"location": Fact(value="北京", status="confirmed", evidence="licence")}
    result = evaluate(draft, facts, AT, evidence_status="附件缺失")
    assert result["eligibility_status"] == "matched"
    assert result["decision"] == "待核实"
