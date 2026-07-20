"""FastAPI app assembly for the PolyLambda dashboard.

Run (dev):   uvicorn webapp.backend.main:app --reload --port 8000     (repo root, in .venv)
The Vite dev server (webapp/frontend, port 5173) proxies /api → 8000. For a single-process demo,
`npm run build` the frontend and this app serves webapp/frontend/dist as static.

The gated mainnet CLOB write path (execution.clob.place_order) is never imported. The TESTNET
execution engine (execution/testnet_keeper.py — engine-signed Amoy transactions, risk-governed)
runs as a background thread when KEEPER_AUTOSTART=1; it is entirely separate from the CLOB gate.
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
        # Pre-compute the HEAVY read endpoints so the FIRST visitor hits warm caches instead of a cold
        # 12s quote-curve / 8s events (on the free tier's 0.5 CPU a cold compute + concurrent card loads
        # = gateway 502s). Each is best-effort — a warm miss must never crash startup.
        # Only OFFLINE (cache/estimator) endpoints here — NOT the chain reads (chain.fleet hits the
        # Amoy RPC; warming it in a background daemon would do real network in the test lifespan and
        # leak a thread, exactly like the tail scan). The fleet read is instead kept warm in prod by
        # its cache TTL + the keepalive workflow pinging it.
        from . import services
        for label, fn in (("overview", services.overview),
                          ("quote_curve", services.quote_curve),
                          ("sigma", services.sigma_surface),
                          ("hf_overview", services.hf_overview),
                          ("base_rates", services.base_rates),
                          ("disputes_analytics", services.disputes_analytics)):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                print(f"[webapp] warm {label} skipped: {type(e).__name__}")
        # kick the keyless-RPC dispute tail scan so the live feed is warm before users poll it
        try:
            from . import live
            live.warm_tail()                  # no-op if the tail was disabled (e.g. the test session)
        except Exception:  # noqa: BLE001 — live feed is optional; the dashboard runs without it
            pass
        print(f"[webapp] offline DI installed (base-rate denominators: {src}); caches warmed (heavy endpoints prewarmed).")
        # the continuous testnet execution engine (opt-in: real signed txs need an explicit flag;
        # never under pytest, where KEEPER_AUTOSTART is unset)
        if os.environ.get("KEEPER_AUTOSTART") == "1":
            try:
                from execution.testnet_keeper import get_keeper
                get_keeper().start_background()
                print("[webapp] testnet keeper autostarted (KEEPER_AUTOSTART=1)")
            except Exception as e:  # noqa: BLE001 — the dashboard must run without the keeper
                print(f"[webapp] keeper autostart failed: {type(e).__name__}: {e}")

    threading.Thread(target=_warm, name="cache-warm", daemon=True).start()
    yield
    # Shutdown: stop the background RPC tail scan and join it, so it never outlives this app instance.
    # Under pytest a TestClient enters/exits lifespan per context; without this the daemon scan thread
    # would linger and call data.disputes._rpc after a later test monkeypatched it.
    try:
        from . import live
        live.stop_tail()
    except Exception:  # noqa: BLE001
        pass
    # stop the keeper thread (no-op if it never started) so it can't outlive the app instance
    try:
        from execution import testnet_keeper
        if testnet_keeper._keeper is not None:
            testnet_keeper._keeper.stop(timeout=5.0)
    except Exception:  # noqa: BLE001
        pass


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
