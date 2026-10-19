"""Private, authenticated rendering bridge for scheduled discovery."""

import base64
import hmac
import os
import threading
from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .acquire import Resource

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
render_lock = threading.Lock()


class RenderInput(BaseModel):
    url: str = Field(max_length=4000)


@app.post("/render")
def render(data: RenderInput, authorization: str = Header(default="")):
    from .research_tools import render_public

    password = Path(os.environ["POLICY_OPENCODE_PASSWORD_FILE"]).read_text().strip()
    if not hmac.compare_digest(authorization, "Bearer " + password):
        raise HTTPException(401)
    if not render_lock.acquire(blocking=False):
        raise HTTPException(429, "浏览器正在处理另一任务")
    try:
        result = asdict(render_public(data.url))
        result["body"] = base64.b64encode(result["body"]).decode()
        return result
    finally:
        render_lock.release()


def configured():
    return bool(os.getenv("POLICY_BROWSER_URL") and os.getenv("POLICY_OPENCODE_PASSWORD_FILE"))


def render_remote(url):
    password = Path(os.environ["POLICY_OPENCODE_PASSWORD_FILE"]).read_text().strip()
    response = httpx.post(
        os.environ["POLICY_BROWSER_URL"].rstrip("/") + "/render",
        headers={"Authorization": "Bearer " + password},
        json={"url": url},
        timeout=100,
        trust_env=False,
    )
    if response.status_code != 200:
        raise ValueError(f"浏览器读取 HTTP {response.status_code}")
    raw = response.json()
    raw["body"] = base64.b64decode(raw["body"], validate=True)
    return Resource(**raw)
