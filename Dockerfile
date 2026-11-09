# Jambubrowser Engine — Dockerfile
# Multi-stage build targeting Linux amd64/arm64.
#
# Build:  docker build -t jambubrowser-engine .
# Run:    docker run -p 8001:8001 jambubrowser-engine
#
# Environment variables:
#   JAMBU_LLM_PROVIDER   (default: ollama)
#   SEARXNG_URL          (default: http://host.docker.internal:8888/search)
#   ANTHROPIC_API_KEY    (optional)
#   OPENAI_API_KEY       (optional)
#   MINIMAX_API_KEY      (optional)

# ── Stage 1: Builder ────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# CPU-only torch first: the default torch wheel pulls ~3 GB of
# NVIDIA CUDA libraries (nvidia-cudnn/cublas/nccl/...) that a
# containerised engine can never use — it has no GPU, and semantic
# search falls back to numpy without torch acceleration. Installing
# the CPU wheel first satisfies sentence-transformers' torch
# requirement, so the CUDA set is never downloaded. Image drops
# from ~6 GB to ~2 GB and builds on disk-constrained hosts.
RUN pip install --user --no-cache-dir torch \
      --index-url https://download.pytorch.org/whl/cpu
# Installed as root in the builder only; artifacts are re-homed to
# /app/.local (owned by jambu) in the runtime stage below — never
# copied to /root/.local there, which jambu cannot traverse (0700).
RUN pip install --user --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Create non-root user (home is /app)
RUN groupadd -r jambu && useradd -r -g jambu -d /app -s /bin/false jambu

# Runtime system deps: Playwright/Chromium shared libraries, SQLite,
# curl for health checks. These mirror `playwright install-deps chromium`;
# the browser binary itself is fetched as jambu below.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# WORKDIR creates /app root-owned; jambu must own it to create
# .cache/.local at runtime (HOME=/app writes, Playwright browsers).
RUN mkdir -p /app/.cache/ms-playwright && chown -R jambu:jambu /app

# Copy installed packages from builder into a jambu-owned location.
COPY --from=builder --chown=jambu:jambu /root/.local /app/.local
ENV HOME=/app \
    PATH=/app/.local/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/app/.cache/ms-playwright

# Copy application code (backend, tools, and the cli package that
# provides the `jambu` entry point declared in pyproject.toml).
COPY --chown=jambu:jambu backend/ ./backend/
COPY --chown=jambu:jambu tools/ ./tools/
COPY --chown=jambu:jambu cli/ ./cli/

# Run as non-root from here on; the browser install must happen in
# this (runtime) stage so the binary survives into the final image,
# and as jambu so it lands in the jambu-owned browsers path.
USER jambu
RUN python -m playwright install chromium

# Expose the engine port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

ENTRYPOINT ["uvicorn", "backend.engine:app", "--host", "0.0.0.0", "--port", "8001"]
