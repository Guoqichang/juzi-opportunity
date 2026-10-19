"""Initialize only the selected bot app in the isolated sync container."""

import json
import os
import subprocess
from pathlib import Path

cfg = json.loads(Path("/run/secrets/lark-app.json").read_text())
result = subprocess.run(
    [
        "lark-cli",
        "config",
        "init",
        "--name",
        "juzi-sync",
        "--app-id",
        cfg["app_id"],
        "--app-secret-stdin",
        "--brand",
        "feishu",
    ],
    input=cfg["app_secret"] + "\n",
    text=True,
    capture_output=True,
    timeout=30,
)
if result.returncode:
    raise SystemExit("Feishu bot initialization failed; credential output suppressed")
os.execvp("python", ["python", "-m", "compute_policy.base_sync"])
