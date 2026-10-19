import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from compute_policy.app import create_app
from compute_policy.contracts import PolicyDraft
from compute_policy.extract import extract
from compute_policy.jobs import Queue
from compute_policy.store import now


def test_queue_idempotency_lease_recovery_and_fencing(service):
    source = service.add_source("https://example.com/p", "test")
    queue = Queue(service)
    first = queue.enqueue("crawl", source["id"], request_key="a")
    assert queue.enqueue("crawl", source["id"], request_key="a")["id"] == first["id"]
    claim = queue.claim()
    assert queue.claim() is None
    recovered = queue.claim(at=now() + timedelta(minutes=31))
    assert recovered["id"] == claim["id"]
    assert recovered["attempts"] == 2
    assert queue.finish(claim) == 0
    assert queue.finish(recovered) == 1


def test_queue_three_failures_go_to_dead_letter(service):
    source = service.add_source("https://example.com/p", "test")
    queue = Queue(service)
    queue.enqueue("crawl", source["id"])
    for _ in range(3):
        job = queue.claim(at=now() + timedelta(hours=1))
        queue.finish(job, ValueError("blocked"))
    assert queue.list()[0]["state"] == "dead"


def test_workbench_api_and_cross_origin_block(demo):
    client = TestClient(create_app(demo.store))
    assert client.get("/").status_code == 200
    assert (
        client.get("/assets/app.js")
        .headers["content-type"]
        .startswith(("text/javascript", "application/javascript"))
    )
    work = client.get("/api/worklist/demo").json()
    assert len(work["items"]) == 4
    assert client.post("/api/sources", json={"url": "https://example.com/"}).status_code == 403
    assert (
        client.post(
            "/api/sources",
            headers={"X-Policy-Action": "1", "Origin": "https://evil.example"},
            json={"url": "https://example.com/"},
        ).status_code
        == 403
    )
    source = client.post(
        "/api/sources",
        headers={"X-Policy-Action": "1"},
        json={"url": "https://example.com/policy", "title": "测试"},
    )
    assert source.status_code == 200
    assert client.get("/api/jobs").json()[0]["state"] == "pending"
    detail = client.get("/api/sources/" + work["items"][0]["source_id"]).json()
    download = client.get("/api/evidence/" + detail["blocks"][0]["snapshot_id"] + "/download")
    assert download.headers["content-disposition"].startswith("attachment")
    assert "虚构" in download.text
    assert "原文" in client.get("/api/export/demo").text or "合成测试" in client.get("/api/export/demo").text


def test_model_adapter_validates_and_does_not_receive_company_facts(monkeypatch):
    monkeypatch.setenv("POLICY_MODEL_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("POLICY_MODEL_API_KEY", "not-a-real-key")
    monkeypatch.setenv("POLICY_MODEL_NAME", "test-model")
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": PolicyDraft(title="public policy").model_dump_json()},
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        draft, version = extract("test", [{"id": "a", "text": "公开政策文本"}], client=client)
    assert draft.title == "public policy"
    assert version.endswith("test-model")
    assert captured[0]["messages"][1]["content"] == json.dumps(
        [{"id": "a", "text": "公开政策文本"}], ensure_ascii=False
    )


def test_truncated_model_output_is_rejected(monkeypatch):
    for key, value in {
        "POLICY_MODEL_BASE_URL": "https://model.example/v1",
        "POLICY_MODEL_API_KEY": "test",
        "POLICY_MODEL_NAME": "test",
    }.items():
        monkeypatch.setenv(key, value)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
            )
        )
    ) as client:
        with pytest.raises(ValueError, match="未完整结束"):
            extract("test", [], client=client)
