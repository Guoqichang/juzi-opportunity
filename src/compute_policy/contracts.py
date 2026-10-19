from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Truth(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class Evidence(Contract):
    block_id: str
    quote: str = Field(min_length=4, max_length=4000)


class Fact(Contract):
    value: Any = None
    status: Literal["confirmed", "estimate", "unknown"] = "unknown"
    evidence: str = ""
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.status == "confirmed" and (self.value is None or not self.evidence.strip()):
            raise ValueError("confirmed facts require a value and evidence/source description")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("invalid fact validity interval")
        return self


class CompanyInput(Contract):
    name: str = Field(min_length=1, max_length=200)
    facts: dict[str, Fact] = Field(default_factory=dict)
    expected_version: int = Field(ge=0)


class Window(Contract):
    start: str | None = None
    end: str | None = None
    ongoing: bool = False
    timezone: Literal["Asia/Shanghai", "Asia/Singapore", "UTC"] = "Asia/Shanghai"
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_bounds(self):
        for value in (self.start, self.end):
            if value:
                if len(value) == 10:
                    date.fromisoformat(value)
                else:
                    dt = datetime.fromisoformat(value)
                    if dt.tzinfo is None:
                        raise ValueError("timestamps require an explicit timezone; dates keep day precision")
        if self.ongoing and self.end:
            raise ValueError("ongoing cannot also have an end")
        if self.start and self.end:

            def bound(value):
                parsed = datetime.fromisoformat(value)
                return parsed.replace(tzinfo=ZoneInfo(self.timezone)) if parsed.tzinfo is None else parsed

            if bound(self.start) > bound(self.end):
                raise ValueError("window start must not follow its end")
        return self


class Rule(Contract):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=500)
    op: Literal["all", "any", "not", "eq", "in", "gte", "lte", "between", "manual"]
    field: str | None = None
    value: Any = None
    children: list[Rule] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def grammar(self):
        if self.op in {"all", "any"}:
            if not self.children:
                raise ValueError("all/any require at least one child")
        elif self.op == "not":
            if len(self.children) != 1:
                raise ValueError("not requires exactly one child")
        else:
            if self.children:
                raise ValueError("leaf rule cannot have children")
            if self.op != "manual" and not self.field:
                raise ValueError("comparison rule needs a fact field")
            if self.op in {"in", "between"} and not isinstance(self.value, list):
                raise ValueError("in/between require a list")
            if self.op == "between" and len(self.value) != 2:
                raise ValueError("between requires two bounds")
            if self.op != "manual" and self.value is None:
                raise ValueError("comparison rule needs a value")
        return self


class PolicyDraft(Contract):
    title: str = Field(min_length=1, max_length=500)
    issuer: str = ""
    batch: str = "待核实"
    support_kind: Literal["subsidy", "voucher", "discount", "cloud_credit", "event", "unknown"] = "unknown"
    window: Window = Field(default_factory=Window)
    eligibility: Rule = Field(
        default_factory=lambda: Rule(id="unread", label="资格条款尚未复核", op="manual")
    )
    materials: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    completeness: Literal["partial", "complete"] = "partial"
    summary: str = ""
    identity_evidence: list[Evidence] = Field(default_factory=list)
    summary_evidence: list[Evidence] = Field(default_factory=list)
    material_evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def bound_tree(self):
        seen = set()

        def visit(rule, depth):
            if depth > 12 or len(seen) >= 200:
                raise ValueError("rule tree too large")
            if rule.id in seen:
                raise ValueError("rule IDs must be unique")
            seen.add(rule.id)
            for child in rule.children:
                visit(child, depth + 1)

        visit(self.eligibility, 0)
        return self


class Approval(Contract):
    draft: PolicyDraft
    expected_bundle_hash: str
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=2000)


def referenced_fields(rule: Rule) -> set[str]:
    result = {rule.field} if rule.field else set()
    for child in rule.children:
        result |= referenced_fields(child)
    return result


def all_evidence(draft: PolicyDraft) -> list[Evidence]:
    result = (
        list(draft.window.evidence)
        + draft.identity_evidence
        + draft.summary_evidence
        + draft.material_evidence
    )

    def visit(rule):
        result.extend(rule.evidence)
        for child in rule.children:
            visit(child)

    visit(draft.eligibility)
    return result
