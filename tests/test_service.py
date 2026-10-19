from datetime import timedelta

import pytest
from sqlalchemy import select

from compute_policy.acquire import Resource, parse
from compute_policy.contracts import Approval, CompanyInput, Fact, PolicyDraft
from compute_policy.service import Conflict
from compute_policy.store import Evaluation, Source, now


def row(service, title="可推进示例", company="demo", **kwargs):
    return next(i for i in service.worklist(company, **kwargs)["items"] if title in i["title"])


def test_demo_and_real_company_are_isolated(demo):
    assert row(demo)["decision"] == "可推进"
    assert row(demo, "支出证据缺口")["decision"] == "待核实"
    assert row(demo, "已截止")["decision"] == "等待新批次"
    assert row(demo, "地区不符")["decision"] == "当前不适用"
    assert demo.worklist("juzi")["items"] == []
    assert all(fact["status"] == "unknown" for fact in demo.company("juzi")["facts"].values())


def test_company_update_reevaluates_and_keeps_audit(demo):
    before = row(demo, save=True)
    company = demo.company("demo")
    company["facts"]["registered_district"] = Fact(
        value="朝阳区", status="confirmed", evidence="new test licence"
    )
    data = CompanyInput(name=company["name"], expected_version=company["version"], facts=company["facts"])
    demo.save_company("demo", data)
    assert row(demo)["decision"] == "当前不适用"
    assert before["facts_version"] == 1
    with pytest.raises(Conflict):
        demo.save_company("demo", data)
    with demo.store.session() as session:
        assert (
            session.scalar(select(Evaluation).where(Evaluation.source_id == before["source_id"])) is not None
        )


def test_changed_source_invalidates_review_and_old_review_is_rejected(demo):
    current = row(demo)
    detail = demo.detail(current["source_id"])
    resource = parse(
        Resource(detail["source"]["url"], "修改后的新政策版本；资格与原来不同。".encode(), "text/plain")
    )
    result = demo.ingest(current["source_id"], [resource])
    assert result["changed"]
    assert row(demo)["decision"] == "待核实"
    with pytest.raises(Conflict):
        demo.approve(
            detail["policy"]["id"],
            Approval(
                draft=PolicyDraft.model_validate(detail["policy"]["draft"]),
                expected_bundle_hash=detail["bundle"]["content_hash"],
                reviewer="tester",
                note="stale",
            ),
        )
    assert len(demo.detail(current["source_id"])["history"]) == 3


def test_same_url_attachment_change_and_idempotent_crawl(service):
    source = service.add_source("https://policy.example/main.html", "test")

    class FakeFetcher:
        revision = 1

        def fetch(self, url):
            if url.endswith(".txt"):
                return parse(
                    Resource(url, f"附件正文第{self.revision}版，包含真实资格要求。".encode(), "text/plain")
                )
            return parse(
                Resource(
                    url,
                    (
                        "<html><article><p>"
                        + "政策正文用于验证。" * 20
                        + '</p><a href="/attachment.txt">附件</a></article></html>'
                    ).encode(),
                    "text/html",
                )
            )

    fetcher = FakeFetcher()
    first = service.crawl(source["id"], fetcher=fetcher)
    assert not service.crawl(source["id"], fetcher=fetcher)["changed"]
    fetcher.revision = 2
    updated = service.crawl(source["id"], fetcher=fetcher)
    assert updated["bundle_hash"] != first["bundle_hash"]
    assert updated["changed"]


def test_forged_quote_and_missing_attachment_cannot_be_reviewed_complete(demo):
    detail = demo.detail(row(demo)["source_id"])
    draft = PolicyDraft.model_validate(detail["policy"]["draft"])
    draft.eligibility.children[0].evidence[0].quote = "根本不存在的文本内容"
    with pytest.raises(ValueError, match="引文不匹配"):
        demo.approve(
            detail["policy"]["id"],
            Approval(
                draft=draft,
                expected_bundle_hash=detail["bundle"]["content_hash"],
                reviewer="tester",
                note="test",
            ),
        )
    source = demo.add_source("https://policy.example/partial.txt", "partial")
    data = demo.ingest(
        source["id"],
        [parse(Resource(source["url"], "这是一段原文政策材料".encode(), "text/plain"))],
        [{"url": "https://policy.example/missing.pdf", "error": "HTTP 403"}],
    )
    with pytest.raises(ValueError, match="缺失附件"):
        demo.approve(
            data["policy_id"],
            Approval(
                draft=PolicyDraft(title="partial", completeness="complete"),
                expected_bundle_hash=data["bundle_hash"],
                reviewer="tester",
                note="test",
            ),
        )


def test_failed_refresh_never_keeps_actionable_status(demo):
    current = row(demo)
    with demo.store.session() as session:
        session.get(Source, current["source_id"]).health = "failed"
    assert row(demo)["decision"] == "待核实"


def test_clock_expiry_and_source_staleness_rechecked_without_new_job(demo):
    current = row(demo)
    assert row(demo, as_of=now() + timedelta(days=30))["decision"] == "等待新批次"
    with demo.store.session() as session:
        source = session.get(Source, current["source_id"])
        source.kind = "policy"
        source.last_success = (now() - timedelta(days=3)).isoformat()
    company = demo.company("demo")
    demo.save_company("juzi", CompanyInput(name="测试用档案", expected_version=1, facts=company["facts"]))
    result = row(demo, company="juzi")
    assert result["decision"] == "待核实"
    assert "超过复查周期" in result["evidence_status"]


def test_no_model_returns_draft_only(demo):
    current = row(demo)
    draft = demo.extract(current["source_id"])
    assert draft["status"] == "draft"
    assert row(demo)["decision"] == "待核实"


def test_export_contains_evidence_url_and_no_claim_of_submission(demo):
    text = demo.export("demo")
    assert "https://demo.invalid/" in text
    assert "所有记录均未提交申报" in text
    assert "合成测试数据" in text
