from __future__ import annotations

import html
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select, update

from .acquire import PARSER_VERSION, Fetcher, Resource, normalize, parse
from .contracts import Approval, CompanyInput, Fact, PolicyDraft
from .evaluator import evaluate
from .extract import extract, validate_evidence
from .store import (
    Bundle,
    Company,
    CompanyVersion,
    Evaluation,
    Policy,
    Snapshot,
    Source,
    Store,
    digest,
    now,
    stamp,
)


class Conflict(ValueError):
    pass


def record(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class Service:
    def __init__(self, store: Store):
        self.store = store

    def add_source(self, url, title="", hosts=None, kind="policy"):
        url = normalize(url)
        host = urlsplit(url).hostname
        hosts = sorted(set([host] + [str(h).lower().strip() for h in (hosts or [])]))
        if kind not in {"policy", "demo", "import"}:
            raise ValueError("invalid source kind")
        with self.store.session() as session:
            old = session.scalar(select(Source).where(Source.url == url))
            if old:
                return record(old)
            source = Source(url=url, title=title or url, hosts=hosts, kind=kind)
            session.add(source)
            session.flush()
            return record(source)

    def sources(self):
        with self.store.session() as session:
            return [record(row) for row in session.scalars(select(Source).order_by(Source.title))]

    def _source(self, session, source_id):
        source = session.get(Source, source_id)
        if source is None:
            raise KeyError("source not found")
        return source

    def _bundle_data(self, session, bundle):
        blocks, warnings = [], []
        for item in bundle.items:
            if "error" in item:
                warnings.append(item["url"] + ": " + item["error"])
            else:
                snapshot = session.get(Snapshot, item["snapshot_id"])
                warnings.extend(snapshot.warnings)
                blocks.extend(
                    {**block, "source_url": snapshot.url, "snapshot_id": snapshot.id}
                    for block in snapshot.blocks
                )
        return blocks, warnings

    def ingest(self, source_id, resources: list[Resource], errors=None, *, expected_bundle=None):
        if not resources:
            raise ValueError("empty acquisition is not a successful policy scan")
        with self.store.session() as session:
            source = self._source(session, source_id)
            if expected_bundle is not None and source.latest_bundle != expected_bundle:
                raise Conflict("采集期间证据已被更新，请重新采集")
            items = []
            for resource in resources:
                content_hash, path = self.store.blob(resource.body)
                snapshot = session.scalar(
                    select(Snapshot).where(
                        Snapshot.source_id == source_id,
                        Snapshot.url == resource.url,
                        Snapshot.content_hash == content_hash,
                        Snapshot.parser_version == PARSER_VERSION,
                    )
                )
                if snapshot is None:
                    snapshot = Snapshot(
                        source_id=source_id,
                        url=resource.url,
                        content_hash=content_hash,
                        content_type=resource.content_type,
                        storage_path=path,
                        parser_version=PARSER_VERSION,
                        blocks=resource.blocks,
                        warnings=resource.warnings,
                    )
                    session.add(snapshot)
                    session.flush()
                items.append(
                    {
                        "url": resource.url,
                        "snapshot_id": snapshot.id,
                        "content_hash": content_hash,
                        "parser_version": PARSER_VERSION,
                    }
                )
            items.extend(errors or [])
            items.sort(key=lambda item: item["url"])
            content_hash = digest(items)
            bundle = session.scalar(
                select(Bundle).where(Bundle.source_id == source_id, Bundle.content_hash == content_hash)
            )
            if bundle is None:
                bundle = Bundle(source_id=source_id, content_hash=content_hash, items=items)
                session.add(bundle)
                session.flush()
            changed = source.latest_bundle != bundle.id
            source.latest_bundle = bundle.id
            if changed:
                draft = PolicyDraft(title=source.title, summary="证据已归档，等待提取与人工复核。")
                policy = Policy(source_id=source_id, bundle_id=bundle.id, draft=draft.model_dump(mode="json"))
                session.add(policy)
                session.flush()
                source.latest_policy = policy.id
            _, warnings = self._bundle_data(session, bundle)
            source.health = "partial" if warnings else "ok"
            source.error = "；".join(warnings)[:4000]
            source.last_checked = source.last_success = stamp()
            return {
                "source_id": source.id,
                "bundle_id": bundle.id,
                "bundle_hash": content_hash,
                "policy_id": source.latest_policy,
                "changed": changed,
                "warnings": warnings,
            }

    def import_file(self, path, title="", source_url=None):
        path = Path(path).resolve()
        if path.stat().st_size > 15 * 1024 * 1024:
            raise ValueError("file exceeds 15 MB")
        url = source_url or "https://local-import.invalid/" + path.name
        source = self.add_source(url, title or path.stem, kind="import")
        resource = parse(Resource(url, path.read_bytes(), "application/octet-stream"))
        return self.ingest(source["id"], [resource])

    def crawl(self, source_id, *, fetcher=None):
        from .discovery import Discovery

        with self.store.session() as session:
            source = record(self._source(session, source_id))
        if source["kind"] != "policy":
            raise ValueError("demo/import sources must be refreshed through file import")
        owned = fetcher is None
        fetcher = fetcher or Fetcher(source["hosts"])
        try:
            main = fetcher.fetch(source["url"])
            resources, errors = [main], []
            links = sorted({link["url"] for link in main.links if link["attachment"]} - {main.url})
            # Re-fetch every linked attachment even when the HTML body is unchanged.
            for url in links[:12]:
                try:
                    resources.append(
                        fetcher.fetch_linked(url, main)
                        if hasattr(fetcher, "fetch_linked")
                        else fetcher.fetch(url)
                    )
                except Exception as error:
                    errors.append({"url": url, "error": str(error)[:500]})
            for url in links[12:]:
                errors.append({"url": url, "error": "超过单政策 12 个附件预算，需要人工补查"})
            result = self.ingest(source_id, resources, errors, expected_bundle=source["latest_bundle"])
            Discovery(self).refresh_source(source_id, document_title=main.title)
            return result
        except Exception as error:
            with self.store.session() as session:
                row = self._source(session, source_id)
                row.health, row.error, row.last_checked = "failed", str(error)[:1000], stamp()
            Discovery(self).refresh_source(source_id)
            raise
        finally:
            if owned:
                fetcher.close()

    def detail(self, source_id):
        with self.store.session() as session:
            source = self._source(session, source_id)
            result = {"source": record(source), "bundle": None, "policy": None, "blocks": [], "warnings": []}
            if source.latest_bundle:
                bundle = session.get(Bundle, source.latest_bundle)
                result["bundle"] = record(bundle)
                result["blocks"], result["warnings"] = self._bundle_data(session, bundle)
                result["policy"] = record(session.get(Policy, source.latest_policy))
            result["history"] = [
                record(p)
                for p in session.scalars(
                    select(Policy)
                    .where(Policy.source_id == source_id)
                    .order_by(Policy.created_at.desc())
                    .limit(30)
                )
            ]
            return result

    def extract(self, source_id):
        from .extract import configured, reserve

        detail = self.detail(source_id)
        if not detail["bundle"]:
            raise ValueError("请先采集政策")
        if configured():
            reserve(self, detail["bundle"]["id"])
        draft, extractor = extract(detail["source"]["title"], detail["blocks"])
        if detail["warnings"]:
            draft.completeness = "partial"
        with self.store.session() as session:
            source = self._source(session, source_id)
            if (
                source.latest_bundle != detail["bundle"]["id"]
                or source.latest_policy != detail["policy"]["id"]
            ):
                raise Conflict("提取期间政策发生变化，请重试")
            policy = Policy(
                source_id=source_id,
                bundle_id=source.latest_bundle,
                draft=draft.model_dump(mode="json"),
                extractor=extractor,
            )
            session.add(policy)
            session.flush()
            source.latest_policy = policy.id
            return record(policy)

    def approve(self, policy_id, approval: Approval):
        with self.store.session() as session:
            old = session.get(Policy, policy_id)
            if old is None:
                raise KeyError("policy not found")
            source = self._source(session, old.source_id)
            bundle = session.get(Bundle, old.bundle_id)
            if (
                source.latest_policy != old.id
                or source.latest_bundle != bundle.id
                or approval.expected_bundle_hash != bundle.content_hash
            ):
                raise Conflict("政策或证据已改变，请刷新后复核")
            blocks, warnings = self._bundle_data(session, bundle)
            validate_evidence(approval.draft, blocks, reviewed=True)
            if warnings and approval.draft.completeness == "complete":
                raise ValueError("有缺失附件或解析警告；补全证据后才能标记完整")
            policy = Policy(
                source_id=old.source_id,
                bundle_id=bundle.id,
                draft=approval.draft.model_dump(mode="json"),
                status="reviewed",
                reviewer=approval.reviewer,
                review_note=approval.note,
                extractor=old.extractor,
            )
            session.add(policy)
            session.flush()
            changed = session.execute(
                update(Source)
                .where(
                    Source.id == source.id,
                    Source.latest_policy == policy_id,
                    Source.latest_bundle == bundle.id,
                )
                .values(latest_policy=policy.id)
            ).rowcount
            if changed != 1:
                raise Conflict("另一位复核者已更新政策，请刷新")
            return record(policy)

    def company(self, company_id):
        with self.store.session() as session:
            company = session.get(Company, company_id)
            if company is None:
                raise KeyError("company not found")
            version = session.scalar(
                select(CompanyVersion).where(
                    CompanyVersion.company_id == company_id, CompanyVersion.version == company.version
                )
            )
            return {**record(company), "facts": version.facts}

    def save_company(self, company_id, data: CompanyInput):
        with self.store.session() as session:
            company = session.get(Company, company_id)
            if company is None:
                if data.expected_version != 0:
                    raise Conflict("公司事实版本已变化")
                company = Company(id=company_id, name=data.name, version=1)
                session.add(company)
            else:
                changed = session.execute(
                    update(Company)
                    .where(Company.id == company_id, Company.version == data.expected_version)
                    .values(name=data.name, version=data.expected_version + 1)
                ).rowcount
                if changed != 1:
                    raise Conflict("公司事实版本已变化，请刷新再编辑")
            session.add(
                CompanyVersion(
                    company_id=company_id,
                    version=data.expected_version + 1,
                    facts={key: fact.model_dump(mode="json") for key, fact in data.facts.items()},
                )
            )
        return self.company(company_id)

    def worklist(self, company_id, *, as_of=None, save=False):
        as_of = as_of or now()
        company = self.company(company_id)
        facts = {key: Fact.model_validate(value) for key, value in company["facts"].items()}
        results = []
        with self.store.session() as session:
            for source in session.scalars(select(Source).order_by(Source.title)):
                if (source.kind == "demo") != (company_id == "demo"):
                    continue
                policy = session.get(Policy, source.latest_policy) if source.latest_policy else None
                draft = (
                    PolicyDraft.model_validate(policy.draft) if policy else PolicyDraft(title=source.title)
                )
                evidence_status = "complete"
                if source.health != "ok":
                    evidence_status = "来源未完成采集或存在失败：" + source.health
                elif policy is None or policy.status != "reviewed" or draft.completeness != "complete":
                    evidence_status = "条款尚未完整复核"
                elif source.kind == "policy" and (
                    not source.last_success
                    or as_of - datetime.fromisoformat(source.last_success)
                    > timedelta(hours=source.interval_hours * 2)
                ):
                    evidence_status = "来源超过复查周期，需重新抓取"
                result = evaluate(draft, facts, as_of, evidence_status=evidence_status)
                result.update(
                    source_id=source.id,
                    policy_id=policy.id if policy else None,
                    title=draft.title,
                    source_url=source.url,
                    source_kind=source.kind,
                    support_kind=draft.support_kind,
                    batch=draft.batch,
                    company_id=company_id,
                    facts_version=company["version"],
                    reviewer=policy.reviewer if policy else "",
                    bundle_id=source.latest_bundle,
                    source_error=source.error,
                )
                if save and policy:
                    key = digest(result)
                    if session.scalar(select(Evaluation.id).where(Evaluation.inputs_hash == key)) is None:
                        session.add(
                            Evaluation(
                                inputs_hash=key,
                                company_id=company_id,
                                source_id=source.id,
                                policy_id=policy.id,
                                facts_version=company["version"],
                                result=result,
                            )
                        )
                results.append(result)
        return {
            "company": {k: v for k, v in company.items() if k != "facts"},
            "as_of": as_of.isoformat(),
            "items": results,
        }

    def export(self, company_id):
        data = self.worklist(company_id, save=True)

        def cell(value):
            return html.escape(str(value)).replace("|", "&#124;").replace("\n", "<br>")

        lines = [
            f"# {cell(data['company']['name'])} · 算力政策行动清单",
            "",
            f"评估时间：{data['as_of']}；公司事实版本：{data['company']['version']}。",
            "",
            "可推进表示已复核规则与已确认事实相符，仍需准备材料并按主管部门要求申报。",
            "",
            "| 政策 / 来源 | 类型 | 判断 | 窗口 | 缺口 / 下一步 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in data["items"]:
            gaps = "；".join(gap["label"] + "：" + gap["reason"] for gap in item["gaps"])
            action = gaps or (
                "等待主管部门发布下一批次"
                if item["window_status"] == "closed"
                else "准备材料：" + "、".join(item["materials"])
            )
            lines.append(
                "| "
                + " | ".join(
                    map(
                        cell,
                        [
                            item["title"] + " / " + item["source_url"],
                            item["support_kind"],
                            item["decision"],
                            item["window_status"],
                            action,
                        ],
                    )
                )
                + " |"
            )
        lines += ["", "注：标有【演示】的记录为合成测试数据，不代表真实政策；所有记录均未提交申报。"]
        return "\n".join(lines) + "\n"
