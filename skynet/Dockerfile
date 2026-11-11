# Skynet server image (FastAPI + headless Chromium for PDF evidence records).
# Built and run by Cloudflare Containers via wrangler.jsonc; also runs locally:
#   docker build -t skynet . && docker run -p 8080:8080 skynet
FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-dejavu-core fonts-noto-core ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    SKYNET_CHROME=/usr/bin/chromium PORT=8080
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY server ./server
COPY web ./web
COPY data ./data

EXPOSE 8080
CMD ["/app/.venv/bin/uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
