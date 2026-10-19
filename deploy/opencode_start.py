"""Load the private service password without including it in argv or logs."""

import os
import signal
import subprocess
import sys
from pathlib import Path

os.environ["OPENCODE_SERVER_PASSWORD"] = Path("/run/secrets/opencode-password").read_text().strip()
os.environ["OPENCODE_SERVER_USERNAME"] = "policy"
os.environ["POLICY_OPENCODE_PASSWORD_FILE"] = "/run/secrets/opencode-password"
children = [
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "compute_policy.browser_bridge:app",
            "--host",
            "0.0.0.0",
            "--port",
            "4097",
        ]
    ),
    subprocess.Popen(["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"]),
]


def stop(*_):
    for child in children:
        child.terminate()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    # If either component exits, restart the pair through the container supervisor.
    while all(child.poll() is None for child in children):
        import time

        time.sleep(1)
finally:
    stop()
    for child in children:
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
