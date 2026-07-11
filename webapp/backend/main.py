"""FastAPI app assembly for the PolyLambda dashboard.

Run (dev):   uvicorn webapp.backend.main:app --reload --port 8000     (repo root, in .venv)
The Vite dev server (webapp/frontend, port 5173) proxies /api → 8000. For a single-process demo,
`npm run build` the frontend and this app serves webapp/frontend/dist as static.

PAPER-mode only: the gated CLOB write path (execution.clob.place_order) is never imported.
"""
from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import cache
from .routes import api

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # make the real estimate_lambda offline (inject cached HF denominators) — cheap, so do it
    # before serving. The heavier cache warms run in a background thread: uvicorn only starts
    # accepting connections after lifespan startup returns, and on a small host (Render cold
    # start) a synchronous warm keeps the port unbound long enough for the gateway to 502
    # incoming requests. Every route falls back to published constants until the warm lands.
    src = cache.install_offline_di()

    def _warm():
        cache.dataset_stats(); cache.hazard_models(); cache.disputes_by_proposer()
        print(f"[webapp] offline DI installed (base-rate denominators: {src}); caches warmed.")

    threading.Thread(target=_warm, name="cache-warm", daemon=True).start()
    yield


app = FastAPI(title="PolyLambda Dashboard API",
              description="A thin, read-only layer over the real PolyLambda engine (paper-mode).",
              version="0.1.0", lifespan=lifespan)

# dev CORS: the Vite dev server origin. Same-origin in the built single-process deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"], allow_credentials=False,
)

app.include_router(api)


# --- serve the built SPA (if present) so `npm run build` → single process on :8000 ---------------
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # API routes are matched first by FastAPI; everything else → the SPA entry (client routing).
        target = FRONTEND_DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    def root():
        return {"service": "PolyLambda Dashboard API", "docs": "/docs",
                "note": "Frontend not built yet — run `cd webapp/frontend && npm run dev` (port 5173),"
                        " or `npm run build` to serve it here.",
                "endpoints": ["/api/overview", "/api/baserates", "/api/lambda/score",
                              "/api/session/run", "/api/ablation", "/api/hazard", "/api/disputes",
                              "/api/recon", "/api/sigma"]}
