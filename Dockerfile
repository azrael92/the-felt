# The Felt — pilot-style poker probability trainer
# Multi-stage Docker build. Final image ~280MB.

# ---- builder ----
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# System deps required for numpy/treys wheels (slim image lacks gcc by default)
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

# Install into a venv so we can copy a clean tree to the runtime image
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install .

# ---- runtime ----
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    THE_FELT_DB=/data/the_felt.db

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY src/ ./src/

# Persistent SQLite volume mount point
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# Healthcheck hits the static index page (lightweight; verifies app is responding)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request, sys; \
    sys.exit(0 if urllib.request.urlopen('http://localhost:8000/', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "the_felt.server.app:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
