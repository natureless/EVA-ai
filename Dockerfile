# EVA-MVSC Production Dockerfile
# Multi-stage build for minimal image size and security

# ── Stage 1: Build ──────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --target=/build/deps -r requirements.txt

# ── Stage 2: Runtime ────────────────────────────────────────
FROM python:3.11-slim

# Security: create non-root user first
RUN groupadd -r eva && useradd -r -g eva -u 1000 -d /app eva

WORKDIR /app

# Copy dependencies from builder
COPY --from=builder /build/deps /usr/local/lib/python3.11/site-packages/

# Copy application code
COPY --chown=eva:eva . .

# Security hardening
RUN chmod -R 750 /app && \
    chmod 770 /app/data /app/logs 2>/dev/null || true && \
    chmod 400 /app/constitution.yaml 2>/dev/null || true

USER eva

# Environment
ENV EVA_ENV=production
ENV EVA_LOG_LEVEL=INFO
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check — validates the full /health/ready endpoint (includes MVSC when enabled)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,json; \
    r=urllib.request.urlopen('http://localhost:8000/health/ready'); \
    d=json.loads(r.read()); \
    assert d['status']=='ready', f'not ready: {d[\"status\"]}'"

EXPOSE 8000

# Use multiple workers in production
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--log-level", "info"]
