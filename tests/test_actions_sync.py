from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from compute_policy.acquire import Resource, parse
from compute_policy.actions import Actions, FollowupInput, RelationInput, crawl_due
from compute_policy.agent_research import AgentResearch
from compute_policy.app import create_app
from compute_policy.base_sync import SCHEMAS, BaseSync, LarkCLI, fact_fields, parse_fact
from compute_policy.contracts import CompanyInput
from compute_policy.discovery import Discovery
from compute_policy.research_contracts import Hub, ResearchSettings, SettingsUpdate
from compute_policy.service import Conflict
from compute_policy.store import now, stamp


@pytest.fixture
def project(service):
    service.save_company("juzi", CompanyInput(name="测试企业", expected_version=0))
    d = Discovery(service)
    d.config("juzi")
    c, _ = d.observe(
        "juzi",
        "https://policy.example/2026",
        "2026年上半年北京算力券申报通知",
        "",
        {"type": "test"},
        ResearchSettings(),
    )
    return service, c


class FakeBase:
    def __init__(self):
        self.data = {table: {} for table in SCHEMAS}
        self.writes = []

    def rows(self, table, fields):
        return deepcopy(self.data[table])

    def create(self, table, rows):
        key = SCHEMAS[table][1]
        for row in rows:
            assert row[key] not in self.data[table]
            self.data[table][row[key]] = {"id": "rec_" + str(len(self.data[table])), "fields": deepcopy(row)}
        self.writes.append(("create", table, len(rows)))

    def update(self, table, record_id, fields):
        if not fields:
            return
        next(r for r in self.data[table].values() if r["id"] == record_id)["fields"].update(fields)
        self.writes.append(("update", table, fields))


def sync_for(service):
    client = FakeBase()
    cfg = {"base_token": "test", "company_id": "juzi", "site_url": "https://site.example", "tables": {}}
    return BaseSync(service, cfg, client), client


def test_two_way_followup_is_idempotent_and_preserves_conflicts(project):
    service, c = project
    bridge, client = sync_for(service)
    assert bridge.table("opportunities")["created"] == 1
    assert bridge.table("opportunities")["updated"] == 0
    remote = client.data["opportunities"][c["id"]]["fields"]
    remote["负责人"] = "飞书负责人"
    assert bridge.table("opportunities")["imported"] == 1
    saved = Actions(service).feedback("juzi")[c["id"]]
    assert saved["owner"] == "飞书负责人"
    Actions(service).save_feedback(
        "juzi", c["id"], FollowupInput(owner="工作台负责人", expected_version=saved["version"])
    )
    remote["负责人"] = "另一位负责人"
    result = bridge.table("opportunities")
    assert len(result["conflicts"]) == 1
    assert remote["负责人"] == "另一位负责人"
    assert Actions(service).feedback("juzi")[c["id"]]["owner"] == "工作台负责人"
    assert bridge.table("opportunities")["conflicts"] == result["conflicts"]


def test_confirmed_facts_require_evidence_and_estimates_stay_estimates(project):
    service, _ = project
    bridge, client = sync_for(service)
    assert bridge.table("facts")["created"] >= 9
    remote = client.data["facts"]["registered_district"]["fields"]
    remote.update({"资料值": "海淀", "确认状态": "已确认", "依据说明": ""})
    assert bridge.table("facts")["errors"]
    assert not service.company("juzi")["facts"]
    remote.update({"确认状态": "估算", "依据说明": "尚未核验"})
    assert bridge.table("facts")["imported"] == 1
    assert service.company("juzi")["facts"]["registered_district"]["status"] == "estimate"
    with pytest.raises(ValueError):
        parse_fact({**fact_fields({}), "资料值": "probably"}, "sme_qualified")


def test_feedback_version_rejects_stale_write_and_changes_actions(project):
    service, c = project
    assert Actions(service).plan("juzi")
    Actions(service).save_feedback("juzi", c["id"], FollowupInput(status="不适合", expected_version=0))
    assert not Actions(service).plan("juzi")
    with pytest.raises(Conflict):
        Actions(service).save_feedback("juzi", c["id"], FollowupInput(expected_version=0))
    response = TestClient(create_app(service.store)).get("/api/actions/juzi")
    assert response.status_code == 200
    assert response.json()["actions"] == []


def test_different_batches_cannot_be_confirmed_as_same(project):
    service, c = project
    other, _ = Discovery(service).observe(
        "juzi",
        "https://policy.example/2025",
        "2025年下半年北京算力券申报通知",
        "",
        {"type": "test"},
        ResearchSettings(),
    )
    with pytest.raises(ValueError, match="年份或批次"):
        Actions(service).save_relation(
            "juzi",
            RelationInput(
                left_id=c["id"],
                right_id=other["id"],
                relation="同批次转载",
                evidence="标题相似",
                expected_version=0,
            ),
        )
    row = Actions(service).save_relation(
        "juzi",
        RelationInput(
            left_id=c["id"],
            right_id=other["id"],
            relation="不同事项",
            evidence="两个通知年份和半年不同",
            expected_version=0,
        ),
    )
    assert row["relation"] == "不同事项"
    assert len(Discovery(service).candidates("juzi")) == 2


def test_cli_paginates_and_refuses_duplicate_business_keys(monkeypatch):
    client = LarkCLI({"base_token": "test", "tables": {"opportunities": "real-table"}})
    calls = []

    def call(command, table, **flags):
        if command == "field-list":
            return {"fields": [{"name": "线索ID", "type": "text"}]}
        calls.append(flags["offset"])
        return {
            "fields": ["线索ID"],
            "record_id_list": ["r" + str(flags["offset"])],
            "data": [["same"]],
            "has_more": flags["offset"] == 0,
        }

    monkeypatch.setattr(client, "call", call)
    with pytest.raises(ValueError, match="重复"):
        client.rows("opportunities", ["线索ID"])
    assert calls == [0, 1]


def test_browser_hub_fallback_is_remembered(project):
    service, _ = project
    d = Discovery(service)
    cfg = d.config("juzi")
    settings = ResearchSettings(
        search_enabled=False,
        hubs=[Hub(url="https://policy.example/list/", label="动态栏目")],
        max_hub_pages=1,
        max_collect=1,
    )
    d.save_config("juzi", SettingsUpdate(settings=settings, expected_version=cfg["version"]))
    http_calls = []

    class Fetcher:
        def __init__(self, hosts):
            pass

        def close(self):
            pass

        def fetch(self, url):
            http_calls.append(url)
            return parse(
                Resource(url, ("<html><p>" + "政策正文测试内容。" * 30 + "</p></html>").encode(), "text/html")
            )

    rendered = []

    def renderer(url):
        rendered.append(url)
        return parse(
            Resource(
                url, '<html><a href="/new">北京人工智能创业大赛报名启动</a></html>'.encode(), "text/html"
            )
        )

    assert d.run("juzi", fetcher_factory=Fetcher, renderer=renderer)["hubs"][0]["method"] == "browser"
    assert d.run("juzi", fetcher_factory=Fetcher, renderer=renderer)["hubs"][0]["method"] == "browser"
    assert http_calls.count("https://policy.example/list/") == 1
    assert len(rendered) == 2


def test_budget_exhaustion_preserves_last_research_receipt(project, monkeypatch):
    service, _ = project
    source = service.add_source("https://policy.example/p", "北京算力券申报")
    agent = AgentResearch(service)
    saved = {
        "state": "recovered",
        "started_at": stamp(),
        "finished_at": stamp(),
        "date": now().date().isoformat(),
    }
    agent.save(source["id"], saved)
    monkeypatch.setattr("compute_policy.agent_research.configured", lambda: True)
    monkeypatch.setattr(agent, "reserve", lambda: False)
    assert agent.run(source["id"])["state"] == "budget_exhausted"
    assert agent.status(source["id"]) == saved
    assert agent.history(source["id"])
    assert not agent.due(source["id"])
    assert not crawl_due(
        {
            "last_checked": (now() - timedelta(days=2)).isoformat(),
            "interval_hours": 24,
            "health": "failed",
            "error": "robots disallows",
        }
    )


def test_new_base_knowledge_is_versioned_without_confirming_company_facts(project):
    service, _ = project
    bridge, client = sync_for(service)
    client.data["knowledge"]["product-notes"] = {
        "id": "rec_knowledge",
        "fields": {
            "资料键": "product-notes",
            "资料标题": "产品介绍",
            "资料正文": "句子互动的产品参考资料，具体能力待核验。",
            "资料来源": "内部产品说明",
            "资料状态": "参考",
        },
    }
    result = bridge.table("knowledge")
    assert result["imported"] == 1
    from compute_policy.knowledge import Knowledge

    entries = Knowledge(service).entries("juzi")
    assert entries[0]["version"] == 1
    assert entries[0]["status"] == "reference"
    assert service.company("juzi")["facts"] == {}
    assert bridge.table("knowledge")["imported"] == 0
    client.data["knowledge"]["product-notes"]["fields"]["资料状态"] = "停用"
    assert bridge.table("knowledge")["imported"] == 1
    assert Knowledge(service).entries("juzi") == []
    client.data["knowledge"]["product-notes"]["fields"]["资料状态"] = "参考"
    assert bridge.table("knowledge")["imported"] == 1
    assert Knowledge(service).entries("juzi")[0]["version"] == 3


def test_cli_payload_file_is_relative_to_working_directory(monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    captured = []

    def run(args, **kwargs):
        payload = args[args.index("--json") + 1]
        assert payload.startswith("@") and not Path(payload[1:]).is_absolute()
        path = Path(kwargs["cwd"]) / payload[1:]
        captured.append(path)
        assert json.loads(path.read_text()) == {"Name": "内容"}
        return SimpleNamespace(returncode=0, stdout='{"ok":true,"data":{}}')

    monkeypatch.setattr("compute_policy.base_sync.subprocess.run", run)
    client = LarkCLI({"base_token": "test", "tables": {"knowledge": "table"}})
    client.update("knowledge", "record", {"Name": "内容"})
    assert not captured[0].exists()


def test_cli_normalizes_select_arrays_and_url_markdown(monkeypatch):
    client = LarkCLI({"base_token": "test", "tables": {"opportunities": "table"}})
    fields = ["线索ID", "跟进状态", "原文链接"]

    def call(command, table, **flags):
        if command == "field-list":
            return {
                "fields": [
                    {"name": "线索ID", "type": "text"},
                    {"name": "跟进状态", "type": "select", "multiple": False},
                    {"name": "原文链接", "type": "text", "style": {"type": "url"}},
                ]
            }
        return {
            "fields": fields,
            "data": [["key", ["待核实"], "[公开原文](https://policy.example/a)"]],
            "record_id_list": ["record"],
            "has_more": False,
        }

    monkeypatch.setattr(client, "call", call)
    result = client.rows("opportunities", fields)["key"]["fields"]
    assert result["跟进状态"] == "待核实"
    assert result["原文链接"] == "https://policy.example/a"
