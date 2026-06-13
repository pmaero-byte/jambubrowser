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
RUN pip install --user --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Create non-root user
RUN groupadd -r jambu && useradd -r -g jambu -d /app -s /bin/false jambu

# Runtime system deps: Playwright browsers, SQLite, curl for health checks
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

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

WORKDIR /app

# Copy application code
COPY backend/ ./backend/
COPY tools/ ./tools/

# Expose the engine port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Run as non-root
USER jambu

ENTRYPOINT ["uvicorn", "backend.engine:app", "--host", "0.0.0.0", "--port", "8001"]
