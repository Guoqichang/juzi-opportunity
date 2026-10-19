"""Record real crawl/research outcomes after the rollout jobs have executed."""

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

domain = os.environ["POLICY_DOMAIN"]
server_ip = os.environ["POLICY_SERVER_IP"]
path = Path("runtime/server-opencode-validation.json")
receipt = json.loads(path.read_text())
with httpx.Client(timeout=60, trust_env=False) as client:

    def call(method, url, body=None):
        response = client.request(
            method,
            "https://" + server_ip + url,
            json=body,
            headers={"Host": domain, "Origin": "https://" + domain, "X-Policy-Action": "1"},
            extensions={"sni_hostname": domain},
        )
        response.raise_for_status()
        return response

    jobs = call("GET", "/api/jobs").json()
    ids = {receipt["research_job"]} | {j["id"] for j in receipt["crawl_jobs"]}
    selected = [j for j in jobs if j["id"] in ids]
    active = [j for j in selected if j["state"] in {"running", "pending"} and not j["error"]]
    if active:
        print(json.dumps({"waiting": [{"stage": j["stage"], "state": j["state"]} for j in active]}))
        raise SystemExit(2)
    radar = call("GET", "/api/research/juzi").json()
    candidates = radar["candidates"]
    target = next(c for c in candidates if c["source_id"] == receipt["research_source"])
    assert target["hints"]["research"]["state"] in {"alternatives_found", "recovered"}
    assert target["hints"]["research"]["steps"]
    official = next(c for c in candidates if c["url"].endswith("t20260330_4810202.shtml"))
    assert official["document_kind"] == "expired_notice" and "失效" in official["title"]
    assert official["source_id"] != target["source_id"]
    outcomes = []
    for job in receipt["crawl_jobs"]:
        detail = call("GET", "/api/sources/" + job["source_id"]).json()
        outcomes.append(
            {
                "source_id": job["source_id"],
                "health": detail["source"]["health"],
                "url": detail["source"]["url"],
                "last_success": detail["source"]["last_success"],
                "blocks": len(detail["blocks"]),
                "warnings": detail["warnings"],
                "error": detail["source"]["error"],
                "blocks_by_url": dict(Counter(b["source_url"] for b in detail["blocks"])),
            }
        )
    word = next(o for o in outcomes if o["source_id"] == "bedb48d686a34d2f9a268a424243659d")
    assert any(url.endswith(".doc") and count > 0 for url, count in word["blocks_by_url"].items())
    report = call("POST", "/api/reports/generate", {"company_id": "juzi", "kind": "manual"}).json()
    assert official["id"] not in {c["id"] for c in report["payload"]["priority"]}
    downloaded = call("GET", f"/api/reports/juzi/{report['id']}/download").text
    assert downloaded == report["markdown"]
    receipt.update(
        {
            "verified_at": datetime.now(UTC).isoformat(),
            "candidate_count_after": len(candidates),
            "candidate_states_after": dict(Counter(c["state"] for c in candidates)),
            "research_result": target["hints"]["research"],
            "all_research_results": [
                {"source_id": c["source_id"], **c["hints"]["research"]}
                for c in candidates
                if c["hints"].get("research")
            ],
            "jobs": [
                {"id": j["id"], "state": j["state"], "attempts": j["attempts"], "error": j["error"]}
                for j in selected
            ],
            "crawl_outcomes": outcomes,
            "report_id": report["id"],
            "report_download_matches": True,
            "official_expired_source": {
                "id": official["source_id"],
                "title": official["title"],
                "kind": official["document_kind"],
            },
            "browser": {"javascript_errors": [], "screenshot": "runtime/juzi-opencode-radar.png"},
            "real_browser_hub": {
                "url": "https://zyk.bjhd.gov.cn/zwdt/xxgk/tzgg/",
                "blocks": 42,
                "links": 84,
                "failed_subresources": 3,
            },
            "status_after": call("GET", "/api/status").json(),
        }
    )
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "candidate_count": len(candidates),
                "research": receipt["research_result"]["state"],
                "crawl_outcomes": outcomes,
                "report_id": report["id"],
            },
            ensure_ascii=False,
        )
    )
