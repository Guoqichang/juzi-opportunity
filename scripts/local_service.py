"""Install/stop only this project's per-user launchd agents (macOS)."""

import argparse
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = f"gui/{os.getuid()}"
LABELS = {"web": "com.juzibot.opportunity-research.web", "worker": "com.juzibot.opportunity-research.worker"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "stop", "status"])
    parser.add_argument("--dns-mode", choices=["system", "doh"], default="system")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This installer supports macOS launchd; run policy serve/worker on other systems.")
    directory = Path.home() / "Library" / "LaunchAgents"
    directory.mkdir(parents=True, exist_ok=True)
    logs = ROOT / "runtime" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    python = ROOT / ".venv" / "bin" / "python"
    for role, label in LABELS.items():
        path = directory / f"{label}.plist"
        if path.exists():
            old = plistlib.loads(path.read_bytes())
            if old.get("WorkingDirectory") != str(ROOT):
                parser.error(f"Refusing to modify {label}: it belongs to another directory.")
        if args.action == "status":
            result = subprocess.run(
                ["launchctl", "print", f"{DOMAIN}/{label}"], capture_output=True, text=True
            )
            lines = [
                line.strip()
                for line in result.stdout.splitlines()
                if "state =" in line or "pid =" in line or "last exit code" in line
            ]
            print(role, "; ".join(lines) or "not loaded")
            continue
        subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{label}"], capture_output=True)
        if args.action == "stop":
            subprocess.run(["launchctl", "disable", f"{DOMAIN}/{label}"], check=True)
            print(role, "stopped and disabled")
            continue
        content = {
            "Label": label,
            "WorkingDirectory": str(ROOT),
            "ProgramArguments": [str(python), "-m", "compute_policy.cli", "serve", "--port", "8766"]
            if role == "web"
            else [str(python), "-m", "compute_policy.cli", "worker"],
            "EnvironmentVariables": {
                "POLICY_DATA_DIR": str(ROOT / "runtime"),
                "POLICY_DNS_MODE": args.dns_mode,
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            },
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 15,
            "StandardOutPath": str(logs / f"{role}.log"),
            "StandardErrorPath": str(logs / f"{role}.err.log"),
        }
        path.write_bytes(plistlib.dumps(content))
        path.chmod(0o600)
        subprocess.run(["launchctl", "enable", f"{DOMAIN}/{label}"], check=True)
        # launchd can acknowledge bootout before the previous service has fully exited.
        for attempt in range(8):
            result = subprocess.run(
                ["launchctl", "bootstrap", DOMAIN, str(path)], capture_output=True, text=True
            )
            if result.returncode == 0:
                break
            if attempt == 7:
                raise RuntimeError(f"Could not start {label}: {result.stderr.strip()}")
            time.sleep(0.5)
        print(role, "installed", path)


if __name__ == "__main__":
    main()
