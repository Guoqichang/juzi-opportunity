from __future__ import annotations

import re

from sqlalchemy import select

from .contracts import CompanyInput, Fact
from .discovery import IDENTITY_POLICY, Discovery
from .research_contracts import FIELD_QUESTIONS, FactAnswer, FormRequest, KnowledgeInput, QuestionInput
from .service import Conflict, Service, record
from .store import ApplicationDraft, Dialogue, KnowledgeEntry, digest, now


def tokens(text):
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    for chunk in re.findall(r"[\u4e00-\u9fff]+", lowered):
        words.update(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return words - {"什么", "我们", "可以", "这个", "哪些", "怎么", "需要", "一下", "公司", "是否"}


def valid_fact(fact, at=None):
    at = at or now().date()
    return (
        fact.status == "confirmed"
        and not (fact.valid_from and at < fact.valid_from)
        and not (fact.valid_to and at > fact.valid_to)
    )


def excerpt(text, query_tokens, limit=650):
    """Keep a contiguous, citable passage around the best matching sentence."""
    sentences = list(re.finditer(r"[^。！？\n]+[。！？\n]?", text))
    if not sentences:
        return text[:limit]
    best = max(sentences, key=lambda match: len(tokens(match.group()) & query_tokens))
    start = best.start()
    if best.end() - start > limit:
        positions = [best.group().lower().find(word) for word in query_tokens if word in best.group().lower()]
        start += max(0, min(positions, default=0) - 120)
    return text[start : start + limit]


class Knowledge:
    def __init__(self, service: Service):
        self.service, self.store = service, service.store

    def entries(self, company_id, *, history=False):
        self.service.company(company_id)
        with self.store.session() as session:
            rows = list(
                session.scalars(
                    select(KnowledgeEntry)
                    .where(KnowledgeEntry.company_id == company_id)
                    .order_by(KnowledgeEntry.version.desc(), KnowledgeEntry.created_at.desc())
                )
            )
        if history:
            return [record(row) for row in rows]
        latest = {}
        for row in rows:
            latest.setdefault(row.key, record(row))
        return [row for row in latest.values() if row["status"] != "retired"]

    def save(self, company_id, data: KnowledgeInput):
        self.service.company(company_id)
        with self.store.session() as session:
            last = session.scalar(
                select(KnowledgeEntry)
                .where(KnowledgeEntry.company_id == company_id, KnowledgeEntry.key == data.key)
                .order_by(KnowledgeEntry.version.desc())
                .limit(1)
            )
            if (last.version if last else 0) != data.expected_version:
                raise Conflict("知识条目版本已变化，请刷新")
            row = KnowledgeEntry(
                company_id=company_id,
                key=data.key,
                version=data.expected_version + 1,
                title=data.title,
                body=data.body,
                source_ref=data.source_ref,
                status=data.status,
                content_hash=digest(data.model_dump(exclude={"expected_version"})),
            )
            session.add(row)
            session.flush()
            return record(row)

    def followups(self, company_id, source_id=None, *, limit=8):
        company = self.service.company(company_id)
        work = self.service.worklist(company_id)
        fields = {}
        for item in work["items"]:
            if source_id and item["source_id"] != source_id:
                continue
            for gap in item["gaps"]:
                field = gap.get("field")
                if field and field not in {"application_window", "policy_evidence"}:
                    fields[field] = gap["label"]
        with self.store.session() as session:
            recent_draft = session.scalar(
                select(ApplicationDraft)
                .where(ApplicationDraft.company_id == company_id)
                .order_by(ApplicationDraft.created_at.desc())
                .limit(1)
            )
            if recent_draft and (not source_id or recent_draft.source_id == source_id):
                for field in recent_draft.payload["fields"]:
                    fact = Fact.model_validate(company["facts"].get(field["field"], {}))
                    if field["required"] and not valid_fact(fact):
                        fields.setdefault(field["field"], field["label"])
        for key in ["legal_name", "registered_district", "eligible_compute_spend_cny", "sme_qualified"]:
            fact = Fact.model_validate(company["facts"].get(key, {}))
            if not valid_fact(fact):
                fields.setdefault(key, FIELD_QUESTIONS[key]["label"])
        questions = []
        for key, label in fields.items():
            questions.append(
                {
                    "field": key,
                    "label": label,
                    "question": FIELD_QUESTIONS.get(key, {}).get(
                        "question", f"请核实“{label}”，并提供来源与有效期。"
                    ),
                    "type": FIELD_QUESTIONS.get(key, {}).get("type", "text"),
                    "facts_version": company["version"],
                }
            )
        return questions if limit is None else questions[:limit]

    def answer_fact(self, company_id, data: FactAnswer):
        company = self.service.company(company_id)
        if company["version"] != data.expected_version:
            raise Conflict("公司事实已更新，请刷新后回答")
        facts = dict(company["facts"])
        facts[data.field] = data.fact.model_dump(mode="json")
        updated = self.service.save_company(
            company_id,
            CompanyInput(name=company["name"], facts=facts, expected_version=data.expected_version),
        )
        return {"company": updated, "next_questions": self.followups(company_id)}

    def ask(self, company_id, data: QuestionInput):
        company = self.service.company(company_id)
        focus = data.source_id
        if not focus and any(word in data.question for word in ["这个", "那它", "刚才", "继续"]):
            with self.store.session() as session:
                last = session.scalar(
                    select(Dialogue)
                    .where(Dialogue.company_id == company_id, Dialogue.session_id == data.session_id)
                    .order_by(Dialogue.created_at.desc())
                    .limit(1)
                )
                if last:
                    focus = last.response.get("source_id")
        query_tokens = tokens(data.question)
        snippets = []
        for entry in self.entries(company_id):
            for index, paragraph in enumerate(re.split(r"\n\s*\n|\n", entry["body"])):
                if not paragraph.strip():
                    continue
                score = len(tokens(entry["title"] + " " + paragraph) & query_tokens)
                if score:
                    snippets.append(
                        {
                            "score": score,
                            "kind": "knowledge",
                            "title": entry["title"],
                            "quote": paragraph[:800],
                            "citation": f"kb:{entry['id']}:{index}",
                            "version": entry["version"],
                            "source_ref": entry["source_ref"],
                            "status": entry["status"],
                        }
                    )
        work = self.service.worklist(company_id)
        candidates = Discovery(self.service).candidates(company_id)
        candidate_map = {c["source_id"]: c for c in candidates if c["source_id"]}
        event_intent = any(word in data.question for word in ["活动", "路演", "竞赛", "大赛", "峰会", "沙龙"])
        support_intent = any(word in data.question for word in ["算力", "模型券", "补贴", "云资源"])
        historical = any(
            word in data.question
            for word in ["历史", "往年", "回顾", "公示", "结果", "已截止", "失效", "废止"]
        )
        scored_items = []
        for item in work["items"]:
            if focus and item["source_id"] != focus:
                continue
            candidate = candidate_map.get(item["source_id"], {})
            if not focus:
                if event_intent and candidate.get("category") != "event":
                    continue
                if support_intent and not event_intent and candidate.get("category") == "event":
                    continue
                if not historical and (
                    candidate.get("document_kind") in {"result_notice", "recap", "expired_notice"}
                    or candidate.get("deadline_hint_status") == "possibly_past"
                ):
                    continue
                if candidate and candidate.get("document_kind") == "information" and not historical:
                    continue
            score = len(tokens(item["title"]) & query_tokens)
            if event_intent and candidate.get("category") == "event":
                score += 5
            if support_intent and candidate.get("category") in {"compute_support", "cloud_credit"}:
                score += 5
            if (
                not focus
                and score == 0
                and not any(w in data.question for w in ["机会", "申请", "推荐", "缺什么", "进展"])
            ):
                continue
            priority = score + (8 if candidate.get("document_kind") == "open_call" else 0)
            scored_items.append((priority, candidate.get("score", 0), item))
        scored_items.sort(key=lambda value: value[:2], reverse=True)
        selected = [item for _, _, item in scored_items[:4]]
        for item in selected:
            detail = self.service.detail(item["source_id"])
            source_snippets = []
            for block in detail["blocks"]:
                score = len(tokens(block["text"]) & query_tokens)
                if score:
                    source_snippets.append(
                        {
                            "score": score,
                            "kind": "policy",
                            "source_id": item["source_id"],
                            "title": item["title"],
                            "quote": excerpt(block["text"], query_tokens),
                            "citation": block["id"],
                            "source_ref": block["source_url"],
                            "snapshot_id": block["snapshot_id"],
                            "status": "原文摘录，资格仍以复核结果为准",
                        }
                    )
            source_snippets.sort(key=lambda row: row["score"], reverse=True)
            # One passage per opportunity keeps long navigation blocks from flooding the answer.
            snippets.extend(source_snippets[:1])
        snippets.sort(key=lambda row: row["score"], reverse=True)
        citations, seen_citations = [], set()
        knowledge_count = 0
        for snippet in snippets:
            citation_key = (snippet["kind"], snippet["source_ref"], snippet["citation"], snippet["quote"])
            if citation_key in seen_citations or (snippet["kind"] == "knowledge" and knowledge_count >= 2):
                continue
            citations.append(snippet)
            seen_citations.add(citation_key)
            knowledge_count += int(snippet["kind"] == "knowledge")
            if len(citations) == 6:
                break
        lines = []
        if selected:
            lines.append("按已收集的原文和当前公司资料，建议先看以下项目：")
            if len(selected) > 1:
                lines.append("以下线索按来源分别列出，尚未确认是否属于同一事项；条件和日期不能跨来源套用。")
            for item in selected:
                c = candidate_map.get(item["source_id"], {})
                suffix = (
                    "；原文标注失效/废止，不能作为当前申报依据"
                    if c.get("document_kind") == "expired_notice"
                    else "；这是结果公示/活动回顾，不代表开放报名"
                    if c.get("document_kind") in {"result_notice", "recap"}
                    else ""
                )
                lines.append(f"• {item['title']}：{item['decision']}{suffix}。")
                lines.append(f"  来源：{item['source_url']}")
                if "材料" in data.question and item["materials"]:
                    lines.append("  已整理材料：" + "、".join(item["materials"]))
                elif item["gaps"]:
                    lines.append("  待解决：" + "；".join(gap["label"] for gap in item["gaps"][:3]))
        elif event_intent:
            lines.append(
                "目前已采集的活动里，没有足够明确且未见过期线索的报名通知。雷达中的回顾、结果公示和未采集线索不作为报名推荐。"
            )
        if citations:
            lines.append("下方是检索到的依据。引用未覆盖的问题，我暂时不能确定。")
        elif not selected:
            lines.append(
                "当前已采集资料和句子知识库里没有足够依据回答这个问题。可以启动新一轮发现，或补充相关公司资料。"
            )
        if any(word in data.question for word in ["能申请", "符合", "资格", "缺什么"]):
            lines.append("相关性不等于资格。公司主体、地区、费用期间及政策例外条件需要分别确认。")
        response = {
            "answer": "\n".join(lines),
            "mode": "local_evidence_retrieval",
            "identity_policy": IDENTITY_POLICY,
            "citations": citations,
            "opportunities": [
                {k: item[k] for k in ["source_id", "title", "decision", "source_url"]} for item in selected
            ],
            "followup_questions": self.followups(company_id, focus),
            "facts_version": company["version"],
            "source_id": focus or (selected[0]["source_id"] if len(selected) == 1 else None),
            "as_of": now().isoformat(),
        }
        with self.store.session() as session:
            session.add(
                Dialogue(
                    company_id=company_id,
                    session_id=data.session_id,
                    question=data.question,
                    response=response,
                )
            )
        return response

    def conversation(self, company_id, session_id="default"):
        with self.store.session() as session:
            rows = list(
                session.scalars(
                    select(Dialogue)
                    .where(Dialogue.company_id == company_id, Dialogue.session_id == session_id)
                    .order_by(Dialogue.created_at.desc())
                    .limit(30)
                )
            )
            return [record(row) for row in reversed(rows)]

    def prepare_form(self, company_id, data: FormRequest):
        company = self.service.company(company_id)
        detail = self.service.detail(data.source_id)
        allowed = {item["source_id"] for item in self.service.worklist(company_id)["items"]}
        if data.source_id not in allowed:
            raise ValueError("此来源不属于当前企业的机会清单")
        fields = data.fields or [
            {"field": key, "label": FIELD_QUESTIONS[key]["label"], "required": True}
            for key in [
                "legal_name",
                "credit_code",
                "project_name",
                "project_summary",
                "contact_name",
                "contact_phone",
            ]
        ]
        values, missing = [], []
        for field in fields:
            field = field.model_dump() if hasattr(field, "model_dump") else field
            fact = Fact.model_validate(company["facts"].get(field["field"], {}))
            valid = valid_fact(fact)
            values.append(
                {
                    **field,
                    "value": fact.value if valid else None,
                    "evidence": fact.evidence if valid else "",
                    "status": "prefilled" if valid else "needs_answer",
                }
            )
            if not valid and field["required"]:
                missing.append(field["label"])
        payload = {
            "template_name": data.template_name,
            "template_type": "user_mapped" if data.fields else "generic_checklist",
            "source_url": detail["source"]["url"],
            "title": detail["source"]["title"],
            "fields": values,
            "missing": missing,
            "submission_state": "draft_only",
            "knowledge_versions": [
                {"id": entry["id"], "title": entry["title"], "version": entry["version"]}
                for entry in self.entries(company_id)
            ],
            "note": "已确认且在有效期内的公司事实可预填；声明、盖章、账号登录与最终提交尚未接入。",
        }
        with self.store.session() as session:
            row = ApplicationDraft(
                company_id=company_id,
                source_id=data.source_id,
                facts_version=company["version"],
                policy_id=detail["policy"]["id"] if detail["policy"] else None,
                payload=payload,
            )
            session.add(row)
            session.flush()
            return record(row)

    def forms(self, company_id):
        company = self.service.company(company_id)
        with self.store.session() as session:
            rows = list(
                session.scalars(
                    select(ApplicationDraft)
                    .where(ApplicationDraft.company_id == company_id)
                    .order_by(ApplicationDraft.created_at.desc())
                    .limit(30)
                )
            )
        entries = {entry["id"] for entry in self.entries(company_id)}
        results = []
        for row in rows:
            source = self.service.detail(row.source_id)["source"]
            stale = (
                company["version"] != row.facts_version
                or source["latest_policy"] != row.policy_id
                or any(e["id"] not in entries for e in row.payload["knowledge_versions"])
            )
            # A fact can expire without a version edit.
            stale = stale or any(
                not valid_fact(Fact.model_validate(company["facts"].get(f["field"], {})))
                for f in row.payload["fields"]
                if f["status"] == "prefilled"
            )
            results.append({**record(row), "stale": stale})
        return results
