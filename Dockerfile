# PolyLambda MVP dashboard — full BE+FE in one image.
# One uvicorn process serves the built React SPA AND the /api routes (same origin, no CORS).
# Paper-mode only: the gated CLOB write path is never installed or imported.

# ---- stage 1: build the React frontend -------------------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /fe
# install deps against the lockfile first (better layer caching)
COPY webapp/frontend/package.json webapp/frontend/package-lock.json ./
RUN npm ci
COPY webapp/frontend/ ./
RUN npm run build          # → /fe/dist

# ---- stage 2: python runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_PROGRESS_BARS=1 \
    MODE=paper
WORKDIR /app

# slim runtime deps (all manylinux wheels — no build toolchain needed)
COPY webapp/requirements-deploy.txt ./webapp/requirements-deploy.txt
RUN pip install -r webapp/requirements-deploy.txt

# the engine + backend (heavy .data_cache, node_modules, dist, etc. excluded via .dockerignore)
COPY . .
# the built SPA from stage 1 → exactly where main.py serves it (parents[1]/frontend/dist)
COPY --from=frontend /fe/dist ./webapp/frontend/dist
# place the tiny non-regenerable artifacts where cache.py already looks (.data_cache/*)
RUN mkdir -p .data_cache && cp -r webapp/deploy/cache/. .data_cache/

# non-root
RUN useradd -m -u 10001 app && chown -R app:app /app
USER app

EXPOSE 8000
# $PORT is injected by Railway/Render/Cloud Run; default 8000 for `docker run`/Fly.
CMD ["sh", "-c", "uvicorn webapp.backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
