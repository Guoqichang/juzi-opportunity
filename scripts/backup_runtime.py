"""Consistent SQLite + immutable evidence backup. Uses only the Python standard library."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def sha(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def export_backup(data_dir, destination):
    data_dir, destination = Path(data_dir).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError("Backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="policy-backup-") as folder:
        staged = Path(folder)
        with closing(sqlite3.connect((data_dir / "policy.db").as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(staged / "policy.db")) as target:
                source.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Database integrity check failed")
                blobs = target.execute("SELECT DISTINCT storage_path,content_hash FROM snapshots").fetchall()
                reports = target.execute("SELECT id,markdown FROM reports").fetchall()
        (staged / "blobs").mkdir()
        for relative, expected in blobs:
            if not re.fullmatch(r"blobs/[0-9a-f]{64}", relative):
                raise ValueError("Invalid evidence storage path")
            original = (data_dir / relative).resolve()
            if not original.is_relative_to(data_dir / "blobs") or sha(original) != expected:
                raise ValueError(f"Evidence integrity check failed: {relative}")
            shutil.copyfile(original, staged / relative)
        (staged / "reports").mkdir()
        for identifier, markdown in reports:
            if not re.fullmatch(r"[0-9a-f]{32}", identifier):
                raise ValueError("Invalid report identifier")
            (staged / "reports" / f"{identifier}.md").write_text(markdown, encoding="utf-8")
        files = {str(p.relative_to(staged)): sha(p) for p in staged.rglob("*") if p.is_file()}
        manifest = {"format": 1, "created_at": datetime.now(UTC).isoformat(), "files": files}
        (staged / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        # Publish only a completed archive. mkstemp is private even under a permissive umask.
        fd, temporary_name = tempfile.mkstemp(prefix=".policy-backup-", dir=destination.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with tarfile.open(temporary, "w:gz") as archive:
                for name in sorted([*files, "manifest.json"]):
                    archive.add(staged / name, arcname=name, recursive=False)
            # link() refuses an existing destination instead of overwriting a concurrent backup.
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return manifest


def validate_archive(archive):
    members = archive.getmembers()
    if len({member.name for member in members}) != len(members):
        raise ValueError("Duplicate archive member")
    for member in members:
        if not member.isfile() or not re.fullmatch(
            r"policy\.db|manifest\.json|blobs/[0-9a-f]{64}|reports/[0-9a-f]{32}\.md", member.name
        ):
            raise ValueError("Unsafe archive member")
    manifest = json.load(archive.extractfile("manifest.json"))
    if manifest.get("format") != 1 or "policy.db" not in manifest["files"]:
        raise ValueError("Unsupported or incomplete backup")
    if {m.name for m in members} != {*manifest["files"], "manifest.json"}:
        raise ValueError("Manifest does not match archive")
    for name, expected in manifest["files"].items():
        with archive.extractfile(name) as file:
            if hashlib.file_digest(file, "sha256").hexdigest() != expected:
                raise ValueError(f"Backup checksum mismatch: {name}")
    return manifest


def restore_backup(backup, destination):
    destination = Path(destination).absolute()
    # Restore into a new directory only. Promotion to live data is a separate deployment step.
    if destination.exists():
        raise ValueError("Restore destination must not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(backup, "r:gz") as archive:
        manifest = validate_archive(archive)
        with tempfile.TemporaryDirectory(prefix=".policy-restore-", dir=destination.parent) as folder:
            staged = Path(folder)
            for name in manifest["files"]:
                path = staged / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(name) as source, path.open("wb") as target:
                    shutil.copyfileobj(source, target)
                path.chmod(0o600)
            (staged / "blobs").mkdir(exist_ok=True)
            (staged / "reports").mkdir(exist_ok=True)
            with closing(sqlite3.connect(staged / "policy.db")) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Restored database integrity check failed")
            if destination.exists():
                raise ValueError("Restore destination was created concurrently")
            os.rename(staged, destination)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("export")
    backup.add_argument("--data-dir", required=True)
    backup.add_argument("--output", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("archive")
    restore = commands.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("--destination", required=True)
    args = parser.parse_args()
    if args.command == "export":
        result = export_backup(args.data_dir, args.output)
    elif args.command == "verify":
        with tarfile.open(args.archive, "r:gz") as archive:
            result = validate_archive(archive)
    else:
        result = restore_backup(args.archive, args.destination)
    print(
        json.dumps(
            {"format": result["format"], "created_at": result["created_at"], "files": len(result["files"])},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
