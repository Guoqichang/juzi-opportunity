#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose --env-file .env run --rm --no-deps -T \
  -v "$PWD/backups:/backups" web python /app/scripts/backup_runtime.py export \
  --data-dir /data --output "/backups/policy-$backup_stamp.tar.gz"
