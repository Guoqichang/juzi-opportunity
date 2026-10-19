FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv
WORKDIR /app
RUN groupadd --gid 10001 policy && useradd --uid 10001 --gid policy --no-create-home policy
RUN apt-get update && apt-get install -y --no-install-recommends antiword \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# Install the versions captured by uv.lock, using uv only during the image build.
RUN uv sync --frozen --no-dev --no-editable \
    && mkdir -p /data && chown policy:policy /data
COPY scripts/backup_runtime.py ./scripts/backup_runtime.py
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 POLICY_DATA_DIR=/data
USER 10001:10001
CMD ["policy", "serve", "--host", "0.0.0.0", "--port", "8766"]
