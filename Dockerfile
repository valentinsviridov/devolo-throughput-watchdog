FROM python:3.12.9-slim-bookworm

# Copy uv binary from official image (pinned version)
COPY --from=ghcr.io/astral-sh/uv:0.6.5 /uv /uvx /bin/

# Install system utilities required for network probing
RUN apt-get update && apt-get install -y --no-install-recommends \
    iperf3 \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ensure virtual environment binaries are on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Copy metadata and lockfile before running uv sync
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-editable --no-dev --no-cache --no-install-project

# Copy application package and install project
COPY devolo_watchdog ./devolo_watchdog
RUN uv sync --locked --no-editable --no-dev --no-cache

# Run non-root user for container security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD ["python", "-m", "devolo_watchdog", "healthcheck"]

ENTRYPOINT ["python", "-m", "devolo_watchdog"]
