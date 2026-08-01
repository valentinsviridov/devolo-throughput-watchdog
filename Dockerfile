FROM python:3.12-slim

# Copy uv binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system utilities required for network probing
RUN apt-get update && apt-get install -y --no-install-recommends \
    iperf3 \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ensure virtual environment binaries are on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Install Python dependencies using uv
COPY pyproject.toml ./
RUN uv sync --no-dev --no-cache

# Copy application package
COPY devolo_watchdog ./devolo_watchdog

ENTRYPOINT ["python", "-m", "devolo_watchdog"]
