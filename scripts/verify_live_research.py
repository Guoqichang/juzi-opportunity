"""Public HTTP rollout verification; intentionally creates bounded crawl jobs."""

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

domain = os.environ["POLICY_DOMAIN"]
server_ip = os.environ["POLICY_SERVER_IP"]
client = httpx.Client(timeout=40, trust_env=False)


def call(method, path, payload=None):
    response = client.request(
        method,
        "https://" + server_ip + path,
        headers={"Host": domain, "Origin": "https://" + domain, "X-Policy-Action": "1"},
        extensions={"sni_hostname": domain},
        json=payload,
    )
    response.raise_for_status()
    return response


status = call("GET", "/api/status").json()
assert status["research_agent"]["configured"] and status["worker"]["running"]
assert "深入查找原文" in call("GET", "/assets/features.js").text
radar = call("GET", "/api/research/juzi").json()
assert radar["identity_policy"] == "separate-source-urls-v1"
candidates = radar["candidates"]
assert len(candidates) >= 74
target = next(c for c in candidates if c["source_id"] == "9c1e43034a304905ba09ee733d3449aa")
research_job = call("POST", f"/api/research/juzi/candidates/{target['id']}/investigate", {}).json()
sources = [
    "bedb48d686a34d2f9a268a424243659d",  # Cross-site Word attachment.
    "9ff29bbcb1b04fd38f9622e2a191818e",  # Cross-site DOCX attachment.
    "0ae9e5a131244b3397a653717372324d",  # Previously unreachable IPv6 route.
    "c90708447e1d4104b7cb2bff99c46406",  # Word form.
    "1555476ce73643229a3c478930178e70",  # Word form, independent 2026 batch.
    "ee89041e22bb453abde42ab841dbb87e",  # App link falsely treated as attachment.
]
jobs = [call("POST", "/api/jobs", {"stage": "crawl", "source_id": source_id}).json() for source_id in sources]
receipt = {
    "at": datetime.now(UTC).isoformat(),
    "status": status,
    "candidate_count_before": len(candidates),
    "candidate_states_before": dict(Counter(c["state"] for c in candidates)),
    "research_source": target["source_id"],
    "research_job": research_job["id"],
    "crawl_jobs": [{"id": j["id"], "source_id": j["payload"]["source_id"]} for j in jobs],
}
Path("runtime/server-opencode-validation.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
print(json.dumps(receipt, ensure_ascii=False))
