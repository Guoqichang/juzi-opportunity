from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import JSON, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


def stamp() -> str:
    return now().isoformat()


def uid() -> str:
    return uuid.uuid4().hex


def digest(value) -> str:
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    )
    return hashlib.sha256(raw).hexdigest()


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40), default="policy")
    hosts: Mapped[list] = mapped_column(JSON, default=list)
    health: Mapped[str] = mapped_column(String(40), default="unfetched")
    error: Mapped[str] = mapped_column(Text, default="")
    latest_bundle: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latest_policy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_checked: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_success: Mapped[str | None] = mapped_column(String(40), nullable=True)
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("source_id", "url", "content_hash", "parser_version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(100))
    storage_path: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[str] = mapped_column(String(40), default=stamp)
    parser_version: Mapped[str] = mapped_column(String(50))
    blocks: Mapped[list] = mapped_column(JSON)
    warnings: Mapped[list] = mapped_column(JSON, default=list)


class Bundle(Base):
    __tablename__ = "bundles"
    __table_args__ = (UniqueConstraint("source_id", "content_hash"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    items: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    bundle_id: Mapped[str] = mapped_column(String(32))
    draft: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    reviewer: Mapped[str] = mapped_column(String(100), default="")
    review_note: Mapped[str] = mapped_column(Text, default="")
    extractor: Mapped[str] = mapped_column(Text, default="manual")
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)


class CompanyVersion(Base):
    __tablename__ = "company_versions"
    __table_args__ = (UniqueConstraint("company_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[int] = mapped_column(Integer)
    facts: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    inputs_hash: Mapped[str] = mapped_column(String(64), unique=True)
    company_id: Mapped[str] = mapped_column(String(100), index=True)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    policy_id: Mapped[str] = mapped_column(String(32))
    facts_version: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    stage: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[str] = mapped_column(String(40), default=stamp)
    lease_until: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class ResearchConfig(Base):
    __tablename__ = "research_configs"
    company_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    settings: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(String(100), index=True)
    state: Mapped[str] = mapped_column(String(30), default="running")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[str] = mapped_column(String(40), default=stamp)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("company_id", "url"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(String(100), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))
    document_kind: Mapped[str] = mapped_column(String(40), default="unknown")
    score: Mapped[int] = mapped_column(Integer)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    origins: Mapped[list] = mapped_column(JSON, default=list)
    hints: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(30), default="discovered")
    source_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_seen: Mapped[str] = mapped_column(String(40), default=stamp)
    last_seen: Mapped[str] = mapped_column(String(40), default=stamp)


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"
    __table_args__ = (UniqueConstraint("company_id", "key", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(String(100), index=True)
    key: Mapped[str] = mapped_column(String(150))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="reference")
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    key: Mapped[str] = mapped_column(String(150), unique=True)
    company_id: Mapped[str] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    period_start: Mapped[str] = mapped_column(String(40))
    period_end: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Dialogue(Base):
    __tablename__ = "dialogues"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(String(100), index=True)
    session_id: Mapped[str] = mapped_column(String(100), index=True)
    question: Mapped[str] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class ApplicationDraft(Base):
    __tablename__ = "application_drafts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(String(100), index=True)
    source_id: Mapped[str] = mapped_column(String(32))
    policy_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    facts_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=stamp)


class RuntimeState(Base):
    __tablename__ = "runtime_state"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Followup(Base):
    __tablename__ = "followups"
    company_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="待核实")
    owner: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[str] = mapped_column(String(40), default=stamp)


class SourceRelation(Base):
    __tablename__ = "source_relations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(100), index=True)
    left_id: Mapped[str] = mapped_column(String(32))
    right_id: Mapped[str] = mapped_column(String(32))
    relation: Mapped[str] = mapped_column(String(40), default="待核对")
    program: Mapped[str] = mapped_column(Text, default="")
    batch: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[str] = mapped_column(String(40), default=stamp)


class Store:
    def __init__(self, path: Path | str | None = None, database_url: str | None = None):
        self.path = Path(path or os.getenv("POLICY_DATA_DIR", "runtime")).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "blobs").mkdir(exist_ok=True)
        url = database_url or os.getenv("POLICY_DATABASE_URL") or f"sqlite:///{self.path / 'policy.db'}"
        self.engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {},
            pool_pre_ping=True,
        )
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self):
        with Session(self.engine, expire_on_commit=False) as session:
            with session.begin():
                yield session

    def blob(self, data: bytes) -> tuple[str, str]:
        content_hash = digest(data)
        destination = self.path / "blobs" / content_hash
        if destination.exists():
            if digest(destination.read_bytes()) != content_hash:
                raise ValueError("existing evidence blob has been corrupted")
        else:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return content_hash, str(destination.relative_to(self.path))
