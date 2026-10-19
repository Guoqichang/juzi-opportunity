from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .contracts import Fact, PolicyDraft, Rule, Truth, Window

VERSION = "rules-1"


def rule_result(rule: Rule, facts: dict[str, Fact], as_of: datetime) -> dict:
    children = [rule_result(child, facts, as_of) for child in rule.children]
    states = [child["result"] for child in children]
    reason = ""
    status = Truth.UNKNOWN
    if rule.op == "all":
        status = (
            Truth.FALSE
            if Truth.FALSE in states
            else Truth.TRUE
            if all(state == Truth.TRUE for state in states)
            else Truth.UNKNOWN
        )
    elif rule.op == "any":
        status = (
            Truth.TRUE
            if Truth.TRUE in states
            else Truth.FALSE
            if all(state == Truth.FALSE for state in states)
            else Truth.UNKNOWN
        )
    elif rule.op == "not":
        status = {Truth.TRUE: Truth.FALSE, Truth.FALSE: Truth.TRUE, Truth.UNKNOWN: Truth.UNKNOWN}[states[0]]
    elif rule.op == "manual":
        reason = "需要人工核实该条件"
    else:
        fact = facts.get(rule.field)
        today = as_of.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if fact is None or fact.status != "confirmed":
            reason = "缺少已确认的公司事实；估算不能用于通过资格检查"
        elif (fact.valid_from and today < fact.valid_from) or (fact.valid_to and today > fact.valid_to):
            reason = "公司事实不在有效期间"
        else:
            try:
                actual, expected = fact.value, rule.value
                if rule.op == "eq":
                    # bool is an int subclass in Python, but not a valid replacement for numeric facts.
                    passed = type(actual) is type(expected) and actual == expected
                elif rule.op == "in":
                    passed = any(type(actual) is type(item) and actual == item for item in expected)
                elif rule.op in {"gte", "lte"}:
                    if isinstance(actual, bool) or isinstance(expected, bool):
                        raise ValueError("boolean is not a monetary/quantity value")
                    if isinstance(actual, str) and len(actual) == 10 and actual[4] == "-":
                        left, right = date.fromisoformat(actual), date.fromisoformat(expected)
                    else:
                        left, right = Decimal(str(actual)), Decimal(str(expected))
                        if not left.is_finite() or not right.is_finite():
                            raise ValueError("non-finite values are not evidence")
                    passed = left >= right if rule.op == "gte" else left <= right
                else:
                    if isinstance(actual, str) and len(actual) == 10 and actual[4] == "-":
                        left, right = map(date.fromisoformat, expected)
                        value = date.fromisoformat(actual)
                    else:
                        if isinstance(actual, bool) or any(isinstance(v, bool) for v in expected):
                            raise ValueError("boolean is not numeric")
                        left, right = map(lambda v: Decimal(str(v)), expected)
                        value = Decimal(str(actual))
                        if not all(v.is_finite() for v in (left, right, value)):
                            raise ValueError("non-finite range")
                    if left > right:
                        raise ValueError("inverted bounds")
                    passed = left <= value <= right
                status = Truth.TRUE if passed else Truth.FALSE
                reason = "已确认事实符合条款" if passed else "已确认事实不符合条款"
            except (TypeError, ValueError, InvalidOperation):
                reason = "事实与规则的类型或区间不一致，需要复核"
    return {
        "id": rule.id,
        "label": rule.label,
        "field": rule.field,
        "op": rule.op,
        "result": status.value,
        "reason": reason,
        "expected": rule.value,
        "actual": facts[rule.field].model_dump(mode="json") if rule.field in facts else None,
        "evidence": [e.model_dump() for e in rule.evidence],
        "children": children,
    }


def window_status(window: Window, as_of: datetime) -> str:
    local = as_of.astimezone(ZoneInfo(window.timezone))

    def compare(value):
        if len(value) == 10:
            boundary = date.fromisoformat(value)
            return (local.date() > boundary) - (local.date() < boundary), True
        boundary = datetime.fromisoformat(value)
        return (local > boundary) - (local < boundary), False

    # An expired end is decisive even if the start is missing.
    if window.end:
        delta, day_precision = compare(window.end)
        if delta > 0:
            return "closed"
        if delta == 0:
            return "unknown"  # time-of-day or inclusivity at the boundary needs verification
    if window.start:
        delta, day_precision = compare(window.start)
        if delta < 0:
            return "upcoming"
        if delta == 0 and day_precision:
            return "unknown"
    if window.ongoing or (window.start and window.end):
        return "open"
    return "unknown"


def evaluate(draft: PolicyDraft, facts: dict[str, Fact], as_of: datetime, *, evidence_status: str) -> dict:
    if as_of.tzinfo is None:
        raise ValueError("as_of must have a timezone")
    trace = rule_result(draft.eligibility, facts, as_of)
    eligibility = {"true": "matched", "false": "not_matched", "unknown": "unknown"}[trace["result"]]
    window = window_status(draft.window, as_of)
    if evidence_status != "complete":
        decision = "待核实"
    elif window in {"closed", "upcoming"}:
        decision = "等待新批次" if window == "closed" else "等待开放"
    elif eligibility == "not_matched":
        decision = "当前不适用"
    elif window == "open" and eligibility == "matched":
        decision = "可推进"
    else:
        decision = "待核实"
    gaps = []

    def collect(node):
        if not node["children"] and node["result"] == "unknown":
            gaps.append({"field": node["field"], "label": node["label"], "reason": node["reason"]})
        for child in node["children"]:
            collect(child)

    collect(trace)
    if window == "unknown":
        gaps.append({"field": "application_window", "label": "申报窗口", "reason": "起止时间或边界待核实"})
    if evidence_status != "complete":
        gaps.append({"field": "policy_evidence", "label": "政策证据", "reason": evidence_status})
    return {
        "decision": decision,
        "window_status": window,
        "eligibility_status": eligibility,
        "evidence_status": evidence_status,
        "application_stage": "unstarted",
        "trace": trace,
        "gaps": gaps,
        "materials": draft.materials,
        "as_of": as_of.isoformat(),
        "evaluator_version": VERSION,
    }
