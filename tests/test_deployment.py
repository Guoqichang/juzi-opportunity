import importlib.util
import io
import signal
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from compute_policy.app import create_app
from compute_policy.jobs import Queue
from compute_policy.service import Service
from compute_policy.store import Store

spec = importlib.util.spec_from_file_location(
    "backup_runtime", Path(__file__).resolve().parents[1] / "scripts" / "backup_runtime.py"
)
backup_runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup_runtime)


def test_server_domain_and_https_origin(monkeypatch, service):
    monkeypatch.setenv("POLICY_DEPLOYMENT", "server")
    with pytest.raises(ValueError, match="POLICY_ALLOWED_HOSTS"):
        create_app(service.store)
    monkeypatch.setenv("POLICY_ALLOWED_HOSTS", "research.example.com")
    client = TestClient(create_app(service.store), base_url="https://research.example.com")
    assert client.get("/api/status").json()["mode"] == "server-single-worker"
    assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 400
    headers = {"X-Policy-Action": "1", "Origin": "https://research.example.com"}
    assert (
        client.post("/api/sources", json={"url": "https://example.com/p"}, headers=headers).status_code == 200
    )
    headers["Origin"] = "http://research.example.com"
    assert (
        client.post("/api/sources", json={"url": "https://example.com/p"}, headers=headers).status_code == 403
    )
    monkeypatch.setenv("POLICY_ALLOWED_HOSTS", "*")
    with pytest.raises(ValueError, match="exact hostnames"):
        create_app(service.store)


def test_backup_roundtrip_preserves_evidence_and_refuses_overwrite(demo, tmp_path):
    archive = tmp_path / "backup.tar.gz"
    manifest = backup_runtime.export_backup(demo.store.path, archive)
    assert archive.stat().st_mode & 0o077 == 0
    with tarfile.open(archive, "r:gz") as opened:
        assert backup_runtime.validate_archive(opened) == manifest
    destination = tmp_path / "restored"
    backup_runtime.restore_backup(archive, destination)
    restored = Service(Store(destination))

    def stable(worklist):
        return [
            (item["source_id"], item["decision"], item["bundle_id"], item["facts_version"])
            for item in worklist["items"]
        ]

    assert stable(restored.worklist("demo")) == stable(demo.worklist("demo"))
    assert restored.company("juzi") == demo.company("juzi")
    for source in demo.sources():
        assert restored.detail(source["id"])["blocks"] == demo.detail(source["id"])["blocks"]
    with pytest.raises(ValueError, match="already exists"):
        backup_runtime.export_backup(demo.store.path, archive)
    with pytest.raises(ValueError, match="must not exist"):
        backup_runtime.restore_backup(archive, destination)


def test_backup_rejects_corruption_and_archive_path_escape(demo, tmp_path):
    blob = next((demo.store.path / "blobs").iterdir())
    blob.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Evidence integrity"):
        backup_runtime.export_backup(demo.store.path, tmp_path / "bad.tar.gz")
    malicious = tmp_path / "unsafe.tar.gz"
    with tarfile.open(malicious, "w:gz") as archive:
        member = tarfile.TarInfo("../escaped")
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="Unsafe archive"):
        backup_runtime.restore_backup(malicious, tmp_path / "destination")
    assert not (tmp_path / "escaped").exists()
    assert not (tmp_path / "destination").exists()


def test_worker_finishes_current_task_on_sigterm_and_restores_handler(service, monkeypatch):
    queue = Queue(service)
    previous = signal.getsignal(signal.SIGTERM)
    completed = []
    monkeypatch.setattr(queue, "schedule", lambda: None)

    def work():
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        completed.append("task completed")
        return True

    monkeypatch.setattr(queue, "run_once", work)
    queue.worker()
    assert completed == ["task completed"]
    assert signal.getsignal(signal.SIGTERM) == previous
