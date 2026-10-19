"""Evidence-linked action lists and human feedback, separate from eligibility approval."""

import re
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import Field
from sqlalchemy import select, update

from .contracts import Contract
from .discovery import Discovery, is_trusted
from .research_contracts import ResearchSettings
from .service import Conflict, record
from .store import Candidate, Followup, SourceRelation, digest, now, stamp


class FollowupInput(Contract):
    status: Literal["待核实", "值得跟进", "不适合", "已处理"] = "待核实"
    owner: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=4000)
    expected_version: int = Field(ge=0)


class RelationInput(Contract):
    left_id: str
    right_id: str
    relation: Literal["待核对", "同批次转载", "同批次解读", "补充更正", "不同事项"] = "待核对"
    program: str = Field(default="", max_length=500)
    batch: str = Field(default="", max_length=200)
    evidence: str = Field(default="", max_length=4000)
    expected_version: int = Field(ge=0)


def crawl_due(source):
    if not source["last_checked"]:
        return True
    hours = source["interval_hours"]
    if source["health"] == "failed":
        hours = max(
            hours,
            168
            if any(s in source["error"].lower() for s in ("robots", "certificate", "ssl", "403", "outside"))
            else 24,
        )
    return now() - datetime.fromisoformat(source["last_checked"]) >= timedelta(hours=hours)


def research_priority(candidate):
    value = candidate["score"]
    if candidate["document_kind"] not in {"open_call", "program"}:
        value -= 100
    if candidate.get("deadline_hint_status") == "possibly_past":
        value -= 100
    for hint in candidate.get("hints", {}).get("dates", []):
        days = (date.fromisoformat(hint["date"]) - now().date()).days
        if hint["kind"] == "possible_deadline" and 0 <= days <= 14:
            value += 40
    return value


class Actions:
    def __init__(self, service):
        self.service, self.store = service, service.store

    def feedback(self, company_id):
        with self.store.session() as session:
            return {
                r.candidate_id: record(r)
                for r in session.scalars(select(Followup).where(Followup.company_id == company_id))
            }

    def save_feedback(self, company_id, candidate_id, data):
        with self.store.session() as session:
            candidate = session.get(Candidate, candidate_id)
            if not candidate or candidate.company_id != company_id:
                raise KeyError("线索不存在")
            row = session.get(Followup, (company_id, candidate_id))
            values = data.model_dump(exclude={"expected_version"})
            if row is None:
                if data.expected_version:
                    raise Conflict("跟进状态已变化，请刷新")
                row = Followup(company_id=company_id, candidate_id=candidate_id, **values)
                session.add(row)
                session.flush()
            else:
                changed = session.execute(
                    update(Followup)
                    .where(
                        Followup.company_id == company_id,
                        Followup.candidate_id == candidate_id,
                        Followup.version == data.expected_version,
                    )
                    .values(**values, version=data.expected_version + 1, updated_at=stamp())
                ).rowcount
                if not changed:
                    raise Conflict("跟进状态已变化，请刷新，避免覆盖另一端的修改")
                session.refresh(row)
            return record(row)

    def relations(self, company_id):
        candidates = Discovery(self.service).candidates(company_id, limit=None)
        by_source = {c["source_id"]: c for c in candidates if c["source_id"]}
        with self.store.session() as session:
            rows = {
                r.id: record(r)
                for r in session.scalars(
                    select(SourceRelation).where(SourceRelation.company_id == company_id)
                )
            }
        for candidate in candidates:
            origin = candidate["hints"].get("alternative_for", {})
            left = by_source.get(origin.get("source_id"))
            if left and left["id"] != candidate["id"]:
                key = digest([company_id, *sorted([left["id"], candidate["id"]])])
                rows.setdefault(
                    key,
                    {
                        "id": key,
                        "company_id": company_id,
                        "left_id": left["id"],
                        "right_id": candidate["id"],
                        "relation": "待核对",
                        "program": "",
                        "batch": "",
                        "evidence": "研究工具找到相关原文，是否同一事项尚未确认。",
                        "version": 0,
                    },
                )
        names = {c["id"]: c for c in candidates}
        return [
            {
                **r,
                "left_title": names.get(r["left_id"], {}).get("title", ""),
                "right_title": names.get(r["right_id"], {}).get("title", ""),
            }
            for r in rows.values()
        ]

    def save_relation(self, company_id, data):
        with self.store.session() as session:
            left, right = session.get(Candidate, data.left_id), session.get(Candidate, data.right_id)
            if (
                not left
                or not right
                or left.company_id != company_id
                or right.company_id != company_id
                or left.id == right.id
            ):
                raise ValueError("请选择同一公司下的两条不同线索")
            if data.relation != "待核对" and not data.evidence.strip():
                raise ValueError("确认来源关系需要填写原文依据")
            if data.relation in {"同批次转载", "同批次解读"}:
                for pattern in [r"20\d{2}", r"[上下]半年", r"第[一二三四五六七八九十\d]+批"]:
                    a, b = set(re.findall(pattern, left.title)), set(re.findall(pattern, right.title))
                    if a and b and a != b:
                        raise ValueError("标题年份或批次存在冲突，不能关联为同批次")
            key = digest([company_id, *sorted([data.left_id, data.right_id])])
            old = session.get(SourceRelation, key)
            if (old.version if old else 0) != data.expected_version:
                raise Conflict("来源关系已更新，请刷新")
            values = data.model_dump(exclude={"expected_version"})
            if old:
                changed = session.execute(
                    update(SourceRelation)
                    .where(SourceRelation.id == key, SourceRelation.version == data.expected_version)
                    .values(**values, version=data.expected_version + 1, updated_at=stamp())
                ).rowcount
                if not changed:
                    raise Conflict("来源关系已更新")
            else:
                session.add(SourceRelation(id=key, company_id=company_id, **values))
        return next(r for r in self.relations(company_id) if r["id"] == key)

    def plan(self, company_id, limit=5):
        candidates = Discovery(self.service).candidates(company_id, limit=None)
        feedback = self.feedback(company_id)
        settings = ResearchSettings.model_validate(Discovery(self.service).config(company_id)["settings"])
        work = {i["source_id"]: i for i in self.service.worklist(company_id)["items"]}
        actions = []
        for candidate in candidates:
            followup = feedback.get(
                candidate["id"], {"status": "待核实", "owner": "", "note": "", "version": 0}
            )
            item = work.get(candidate["source_id"], {})
            if (
                followup["status"] in {"不适合", "已处理"}
                or candidate["document_kind"] not in {"open_call", "program"}
                or candidate["deadline_hint_status"] == "possibly_past"
                or item.get("window_status") == "closed"
            ):
                continue
            draft = None
            if candidate["source_id"]:
                detail = self.service.detail(candidate["source_id"])
                draft = detail["policy"]["draft"] if detail["policy"] else None
            if candidate["state"] != "collected":
                next_action = "取得完整原文及附件，再判断是否适合"
            elif not draft or draft.get("eligibility", {}).get("id") == "unread":
                next_action = "提取并核对资格、申报窗口和材料要求"
            elif item.get("gaps"):
                next_action = "补齐或核实：" + "；".join(g["label"] for g in item["gaps"][:3])
            elif item.get("decision") == "可推进":
                next_action = "按已核对清单准备材料并确认申报入口"
            else:
                next_action = "复核原文条款和申报窗口，再决定是否跟进"
            score = (
                research_priority(candidate)
                + (30 if followup["status"] == "值得跟进" else 0)
                + (10 if candidate["state"] == "collected" else 0)
                + (8 if is_trusted(candidate["url"], settings.trusted_domains) else 0)
            )
            evidence = []
            if draft:
                evidence = draft.get("window", {}).get("evidence", []) + draft.get("identity_evidence", [])
            actions.append(
                {
                    "candidate_id": candidate["id"],
                    "source_id": candidate["source_id"],
                    "title": candidate["title"],
                    "url": candidate["url"],
                    "why": "；".join(candidate["reasons"]),
                    "next_action": next_action,
                    "decision": item.get("decision", "待核实"),
                    "window_status": item.get("window_status", "unknown"),
                    "deadline": (draft or {}).get("window", {}).get("end"),
                    "summary": (draft or {}).get("summary", ""),
                    "batch": (draft or {}).get("batch", "待核实"),
                    "materials": (draft or {}).get("materials", []),
                    "gaps": item.get("gaps", []),
                    "evidence": evidence,
                    "followup": followup,
                    "score": score,
                }
            )
        actions.sort(key=lambda a: (-a["score"], a["candidate_id"]))
        return actions[:limit]
