"""Bounded public-web tools for the isolated OpenCode research process.

The model can request these operations, but cannot write evidence or execute shell
commands. Only the tool process saves fetched bytes; the application reads receipts.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from .acquire import MAX_BYTES, AcquisitionError, Fetcher, Resource, normalize, parse
from .store import digest


def session_directory(session_id):
    if not re.fullmatch(r"ses_[A-Za-z0-9_-]{8,100}", session_id):
        raise ValueError("invalid research session")
    root = Path(os.environ["POLICY_RESEARCH_RESULTS_DIR"]).resolve()
    directory = root / session_id
    if directory.is_symlink() or not (directory / "request.json").is_file():
        raise ValueError("research session is not authorized")
    return directory


def claim_tool(directory):
    with (directory / "budget.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        request = json.loads((directory / "request.json").read_text())
        if time.time() >= request["expires_at"]:
            raise ValueError("research time budget exhausted")
        path = directory / "count.json"
        used = json.loads(path.read_text()) if path.exists() else 0
        if used >= min(request["max_tools"], 6):
            raise ValueError("research tool budget exhausted")
        path.write_text(json.dumps(used + 1))
        return used + 1


def render_public(url):
    """Render JS with every HTTP request fulfilled by the DNS-pinned fetcher.

    Chromium has no direct DNS egress, service workers or WebSockets. No existing
    browser profile, login cookies, machine files or production database are used.
    """
    from playwright.sync_api import sync_playwright

    url = normalize(url)
    fetcher = Fetcher([urlsplit(url).hostname], delay=0.15)
    requests, total = 0, 0
    failures = []
    started = time.monotonic()
    try:
        fetcher._allowed(url)
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--host-resolver-rules=MAP * ~NOTFOUND",
                    "--disable-quic",
                    "--disable-background-networking",
                ],
            )
            context = browser.new_context(service_workers="block", accept_downloads=False)
            context.route_web_socket("**/*", lambda ws: ws.close())

            def route_request(route):
                nonlocal requests, total
                request = route.request
                requests += 1
                if request.method != "GET" or requests > 60 or time.monotonic() - started > 45:
                    route.abort()
                    return
                try:
                    target = normalize(request.url)
                    if request.is_navigation_request():
                        fetcher._allowed(target, public_link=True)
                    status, headers, body = fetcher._request(target, public_link=True)
                    total += len(body)
                    if total > 25 * 1024 * 1024:
                        raise AcquisitionError("browser transfer budget exhausted")
                    kept = {k: v for k, v in headers.items() if k.lower() in {"content-type", "location"}}
                    route.fulfill(status=status, headers=kept, body=body)
                except Exception as error:
                    failures.append(type(error).__name__ + ": " + str(error)[:200])
                    route.abort()

            context.route("**/*", route_request)
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            if response is None or response.status != 200:
                raise AcquisitionError("browser navigation did not return HTTP 200")
            body = page.content().encode()
            final_url = normalize(page.url)
            browser.close()
        if len(body) > MAX_BYTES:
            raise AcquisitionError("rendered document exceeds size budget")
        resource = parse(Resource(final_url, body, "text/html"))
        for block in resource.blocks:
            block["locator"] = "browser-dom:" + block["locator"]
        if failures:
            resource.warnings.append(f"浏览器有 {len(failures)} 个子资源未加载，正文完整性需核对")
        return resource
    finally:
        fetcher.close()


def execute(session_id, action, argument):
    directory = session_directory(session_id)
    number = claim_tool(directory)
    entry = {"step": number, "action": action, "argument": argument[:1500], "at": time.time()}
    try:
        if action == "search":
            from ddgs import DDGS

            if not argument.strip() or len(argument) > 300:
                raise ValueError("search query must contain 1-300 characters")
            rows = list(DDGS(timeout=18).text(argument, region="cn-zh", max_results=5, backend="bing"))[:5]
            output = [
                {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")[:700]}
                for r in rows
            ]
            entry["results"] = output
        elif action in {"fetch", "render"}:
            url = normalize(argument)
            if action == "render":
                resource = render_public(url)
            else:
                fetcher = Fetcher([urlsplit(url).hostname])
                try:
                    resource = fetcher.fetch(url)
                finally:
                    fetcher.close()
            content = asdict(resource)
            content["body"] = base64.b64encode(resource.body).decode()
            receipt = {
                "requested_url": url,
                "method": action,
                "resource": content,
                "sha256": digest(resource.body),
            }
            filename = f"resource-{number}.json"
            temporary = directory / (filename + ".tmp")
            temporary.write_text(json.dumps(receipt, ensure_ascii=False))
            temporary.replace(directory / filename)
            output = {
                "url": resource.url,
                "title": resource.title,
                "method": action,
                "text": "\n".join(b["text"] for b in resource.blocks)[:6500],
                "links": resource.links[:30],
                "warnings": resource.warnings,
                "evidence_saved": True,
            }
            entry.update(
                url=resource.url,
                title=resource.title,
                blocks=len(resource.blocks),
                warnings=resource.warnings,
            )
        else:
            raise ValueError("unknown public research tool")
        entry["state"] = "done"
        return output
    except Exception as error:
        entry.update(state="failed", error=str(error)[:500])
        return {"error": entry["error"], "evidence_saved": False}
    finally:
        (directory / f"step-{number}.json").write_text(json.dumps(entry, ensure_ascii=False))


if __name__ == "__main__":
    try:
        print(json.dumps(execute(*sys.argv[1:]), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"error": str(error)[:300], "evidence_saved": False}, ensure_ascii=False))
