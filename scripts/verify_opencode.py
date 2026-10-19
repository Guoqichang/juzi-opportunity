"""Run inside the research container; print public receipts, never model config."""

import json
import time
from pathlib import Path

import httpx

password = Path("/run/secrets/opencode-password").read_text().strip()
with httpx.Client(base_url="http://127.0.0.1:4096", auth=("policy", password), timeout=210) as client:
    health = client.get("/global/health")
    health.raise_for_status()
    print(json.dumps({"health": health.json()}), flush=True)
    response = client.post("/session", json={"title": "公开来源研究部署验收"})
    response.raise_for_status()
    session_id = response.json()["id"]
    directory = Path("/research") / session_id
    directory.mkdir(mode=0o700)
    request = {
        "url": "https://www.sohu.com/a/1003562534_121123697",
        "expires_at": time.time() + 180,
        "max_tools": 6,
    }
    (directory / "request.json").write_text(json.dumps(request))
    print(json.dumps({"session_id": session_id}), flush=True)
    try:
        response = client.post(
            f"/session/{session_id}/message",
            json={
                "agent": "researcher",
                "parts": [
                    {
                        "type": "text",
                        "text": "请核查公开线索：2026年中关村科学城模型券政策申报指南。原网址 https://www.sohu.com/a/1003562534_121123697 的 robots 禁止访问，不要访问该网址。请调用 policy_search 搜索可能相关的官方通知，再用 policy_fetch 实际抓取官方正文。保留年份和批次，不要宣称两者是同一事项。",
                    }
                ],
            },
        )
        payload = response.json()
        error = payload.get("info", {}).get("error")
        print(
            json.dumps(
                {
                    "status": response.status_code,
                    "error_type": error.get("name") if isinstance(error, dict) else bool(error),
                    "parts": [p.get("type") for p in payload.get("parts", [])],
                }
            ),
            flush=True,
        )
    finally:
        client.post(f"/session/{session_id}/abort", timeout=10)
        request["expires_at"] = 0
        (directory / "request.json").write_text(json.dumps(request))
    print(
        json.dumps(
            {
                "steps": [json.loads(p.read_text()) for p in sorted(directory.glob("step-*.json"))],
                "saved_resources": len(list(directory.glob("resource-*.json"))),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
