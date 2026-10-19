from __future__ import annotations

import fcntl
import os
import signal
import threading
import time
from datetime import timedelta

from sqlalchemy import and_, case, or_, select, update

from .extract import configured
from .service import Service, record
from .store import Company, Job, Policy, ResearchConfig, RuntimeState, Source, now, stamp, uid


class Queue:
    def __init__(self, service: Service):
        self.service, self.store = service, service.store

    def enqueue(self, stage, source_id=None, *, company_id=None, report_kind="daily", request_key=None):
        if stage not in {"crawl", "extract", "discover", "report", "research"}:
            raise ValueError("unknown job stage")
        with self.store.session() as session:
            if stage in {"crawl", "extract", "research"}:
                if session.get(Source, source_id) is None:
                    raise KeyError("source not found")
                payload = {"source_id": source_id}
            else:
                if session.get(Company, company_id) is None or company_id == "demo":
                    raise ValueError("主动研究和报告需要真实企业档案")
                payload = {"company_id": company_id}
                if stage == "report":
                    if report_kind not in {"daily", "weekly", "manual"}:
                        raise ValueError("invalid report kind")
                    payload["kind"] = report_kind
            # Repeated HTTP requests with the same key return the same receipt.
            key = request_key or uid()
            old = session.scalar(select(Job).where(Job.key == key))
            if old:
                if old.stage != stage or old.payload != payload:
                    raise ValueError("idempotency key belongs to another request")
                return record(old)
            active = list(
                session.scalars(select(Job).where(Job.stage == stage, Job.state.in_(["pending", "running"])))
            )
            for job in active:
                if job.payload == payload:
                    return record(job)
            job = Job(key=key, stage=stage, payload=payload)
            session.add(job)
            session.flush()
            return record(job)

    def claim(self, *, at=None):
        at = at or now()
        with self.store.session() as session:
            # A crashed worker leaves a lease. It becomes claimable after expiry.
            session.execute(
                update(Job)
                .where(Job.state == "running", Job.lease_until < at.isoformat(), Job.attempts >= 3)
                .values(state="dead", error="任务租约反复过期")
            )
            ready = and_(
                Job.attempts < 3,
                or_(
                    and_(Job.state == "pending", Job.available_at <= at.isoformat()),
                    and_(Job.state == "running", Job.lease_until < at.isoformat()),
                ),
            )
            priority = case((Job.stage == "report", 0), (Job.stage == "discover", 1), else_=2)
            job = session.scalar(select(Job).where(ready).order_by(priority, Job.created_at).limit(1))
            if job is None:
                return None
            token = uid()
            changed = session.execute(
                update(Job)
                .where(Job.id == job.id, ready)
                .values(
                    state="running",
                    attempts=Job.attempts + 1,
                    lease_token=token,
                    lease_until=(at + timedelta(minutes=30)).isoformat(),
                )
            ).rowcount
            if not changed:
                return None
            session.refresh(job)
            return record(job)

    def finish(self, job, error=None, *, terminal=False):
        with self.store.session() as session:
            values = {"state": "done", "lease_until": None, "error": ""}
            if error:
                values.update(
                    state="dead" if terminal or job["attempts"] >= 3 else "pending",
                    error=str(error)[:1000],
                    available_at=(now() + timedelta(seconds=10 * 2 ** job["attempts"])).isoformat(),
                )
            return session.execute(
                update(Job)
                .where(Job.id == job["id"], Job.state == "running", Job.lease_token == job["lease_token"])
                .values(**values)
            ).rowcount

    def run_once(self):
        job = self.claim()
        if not job:
            return False
        try:
            if job["stage"] == "discover":
                from .discovery import Discovery

                Discovery(self.service).run(job["payload"]["company_id"])
            elif job["stage"] == "report":
                from .reports import Reports

                key = job["key"][7:] if job["key"].startswith("report:") else None
                Reports(self.service).generate(job["payload"]["company_id"], job["payload"]["kind"], key=key)
            elif job["stage"] == "crawl":
                self.service.crawl(job["payload"]["source_id"])
            elif job["stage"] == "research":
                from .agent_research import AgentResearch

                AgentResearch(self.service).run(job["payload"]["source_id"])
            else:
                self.service.extract(job["payload"]["source_id"])
            with self.store.session() as session:
                company_ids = list(session.scalars(select(Company.id)))
            for company_id in company_ids:
                self.service.worklist(company_id, save=True)
        except Exception as error:
            self.finish(job, error, terminal=job["stage"] == "extract" and isinstance(error, ValueError))
        else:
            self.finish(job)
        return True

    def schedule(self):
        from .actions import crawl_due, research_priority
        from .agent_research import AgentResearch
        from .agent_research import configured as research_configured
        from .discovery import Discovery
        from .extract import remaining as extraction_remaining
        from .reports import Reports

        discovery = Discovery(self.service)
        for company_id in discovery.due():
            self.enqueue("discover", company_id=company_id)
        with self.store.session() as session:
            company_ids = list(session.scalars(select(ResearchConfig.company_id)))
        for company_id in company_ids:
            for report in Reports(self.service).due(company_id):
                self.enqueue(
                    "report",
                    company_id=company_id,
                    report_kind=report["kind"],
                    request_key="report:" + report["key"],
                )
        for source in self.service.sources():
            if source["kind"] != "policy":
                continue
            if crawl_due(source):
                self.enqueue("crawl", source["id"])
        candidates = [c for company_id in company_ids for c in discovery.candidates(company_id)]
        priorities = {}
        for candidate in candidates:
            if candidate["source_id"]:
                priorities[candidate["source_id"]] = max(
                    priorities.get(candidate["source_id"], -1000), research_priority(candidate)
                )
        ordered = sorted(self.service.sources(), key=lambda s: (-priorities.get(s["id"], -1000), s["id"]))
        if configured() and extraction_remaining(self.service):
            active_extract = sum(
                j["stage"] == "extract" and j["state"] in {"pending", "running"} for j in self.list()
            )
            for source in ordered:
                if active_extract >= min(2, extraction_remaining(self.service)):
                    break
                if (
                    source["kind"] != "policy"
                    or source["health"] == "failed"
                    or priorities.get(source["id"], -1000) < 0
                    or not source["latest_policy"]
                ):
                    continue
                with self.store.session() as session:
                    policy = session.get(Policy, source["latest_policy"])
                    attempt = session.get(RuntimeState, "extraction-attempt:" + source["latest_bundle"])
                    needs_extract = (
                        policy.status != "reviewed"
                        and policy.extractor in {"manual", "manual-template-1"}
                        and (not attempt or attempt.value.get("date") != now().date().isoformat())
                    )
                if needs_extract:
                    self.enqueue("extract", source["id"])
                    active_extract += 1
        if research_configured():
            research = AgentResearch(self.service)
            active = sum(
                j["stage"] == "research" and j["state"] in {"pending", "running"} for j in self.list()
            )
            for source in ordered:
                if active >= min(2, research.remaining()):
                    break
                needs_help = source["health"] == "failed" or (
                    source["health"] == "partial"
                    and any(t in source["error"] for t in ["host outside", "没有可提取文字", "正文过短"])
                )
                if (
                    source["kind"] == "policy"
                    and priorities.get(source["id"], -1000) >= 0
                    and needs_help
                    and research.due(source["id"])
                ):
                    self.enqueue("research", source["id"])
                    active += 1

    def list(self):
        with self.store.session() as session:
            return [
                record(job) for job in session.scalars(select(Job).order_by(Job.created_at.desc()).limit(100))
            ]

    def worker(self, *, once=False):
        # This MVP intentionally has one worker, even if backed by PostgreSQL.
        with (self.store.path / "worker.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("此数据目录已有 worker；第一版只支持单 worker") from error
            stop = threading.Event()
            shutdown = threading.Event()
            previous_handler = None
            if threading.current_thread() is threading.main_thread():
                previous_handler = signal.signal(signal.SIGTERM, lambda *_: shutdown.set())

            def heartbeat():
                while not stop.is_set():
                    try:
                        with self.store.session() as session:
                            state = session.get(RuntimeState, "worker")
                            if state is None:
                                state = RuntimeState(key="worker", value={})
                                session.add(state)
                            state.value, state.updated_at = {"pid": os.getpid(), "state": "running"}, stamp()
                    except Exception:
                        pass  # Busy DB must not terminate the actual job worker.
                    stop.wait(10)

            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
            last_schedule = 0.0
            try:
                while not shutdown.is_set():
                    if time.monotonic() - last_schedule > 60:
                        self.schedule()
                        last_schedule = time.monotonic()
                    worked = self.run_once()
                    if once:
                        return worked
                    if not worked:
                        shutdown.wait(1)
            finally:
                if previous_handler is not None:
                    signal.signal(signal.SIGTERM, previous_handler)
                stop.set()
                thread.join(timeout=1)
                with self.store.session() as session:
                    state = session.get(RuntimeState, "worker")
                    if state:
                        state.value, state.updated_at = {"pid": os.getpid(), "state": "stopped"}, stamp()
