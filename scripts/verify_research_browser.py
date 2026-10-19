"""Exercise the actual Chromium renderer against deterministic HTTP fixtures."""

import json
import socket

import httpx

from compute_policy import research_tools
from compute_policy.acquire import Fetcher

calls = []


def resolve(host, port, **_):
    address = "127.0.0.1" if host == "127.0.0.1" else "93.184.216.34"
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]


def handle(request):
    calls.append(str(request.url))
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    if request.url.path == "/data.json":
        return httpx.Response(200, json={"text": "JS_RENDERED_POLICY " * 20})
    return httpx.Response(
        200,
        headers={"content-type": "text/html"},
        text="""
      <html><head><title>Dynamic public policy</title></head><body>
      <article><p id="body">Loading</p></article>
      <script>fetch('/data.json').then(r=>r.json()).then(x=>document.getElementById('body').textContent=x.text);
      fetch('http://127.0.0.1/private').catch(()=>{});
      fetch('/must-not-post', {method:'POST', body:'no'}).catch(()=>{});</script></body></html>""",
    )


research_tools.Fetcher = lambda hosts, delay: Fetcher(
    hosts, delay=0, resolver=resolve, transport=httpx.MockTransport(handle)
)
resource = research_tools.render_public("https://public.example/notice")
assert any("JS_RENDERED_POLICY" in block["text"] for block in resource.blocks)
assert all("127.0.0.1" not in url and "must-not-post" not in url for url in calls)
assert all(block["locator"].startswith("browser-dom:") for block in resource.blocks)
print(
    json.dumps(
        {
            "javascript_rendered": True,
            "private_destination_blocked": True,
            "post_blocked": True,
            "blocks": len(resource.blocks),
        }
    )
)
