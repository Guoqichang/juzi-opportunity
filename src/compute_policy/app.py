from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import Field
from sqlalchemy.exc import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .actions import Actions, FollowupInput, RelationInput
from .base_sync import SyncResolution
from .contracts import Approval, CompanyInput, Contract
from .discovery import IDENTITY_POLICY, Discovery
from .extract import configured
from .jobs import Queue
from .knowledge import Knowledge
from .reports import Reports
from .research_contracts import (
    FactAnswer,
    FormRequest,
    KnowledgeInput,
    QuestionInput,
    ResearchJobInput,
    SettingsUpdate,
)
from .service import Conflict, Service
from .store import RuntimeState, Snapshot, Store, digest, now


class SourceInput(Contract):
    url: str = Field(max_length=4000)
    title: str = Field(default="", max_length=500)
    hosts: list[str] = Field(default_factory=list, max_length=20)


class JobInput(Contract):
    stage: str
    source_id: str
    request_key: str | None = Field(default=None, max_length=100)


def create_app(store=None):
    service = Service(store or Store())
    queue = Queue(service)
    app = FastAPI(title="句子机会研究台", version="0.2.0")
    app.state.service, app.state.queue = service, queue
    deployment = os.getenv("POLICY_DEPLOYMENT", "local")
    hosts = [h.strip().lower() for h in os.getenv("POLICY_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if any("*" in h or "/" in h or ":" in h for h in hosts):
        raise ValueError("POLICY_ALLOWED_HOSTS must contain exact hostnames without schemes or ports")
    if deployment == "server" and not hosts:
        raise ValueError("Server deployment requires POLICY_ALLOWED_HOSTS")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver", *hosts])

    @app.middleware("http")
    async def local_actions(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            valid_origin = not origin or (
                urlsplit(origin).netloc == request.headers.get("host")
                and urlsplit(origin).scheme == request.url.scheme
            )
            if not valid_origin or request.headers.get("x-policy-action") != "1":
                return JSONResponse({"detail": "操作要求同源请求和 X-Policy-Action: 1"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(ValueError)
    async def value_error(_, error):
        return JSONResponse({"detail": str(error)}, status_code=409 if isinstance(error, Conflict) else 400)

    @app.exception_handler(KeyError)
    async def missing(_, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(IntegrityError)
    async def collision(_, error):
        return JSONResponse({"detail": "另一项操作已更新数据，请刷新后重试"}, status_code=409)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (Path(__file__).parent / "web" / "index.html").read_text()

    @app.get("/assets/{name}")
    def asset(name: str):
        if name not in {"app.js", "style.css", "features.js"}:
            raise KeyError("asset not found")
        return FileResponse(Path(__file__).parent / "web" / name)

    @app.get("/api/status")
    def status():
        from .agent_research import AgentResearch, daily_limit

        with service.store.session() as session:
            heartbeat = session.get(RuntimeState, "worker")
            worker = {
                "running": bool(
                    heartbeat
                    and heartbeat.value.get("state") == "running"
                    and now() - datetime.fromisoformat(heartbeat.updated_at) < timedelta(seconds=35)
                ),
                "last_heartbeat": heartbeat.updated_at if heartbeat else None,
            }
        return {
            "model_configured": configured() or os.getenv("POLICY_EXTRACTION_ENABLED") == "1",
            "mode": f"{deployment}-single-worker",
            "database": service.store.engine.dialect.name,
            "worker": worker,
            "research_agent": {
                "configured": os.getenv("POLICY_RESEARCH_ENABLED") == "1",
                "engine": "OpenCode",
                "daily_limit": daily_limit(),
                "remaining": AgentResearch(service).remaining(),
            },
        }

    @app.get("/api/sources")
    def sources():
        return service.sources()

    @app.post("/api/sources")
    def add_source(data: SourceInput):
        source = service.add_source(**data.model_dump())
        queue.enqueue("crawl", source["id"])
        return source

    @app.get("/api/sources/{source_id}")
    def detail(source_id: str):
        return service.detail(source_id)

    @app.post("/api/policies/{policy_id}/review")
    def review(policy_id: str, data: Approval):
        return service.approve(policy_id, data)

    @app.get("/api/companies/{company_id}")
    def company(company_id: str):
        return service.company(company_id)

    @app.put("/api/companies/{company_id}")
    def save_company(company_id: str, data: CompanyInput):
        return service.save_company(company_id, data)

    @app.get("/api/worklist/{company_id}")
    def worklist(company_id: str):
        return service.worklist(company_id)

    @app.get("/api/export/{company_id}")
    def export(company_id: str):
        return PlainTextResponse(
            service.export(company_id),
            headers={"Content-Disposition": 'attachment; filename="compute-policy-worklist.md"'},
        )

    @app.get("/api/jobs")
    def jobs():
        return queue.list()

    @app.post("/api/jobs")
    def job(data: JobInput):
        return queue.enqueue(**data.model_dump())

    @app.get("/api/research/{company_id}")
    def research(company_id: str):
        discovery = Discovery(service)
        return {
            "identity_policy": IDENTITY_POLICY,
            "config": discovery.config(company_id),
            "candidates": discovery.candidates(company_id),
            "runs": discovery.runs(company_id),
            "feedback": Actions(service).feedback(company_id),
        }

    @app.get("/api/actions/{company_id}")
    def action_plan(company_id: str):
        return {
            "actions": Actions(service).plan(company_id),
            "relations": Actions(service).relations(company_id),
        }

    @app.put("/api/feedback/{company_id}/{candidate_id}")
    def feedback(company_id: str, candidate_id: str, data: FollowupInput):
        return Actions(service).save_feedback(company_id, candidate_id, data)

    @app.post("/api/relations/{company_id}")
    def relation(company_id: str, data: RelationInput):
        return Actions(service).save_relation(company_id, data)

    @app.get("/api/research-history/{source_id}")
    def research_history(source_id: str):
        from .agent_research import AgentResearch

        service.detail(source_id)
        return AgentResearch(service).history(source_id)

    @app.post("/api/base-sync/resolve")
    def resolve_base(data: SyncResolution):
        from .base_sync import resolve

        return resolve(service, data)

    @app.get("/api/base-sync")
    def base_status():
        from .base_sync import public_status

        return public_status(service)

    @app.put("/api/research/{company_id}/config")
    def research_config(company_id: str, data: SettingsUpdate):
        return Discovery(service).save_config(company_id, data)

    @app.post("/api/research/discover")
    def discover(data: ResearchJobInput):
        Discovery(service).config(data.company_id)
        return queue.enqueue("discover", company_id=data.company_id)

    @app.post("/api/research/{company_id}/candidates/{candidate_id}/collect")
    def collect_candidate(company_id: str, candidate_id: str):
        source = Discovery(service).register_candidate(company_id, candidate_id)
        return queue.enqueue("crawl", source["id"])

    @app.post("/api/research/{company_id}/candidates/{candidate_id}/investigate")
    def investigate_candidate(company_id: str, candidate_id: str):
        if os.getenv("POLICY_RESEARCH_ENABLED") != "1":
            raise ValueError("深入研究服务尚未配置")
        source = Discovery(service).register_candidate(company_id, candidate_id)
        return queue.enqueue("research", source["id"])

    @app.get("/api/reports/{company_id}")
    def reports(company_id: str):
        return Reports(service).list(company_id)

    @app.post("/api/reports/generate")
    def generate_report(data: ResearchJobInput):
        return Reports(service).generate(data.company_id, data.kind)

    @app.get("/api/reports/{company_id}/{report_id}/download")
    def report_download(company_id: str, report_id: str):
        report = next((r for r in Reports(service).list(company_id) if r["id"] == report_id), None)
        if report is None:
            raise KeyError("report not found")
        return PlainTextResponse(
            report["markdown"], headers={"Content-Disposition": 'attachment; filename="research-report.md"'}
        )

    @app.get("/api/knowledge/{company_id}")
    def knowledge(company_id: str):
        return Knowledge(service).entries(company_id)

    @app.post("/api/knowledge/{company_id}")
    def knowledge_save(company_id: str, data: KnowledgeInput):
        return Knowledge(service).save(company_id, data)

    @app.post("/api/qa/{company_id}")
    def ask(company_id: str, data: QuestionInput):
        return Knowledge(service).ask(company_id, data)

    @app.get("/api/qa/{company_id}")
    def conversation(company_id: str, session_id: str = "default"):
        return Knowledge(service).conversation(company_id, session_id)

    @app.get("/api/questions/{company_id}")
    def questions(company_id: str, source_id: str | None = None):
        return Knowledge(service).followups(company_id, source_id)

    @app.post("/api/answers/{company_id}")
    def answer(company_id: str, data: FactAnswer):
        return Knowledge(service).answer_fact(company_id, data)

    @app.post("/api/forms/{company_id}")
    def prepare_form(company_id: str, data: FormRequest):
        return Knowledge(service).prepare_form(company_id, data)

    @app.get("/api/forms/{company_id}")
    def forms(company_id: str):
        return Knowledge(service).forms(company_id)

    @app.get("/api/forms/{company_id}/{draft_id}/download")
    def form_download(company_id: str, draft_id: str):
        import json

        draft = next((d for d in Knowledge(service).forms(company_id) if d["id"] == draft_id), None)
        if draft is None:
            raise KeyError("application draft not found")
        if draft["stale"]:
            raise Conflict("公司资料或政策已更新，请重新生成草稿")
        return PlainTextResponse(
            json.dumps(draft, ensure_ascii=False, indent=2),
            headers={"Content-Disposition": 'attachment; filename="application-draft.json"'},
        )

    @app.get("/api/evidence/{snapshot_id}/download")
    def download(snapshot_id: str):
        with service.store.session() as session:
            snapshot = session.get(Snapshot, snapshot_id)
            if snapshot is None:
                raise KeyError("snapshot not found")
            path = (service.store.path / snapshot.storage_path).resolve()
            if (
                not path.is_relative_to(service.store.path / "blobs")
                or digest(path.read_bytes()) != snapshot.content_hash
            ):
                raise ValueError("原文文件完整性检查未通过")
            suffix = PurePosixPath(urlsplit(snapshot.url).path).suffix
            suffix = (
                suffix
                if suffix.lower() in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".html"}
                else ".bin"
            )
            return FileResponse(
                path, media_type="application/octet-stream", filename="evidence-" + snapshot.id + suffix
            )

    return app
