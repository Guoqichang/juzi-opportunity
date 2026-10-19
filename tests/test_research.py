from datetime import UTC, datetime, timedelta

import pytest

from compute_policy.acquire import Resource, parse
from compute_policy.contracts import CompanyInput, Fact
from compute_policy.discovery import Discovery, canonical, classify, date_hints, priority_candidates
from compute_policy.jobs import Queue
from compute_policy.knowledge import Knowledge
from compute_policy.reports import Reports
from compute_policy.research_contracts import (
    FactAnswer,
    FormField,
    FormRequest,
    Hub,
    KnowledgeInput,
    QuestionInput,
    ResearchSettings,
    SettingsUpdate,
)
from compute_policy.service import Conflict
from compute_policy.store import DiscoveryRun


@pytest.fixture
def researcher(service):
    service.save_company("juzi", CompanyInput(name="测试企业", expected_version=0))
    return service


class PublicSites:
    calls = []

    def __init__(self, hosts):
        self.hosts = hosts

    def close(self):
        pass

    def fetch(self, url):
        self.calls.append(url)
        if url.endswith("/list/"):
            body = '<html><a href="/event.html">北京人工智能大赛报名启动</a><a href="/index_1.html">下一页</a></html>'
        elif url.endswith("index_1.html"):
            body = '<html><a href="/grant.html">海淀算力券申报通知</a></html>'
        else:
            body = (
                "<html><article><p>报名截至2026年10月1日，请核对全部申报条件。</p><p>"
                + "所有条款以主管部门原始通知为准。" * 10
                + "</p></article></html>"
            )
        return parse(Resource(url, body.encode(), "text/html"))


def research_settings(discovery):
    settings = ResearchSettings(
        queries=["人工智能 活动"],
        hubs=[Hub(url="https://policy.example/list/", label="test")],
        max_hub_pages=2,
        max_queries=1,
        max_collect=3,
    )
    config = discovery.config("juzi")
    discovery.save_config("juzi", SettingsUpdate(settings=settings, expected_version=config["version"]))
    return settings


def test_discovers_unseeded_urls_follows_pagination_and_keeps_provenance(researcher):
    discovery = Discovery(researcher)
    research_settings(discovery)

    def searcher(q):
        return [
            {
                "title": "北京人工智能活动报名",
                "href": "https://event.example/notice.html?utm_source=test",
                "body": "报名",
            }
        ]

    result = discovery.run("juzi", searcher=searcher, fetcher_factory=PublicSites)
    assert result["new_candidates"] == 3
    assert result["collected"] == 3
    assert len(result["hubs"]) == 2
    candidates = discovery.candidates("juzi")
    assert any(c["origins"][0]["type"] == "search" for c in candidates)
    assert any(c["origins"][0]["type"] == "hub" for c in candidates)
    assert all(c["source_id"] for c in candidates)
    assert all(item["decision"] == "待核实" for item in researcher.worklist("juzi")["items"])
    assert discovery.run("juzi", searcher=searcher, fetcher_factory=PublicSites)["new_candidates"] == 0


def test_failed_provider_is_reported_and_hub_still_runs(researcher):
    discovery = Discovery(researcher)
    research_settings(discovery)

    def broken(query):
        raise RuntimeError("provider unavailable")

    result = discovery.run("juzi", searcher=broken, fetcher_factory=PublicSites)
    assert result["collected"] == 2
    assert result["queries"][0]["state"] == "failed"
    assert discovery.runs("juzi")[0]["state"] == "partial"
    assert result["errors"][0]["stage"] == "search"


def test_query_budget_and_body_budget_are_bounded(researcher):
    discovery = Discovery(researcher)
    config = discovery.config("juzi")
    settings = ResearchSettings(
        queries=["a", "b", "c"], hubs=[], max_hub_pages=0, max_queries=2, max_collect=1
    )
    discovery.save_config("juzi", SettingsUpdate(settings=settings, expected_version=config["version"]))
    calls = []

    def search(query):
        calls.append(query)
        return [
            {"title": f"人工智能活动 {query} {i}", "href": f"https://p.example/{query}/{i}.html"}
            for i in range(50)
        ]

    result = discovery.run("juzi", searcher=search, fetcher_factory=PublicSites)
    assert len(calls) == 2
    assert result["new_candidates"] == 16
    assert result["collected"] == 1
    assert result["budget_deferred"] == 15


@pytest.mark.parametrize(
    "title,expected",
    [
        ("关于公布创业大赛推荐入围全国赛企业名单的通知", "result_notice"),
        ("关于公示拟申请人工智能券项目情况的通知", "result_notice"),
        ("人工智能创业大赛圆满收官", "recap"),
        ("人工智能创业大赛报名启动", "open_call"),
        ("人工智能团队亮相路演，完整名单公布", "recap"),
    ],
)
def test_results_and_recaps_are_not_open_calls(title, expected):
    assert classify(title)[1] == expected


def test_canonicalization_preserves_business_params():
    assert (
        canonical("https://p.example/x?utm_source=x&id=3&batch=2026#foo")
        == "https://p.example/x?id=3&batch=2026"
    )
    assert canonical("https://p.example/x?id=3") != canonical("https://p.example/x?id=4")


def test_dates_are_evidence_hints_not_verified_eligibility():
    hints = date_hints([{"id": "block1", "text": "报名截至2026年10月1日，请尽早提交。"}])
    assert hints[0]["date"] == "2026-10-01"
    assert hints[0]["kind"] == "possible_deadline"
    assert hints[0]["verified"] is False
    assert hints[0]["block_id"] == "block1"


def test_report_priority_keeps_unconfirmed_reposts_and_events_visible():
    rows = []
    for index, (title, category) in enumerate(
        [
            ("2026年中关村模型券申报指南", "compute_support"),
            ("2026年中关村模型券申报指南 - 转载网站", "compute_support"),
            ("企业智能体场景路演报名启动", "event"),
        ]
    ):
        rows.append(
            {
                "id": str(index),
                "title": title,
                "category": category,
                "document_kind": "open_call",
                "state": "collected",
                "deadline_hint_status": "unverified",
                "score": 100 - index * 30,
                "url": f"https://p.gov.cn/{index}",
            }
        )
    selected = priority_candidates(rows, ResearchSettings(), limit=2)
    assert [row["id"] for row in selected] == ["0", "2"]
    assert {row["id"] for row in priority_candidates(rows, ResearchSettings(), limit=8)} == {"0", "1", "2"}


@pytest.mark.parametrize(
    "left,right",
    [
        (
            "北京经济技术开发区信息技术产业局关于开展经开区2026年上半年“算力券”申报的通知",
            "北京经济技术开发区信息技术产业局关于开展经开区2025年下半年“算力券”申报的通知",
        ),
        ("2026年第一批人工智能算力券申报通知", "2026年第二批人工智能算力券申报通知"),
        ("2026年北京人工智能产业创新大赛报名启动", "2026年上海人工智能产业创新大赛报名启动"),
        ("2026年人工智能产业创新大赛报名启动", "2026年人工智能产业创新大赛报名启动"),
    ],
)
def test_reports_keep_distinct_sources_despite_similar_titles(researcher, left, right):
    discovery = Discovery(researcher)
    candidates = [
        discovery.observe("juzi", url, title, "", {}, ResearchSettings())[0]
        for url, title in [("https://a.example/notice", left), ("https://b.example/notice", right)]
    ]
    report = Reports(researcher).generate("juzi", "manual")
    assert {c["id"] for c in report["payload"]["priority"]} == {c["id"] for c in candidates}
    assert report["payload"]["priority_total"] == 2
    for c in candidates:
        assert c["url"] in report["markdown"]


@pytest.mark.parametrize("key", ["id", "batch", "ref", "from", "spm"])
def test_discovery_preserves_document_selectors_but_deduplicates_tracking(researcher, key):
    discovery = Discovery(researcher)
    settings = ResearchSettings()
    url = f"https://policy.example/apply?{key}=one"
    first, _ = discovery.observe("juzi", url, "人工智能算力券申报通知", "", {"type": "hub"}, settings)
    repeat, created = discovery.observe(
        "juzi", url + "&utm_source=search", "人工智能算力券申报通知", "", {"type": "search"}, settings
    )
    assert not created and repeat["id"] == first["id"]
    assert len(repeat["origins"]) == 2
    other, created = discovery.observe(
        "juzi", f"https://policy.example/apply?{key}=two", "人工智能算力券申报通知", "", {}, settings
    )
    assert created and other["id"] != first["id"]


def test_url_identity_preserves_repeated_parameter_order():
    assert canonical("https://p.example/x?id=one&id=two") != canonical("https://p.example/x?id=two&id=one")


def test_qa_preserves_identical_quotes_from_distinct_sources(researcher):
    discovery = Discovery(researcher)
    source_ids = set()
    for host in ["a.example", "b.example"]:
        candidate, _ = discovery.observe(
            "juzi", f"https://{host}/notice", "人工智能大赛报名启动", "", {}, ResearchSettings()
        )
        source = discovery.register_candidate("juzi", candidate["id"])
        source_ids.add(source["id"])
        researcher.crawl(source["id"], fetcher=PublicSites([]))
    response = Knowledge(researcher).ask("juzi", QuestionInput(question="人工智能大赛报名有什么要求？"))
    assert {c["source_id"] for c in response["citations"]} == source_ids
    assert len({c["quote"] for c in response["citations"]}) == 1
    assert len({c["source_ref"] for c in response["citations"]}) == 2
    assert {item["source_id"] for item in response["opportunities"]} == source_ids
    for item in response["opportunities"]:
        assert item["source_url"] in response["answer"]


def test_knowledge_revision_retirement_and_no_automatic_fact_promotion(researcher):
    kb = Knowledge(researcher)
    data = KnowledgeInput(
        key="intro",
        title="企业介绍",
        body="我们正在探索人工智能客服项目。",
        source_ref="内部草稿",
        expected_version=0,
    )
    first = kb.save("juzi", data)
    with pytest.raises(Conflict):
        kb.save("juzi", data)
    updated = data.model_copy(
        update={"expected_version": 1, "body": "项目介绍已修订。", "status": "verified"}
    )
    second = kb.save("juzi", updated)
    assert first["id"] != second["id"]
    assert len(kb.entries("juzi")) == 1
    assert len(kb.entries("juzi", history=True)) == 2
    assert researcher.company("juzi")["facts"] == {}
    kb.save("juzi", updated.model_copy(update={"expected_version": 2, "status": "retired"}))
    assert kb.entries("juzi") == []


def test_qa_quotes_knowledge_and_returns_followup_questions(researcher):
    kb = Knowledge(researcher)
    entry = kb.save(
        "juzi",
        KnowledgeInput(
            key="project",
            title="客服项目介绍",
            body="客服项目通过人工智能助手处理客户咨询。",
            source_ref="内部介绍记录",
            expected_version=0,
        ),
    )
    response = kb.ask("juzi", QuestionInput(question="客服项目有哪些资料？"))
    assert any(entry["id"] in c["citation"] for c in response["citations"])
    assert response["followup_questions"]
    assert response["mode"] == "local_evidence_retrieval"
    assert len(kb.conversation("juzi")) == 1
    unknown = kb.ask("juzi", QuestionInput(question="火星卫星公转周期？"))
    assert "没有足够依据" in unknown["answer"]
    assert unknown["citations"] == []


def test_fact_question_answer_is_versioned_and_estimate_is_preserved(researcher):
    kb = Knowledge(researcher)
    answer = FactAnswer(
        field="eligible_compute_spend_cny", fact=Fact(value=100000, status="estimate"), expected_version=1
    )
    kb.answer_fact("juzi", answer)
    assert researcher.company("juzi")["version"] == 2
    assert researcher.company("juzi")["facts"]["eligible_compute_spend_cny"]["status"] == "estimate"
    with pytest.raises(Conflict):
        kb.answer_fact("juzi", answer)


def test_event_recommendations_exclude_results_recaps_news_and_past_deadlines(researcher):
    discovery = Discovery(researcher)
    settings = ResearchSettings()
    titles = [
        "北京人工智能大赛报名启动",
        "北京人工智能大赛结果公示",
        "北京人工智能大赛圆满收官",
        "北京人工智能半导体新闻",
        "北京人工智能旧活动报名启动",
    ]
    ids = []
    for index, title in enumerate(titles):
        candidate, _ = discovery.observe(
            "juzi", f"https://policy.example/{index}.html", title, "", {}, settings
        )
        source = discovery.register_candidate("juzi", candidate["id"])
        ids.append(source["id"])
        researcher.crawl(source["id"], fetcher=PublicSites([]))
    from sqlalchemy import select

    from compute_policy.store import Candidate

    with researcher.store.session() as session:
        row = session.scalar(select(Candidate).where(Candidate.source_id == ids[-1]))
        row.hints = {"dates": [{"date": "2001-01-01", "kind": "possible_deadline"}]}
    response = Knowledge(researcher).ask("juzi", QuestionInput(question="有哪些人工智能活动值得报名？"))
    assert [item["source_id"] for item in response["opportunities"]] == [ids[0]]
    assert len(response["citations"]) <= 1
    historical = Knowledge(researcher).ask(
        "juzi", QuestionInput(question="查看这个大赛结果公示", source_id=ids[1])
    )
    assert historical["opportunities"][0]["source_id"] == ids[1]


def test_candidate_hints_follow_independent_crawl_success_and_failure(researcher):
    discovery = Discovery(researcher)
    candidate, _ = discovery.observe(
        "juzi", "https://policy.example/notice.html", "人工智能活动报名", "", {}, ResearchSettings()
    )
    source = discovery.register_candidate("juzi", candidate["id"])
    researcher.crawl(source["id"], fetcher=PublicSites([]))
    refreshed = discovery.candidates("juzi")[0]
    assert refreshed["state"] == "collected"
    assert refreshed["hints"]["dates"][0]["date"] == "2026-10-01"

    class Broken:
        def fetch(self, url):
            raise RuntimeError("site unavailable")

    with pytest.raises(RuntimeError):
        researcher.crawl(source["id"], fetcher=Broken())
    blocked = discovery.candidates("juzi")[0]
    assert blocked["state"] == "blocked"
    assert blocked["hints"]["error"] == "site unavailable"
    assert blocked["hints"]["dates"]  # old evidence survives, but is no longer reported as collected


def test_form_prefills_confirmed_valid_facts_only_and_detects_staleness(researcher):
    source = researcher.add_source("https://policy.example/form.html", "人工智能项目申报")
    researcher.save_company(
        "juzi",
        CompanyInput(
            name="测试企业",
            expected_version=1,
            facts={
                "legal_name": Fact(value="测试有限公司", status="confirmed", evidence="测试用营业执照"),
                "eligible_compute_spend_cny": Fact(value=20000, status="estimate"),
                "contact_phone": Fact(
                    value="test", status="confirmed", evidence="测试记录", valid_to="2000-01-01"
                ),
            },
        ),
    )
    kb = Knowledge(researcher)
    request = FormRequest(
        source_id=source["id"],
        fields=[
            FormField(field=k, label=k) for k in ["legal_name", "eligible_compute_spend_cny", "contact_phone"]
        ],
    )
    draft = kb.prepare_form("juzi", request)
    assert draft["payload"]["fields"][0]["value"] == "测试有限公司"
    assert all(f["value"] is None for f in draft["payload"]["fields"][1:])
    assert draft["payload"]["submission_state"] == "draft_only"
    assert not kb.forms("juzi")[0]["stale"]
    kb.answer_fact(
        "juzi",
        FactAnswer(
            field="project_name",
            fact=Fact(value="测试项目", status="confirmed", evidence="项目记录"),
            expected_version=2,
        ),
    )
    assert kb.forms("juzi")[0]["stale"]


def test_reports_scheduled_once_per_local_day_and_week(researcher):
    reports = Reports(researcher)
    friday_before = datetime(2026, 9, 25, 0, 59, tzinfo=UTC)
    friday_after = datetime(2026, 9, 25, 1, 0, tzinfo=UTC)
    assert reports.due("juzi", friday_before) == []
    assert {r["kind"] for r in reports.due("juzi", friday_after)} == {"daily", "weekly"}
    first = reports.generate("juzi", "daily", at=friday_after)
    assert reports.generate("juzi", "daily", at=friday_after + timedelta(minutes=30))["id"] == first["id"]
    assert "本期没有完成发现记录" in first["markdown"]
    assert first["payload"]["actionable"] == []


def test_report_captures_failure_instead_of_claiming_no_opportunity(researcher):
    with researcher.store.session() as session:
        session.add(
            DiscoveryRun(
                company_id="juzi",
                state="partial",
                summary={"errors": [{"stage": "search", "url": "query", "error": "blocked"}]},
            )
        )
    report = Reports(researcher).generate("juzi", "manual")
    assert report["payload"]["coverage"]["errors"] == 1
    assert "blocked" in report["markdown"]
    assert "不代表全网没有机会" in report["markdown"]


def test_discovery_schedule_and_queue_survive_reopen(researcher):
    from compute_policy.service import Service
    from compute_policy.store import Store

    discovery = Discovery(researcher)
    discovery.config("juzi")
    assert discovery.due() == ["juzi"]
    queue = Queue(researcher)
    first = queue.enqueue("discover", company_id="juzi", request_key="discover-test")
    reopened = Queue(Service(Store(researcher.store.path)))
    assert reopened.enqueue("discover", company_id="juzi", request_key="discover-test")["id"] == first["id"]
    assert reopened.claim()["stage"] == "discover"
    with pytest.raises(ValueError):
        queue.enqueue("report", company_id="demo")


def test_research_api_flows_and_stale_form_export(researcher):
    from fastapi.testclient import TestClient

    from compute_policy.app import create_app

    client = TestClient(create_app(researcher.store))
    headers = {"X-Policy-Action": "1"}
    assert client.get("/assets/features.js").status_code == 200
    assert client.get("/api/research/juzi").status_code == 200
    assert (
        client.post("/api/research/discover", json={"company_id": "juzi"}, headers=headers).json()["stage"]
        == "discover"
    )
    assert client.post("/api/qa/juzi", json={"question": "能申请什么？"}, headers=headers).status_code == 200
    report = client.post(
        "/api/reports/generate", json={"company_id": "juzi", "kind": "manual"}, headers=headers
    ).json()
    assert client.get(f"/api/reports/juzi/{report['id']}/download").status_code == 200
    candidate, _ = Discovery(researcher).observe(
        "juzi", "https://policy.example/apply.html", "人工智能项目申报启动", "", {}, ResearchSettings()
    )
    collected = client.post(f"/api/research/juzi/candidates/{candidate['id']}/collect", headers=headers)
    assert collected.status_code == 200
    source_id = Discovery(researcher).candidates("juzi")[0]["source_id"]
    draft = client.post("/api/forms/juzi", json={"source_id": source_id}, headers=headers).json()
    export_path = f"/api/forms/juzi/{draft['id']}/download"
    assert client.get(export_path).status_code == 200
    assert all(field["value"] is None for field in draft["payload"]["fields"])
    questions = client.get("/api/questions/juzi").json()
    assert {"project_name", "project_summary", "contact_name", "contact_phone"}.issubset(
        {question["field"] for question in questions}
    )
    assert (
        client.post(
            "/api/answers/juzi",
            json={
                "field": "legal_name",
                "expected_version": 1,
                "fact": {"value": "测试法人", "status": "estimate"},
            },
            headers=headers,
        ).status_code
        == 200
    )
    assert client.get(export_path).status_code == 409
    assert (
        client.post(
            "/api/knowledge/juzi", json={}, headers={"Origin": "https://evil.example", "X-Policy-Action": "1"}
        ).status_code
        == 403
    )
