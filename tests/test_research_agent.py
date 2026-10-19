import base64
import json
import time
from dataclasses import asdict

import httpx
import pytest
from test_acquire import resolver

from compute_policy.acquire import AcquisitionError, Fetcher, Resource, parse
from compute_policy.agent_research import AgentResearch, RecoveredFetcher
from compute_policy.contracts import CompanyInput
from compute_policy.discovery import Discovery, classify, priority_candidates
from compute_policy.research_contracts import ResearchSettings
from compute_policy.research_tools import claim_tool, execute, session_directory
from compute_policy.store import digest


def test_robots_redirect_is_followed_without_widening_document_allowlist():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "policy.example" and request.url.path == "/robots.txt":
            return httpx.Response(302, headers={"location": "https://cdn.example/robots.txt"})
        if request.url.host == "cdn.example":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        return httpx.Response(
            200, text="<html><p>Policy public page</p></html>", headers={"content-type": "text/html"}
        )

    fetcher = Fetcher(["policy.example"], transport=httpx.MockTransport(handler), resolver=resolver, delay=0)
    assert fetcher.fetch("https://policy.example/public").blocks
    with pytest.raises(AcquisitionError, match="disallows"):
        fetcher.fetch("https://policy.example/private")
    with pytest.raises(AcquisitionError, match="allowlist"):
        fetcher.fetch("https://cdn.example/unrelated")
    assert "https://cdn.example/unrelated" not in calls


def test_retry_temporary_http_failure_but_not_access_denial(monkeypatch):
    monkeypatch.setattr("compute_policy.acquire.time.sleep", lambda _: None)
    statuses = iter([503, 200, 403])
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(next(statuses), text="response")

    fetcher = Fetcher(["p.example"], transport=httpx.MockTransport(handler), resolver=resolver, delay=0)
    assert fetcher._request("https://p.example/")[0] == 200
    assert fetcher._request("https://p.example/")[0] == 403
    assert len(calls) == 3


def test_cross_host_attachment_requires_explicit_link_and_public_dns():
    parent = Resource(
        "https://p.example/notice",
        b"",
        "text/html",
        links=[{"url": "https://files.example/form.txt", "attachment": True}],
    )
    fetcher = Fetcher(
        ["p.example"],
        resolver=resolver,
        delay=0,
        transport=httpx.MockTransport(
            lambda r: (
                httpx.Response(404)
                if r.url.path == "/robots.txt"
                else httpx.Response(200, text="Application form", headers={"content-type": "text/plain"})
            )
        ),
    )
    assert fetcher.fetch_linked("https://files.example/form.txt", parent).blocks
    with pytest.raises(AcquisitionError, match="not linked"):
        fetcher.fetch_linked("https://files.example/other.txt", parent)
    assert fetcher.hosts == {"p.example"}


def test_marketing_download_link_is_not_a_policy_attachment():
    resource = parse(
        Resource(
            "https://p.example/notice",
            '<html><a href="https://wx.example/group/abc">下载APP查看更多</a><a href="/file.pdf">附件</a></html>'.encode(),
            "text/html",
        )
    )
    assert [r["attachment"] for r in resource.links] == [False, True]


def test_retry_connection_closed_without_response(monkeypatch):
    monkeypatch.setattr("compute_policy.acquire.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response")
        return httpx.Response(200, text="public body")

    fetcher = Fetcher(["p.example"], transport=httpx.MockTransport(handler), resolver=resolver, delay=0)
    assert fetcher._request("https://p.example/notice")[0] == 200
    assert len(calls) == 2


def test_official_expired_title_survives_stale_search_snippet(service, tmp_path):
    service.save_company("juzi", CompanyInput(name="测试公司", expected_version=0))
    discovery = Discovery(service)
    original = service.add_source("https://old.example/notice", "2026年模型券申报指南")
    official_url = "https://official.example/2026"
    title = "（失效）2026年中关村科学城模型券政策申报指南"
    body = (
        f'<html><head><title>北京市海淀区人民政府</title><meta name="ArticleTitle" content="{title}">'
        "</head><body><article><p>" + "企业按照本批次申报要求提交材料。" * 15 + "</p></article></body></html>"
    ).encode()
    assert parse(Resource(official_url, body, "text/html")).title == title
    receipt_file(tmp_path, official_url, body)
    result = {"state": "running", "alternatives": []}
    AgentResearch(service).apply_receipts(original, tmp_path, result)
    assert len(result["alternatives"]) == 1
    discovery.observe("juzi", official_url, "2026年模型券申报指南", "", {}, ResearchSettings())
    candidates = discovery.candidates("juzi")
    candidate = next(c for c in candidates if c["url"] == official_url)
    assert candidate["title"] == title
    assert candidate["document_kind"] == "expired_notice"
    assert candidate not in priority_candidates(candidates, ResearchSettings())
    assert classify("关于2026年模型券政策申报指南的政策解读")[1] == "information"


def test_normal_crawl_keeps_official_withdrawal_marker(service):
    service.save_company("juzi", CompanyInput(name="测试公司", expected_version=0))
    discovery = Discovery(service)
    candidate, _ = discovery.observe(
        "juzi", "https://p.example/2026", "2026年模型券申报", "", {}, ResearchSettings()
    )
    source = discovery.register_candidate("juzi", candidate["id"])
    main = parse(
        Resource(
            source["url"],
            (
                '<html><meta name="ArticleTitle" content="（失效）2026年模型券申报"><p>'
                + "公开正文" * 50
                + "</p></html>"
            ).encode(),
            "text/html",
        )
    )
    fetcher = RecoveredFetcher(source, main)
    try:
        service.crawl(source["id"], fetcher=fetcher)
    finally:
        fetcher.close()
    updated = discovery.candidates("juzi")[0]
    assert updated["document_kind"] == "expired_notice"
    assert updated["hints"]["document_title"].startswith("（失效）")


def test_public_tool_has_hard_session_budget_and_rejects_unauthorized_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("POLICY_RESEARCH_RESULTS_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="invalid"):
        session_directory("../../etc")
    with pytest.raises(ValueError, match="not authorized"):
        session_directory("ses_missing123")
    directory = tmp_path / "ses_testing123"
    directory.mkdir()
    (directory / "request.json").write_text(json.dumps({"expires_at": time.time() + 60, "max_tools": 1}))
    assert claim_tool(directory) == 1
    with pytest.raises(ValueError, match="budget"):
        claim_tool(directory)
    (directory / "request.json").write_text(json.dumps({"expires_at": 0, "max_tools": 6}))
    with pytest.raises(ValueError, match="time budget"):
        execute("ses_testing123", "fetch", "https://example.com")


def test_daily_agent_budget_is_persistent(service, monkeypatch):
    monkeypatch.setenv("POLICY_RESEARCH_DAILY_LIMIT", "2")
    assert AgentResearch(service).reserve()
    assert AgentResearch(service).reserve()
    assert not AgentResearch(service).reserve()


def receipt_file(directory, url, body):
    resource = parse(Resource(url, body, "text/html"))
    data = asdict(resource)
    data["body"] = base64.b64encode(body).decode()
    (directory / "resource-1.json").write_text(
        json.dumps({"requested_url": url, "method": "fetch", "sha256": digest(body), "resource": data})
    )


def test_alternative_is_new_source_never_merged_into_failed_original(service, tmp_path):
    service.save_company("juzi", CompanyInput(name="测试公司", expected_version=0))
    discovery = Discovery(service)
    c, _ = discovery.observe(
        "juzi", "https://old.example/notice", "2026年算力券申报", "", {}, ResearchSettings()
    )
    original = discovery.register_candidate("juzi", c["id"])
    body = (
        "<html><title>2025年算力券申报通知</title><article><p>"
        + "企业应按照本批次申报要求提交材料。" * 15
        + "</p></article></html>"
    ).encode()
    receipt_file(tmp_path, "https://official.example/2025", body)
    result = {"state": "running", "alternatives": []}
    AgentResearch(service).apply_receipts(original, tmp_path, result)
    assert len(result["alternatives"]) == 1
    assert result["alternatives"][0]["source_id"] != original["id"]
    assert service.detail(original["id"])["bundle"] is None
    alternative = next(x for x in discovery.candidates("juzi") if x["url"] == "https://official.example/2025")
    assert alternative["hints"]["alternative_for"]["relation"] == "unverified"
    assert service.detail(alternative["source_id"])["blocks"]


def test_fabricated_or_corrupt_receipt_is_not_imported(service, tmp_path):
    source = service.add_source("https://p.example/notice", "算力券通知")
    receipt_file(tmp_path, source["url"], ("<html><p>" + "正文内容" * 100 + "</p></html>").encode())
    path = tmp_path / "resource-1.json"
    data = json.loads(path.read_text())
    data["sha256"] = "invalid"
    path.write_text(json.dumps(data))
    result = {"state": "running", "alternatives": []}
    AgentResearch(service).apply_receipts(source, tmp_path, result)
    assert service.detail(source["id"])["bundle"] is None


def test_model_has_only_bounded_public_tools():
    from pathlib import Path

    config = json.loads((Path(__file__).parents[1] / "deploy/opencode/opencode.json").read_text())
    assert config["permission"]["*"] == "deny"
    assert {k for k, v in config["permission"].items() if v == "allow"} == {
        "policy_search",
        "policy_fetch",
        "policy_render",
    }
    assert config["agent"]["researcher"]["steps"] == 6
