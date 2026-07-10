"""API routes — every handler delegates to the service layer (which calls the real engine)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from . import chain, live, services
from .schemas import EngineQuoteRequest, ResolveRequest, ScoreRequest, SessionRequest

api = APIRouter(prefix="/api")

# heavy live-engine calls run in a worker thread under a hard deadline so a slow one can't make the
# client wait (or, on a constrained single-worker host, starve the threadpool). On timeout we serve
# the fast published fallback instead.
LIVE_TIMEOUT_S = 12.0
SESSION_TIMEOUT_S = 30.0


async def _with_timeout(fn, timeout: float):
    return await asyncio.wait_for(run_in_threadpool(fn), timeout=timeout)


@api.get("/overview")
def get_overview():
    return services.overview()


@api.get("/baserates")
def get_baserates():
    return services.base_rates()


@api.post("/lambda/score")
def post_score(req: ScoreRequest):
    try:
        return services.score_market(
            category=req.category, fill_count=req.fill_count, price=req.price,
            proposer=req.proposer, inventory=req.inventory, horizon_days=req.horizon_days)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"scoring failed: {e}")


@api.post("/session/run")
async def post_session(req: SessionRequest):
    def _run():
        return services.run_session(
            scenario_name=req.scenario, category=req.category, entry_price=req.entry_price,
            inventory=req.inventory, dispute_tick=req.dispute_tick, gap_logit=req.gap_logit,
            n_ticks=req.n_ticks, n_markets=req.n_markets, seed=req.seed,
            source=req.source, hazard=req.hazard)
    try:
        return await _with_timeout(_run, SESSION_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="session timed out")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"session failed: {e}")


@api.get("/ablation")
async def get_ablation(live: bool = False):
    if not live:
        return services.ablation(live=False)
    try:
        return await _with_timeout(lambda: services.ablation(live=True), LIVE_TIMEOUT_S)
    except asyncio.TimeoutError:
        out = services.ablation(live=False)
        out["live_error"] = "live replay timed out — showing the published artifact"
        return out


@api.get("/hazard")
def get_hazard():
    return services.hazard()


@api.get("/disputes")
def get_disputes(category: str | None = None, adapter: str | None = None,
                 year: int | None = None, q: str | None = None, sort: str = "disputeTs",
                 desc: bool = True, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return services.disputes(category=category, adapter=adapter, year=year, q=q, sort=sort,
                             desc=desc, limit=limit, offset=offset)


@api.get("/recon")
def get_recon():
    return services.recon()


@api.get("/recon/live")
async def get_recon_live():
    try:
        return await _with_timeout(services.recon_live, LIVE_TIMEOUT_S)
    except asyncio.TimeoutError:
        base = services.recon()
        base["source"] = "published"
        base["live_error"] = "live reconciliation timed out — showing the published artifact"
        return base


@api.get("/sigma")
def get_sigma():
    return services.sigma_surface()


@api.get("/proposers")
def get_proposers(limit: int = Query(15, ge=1, le=50)):
    return services.proposers(limit=limit)


@api.get("/disputes/analytics")
def get_disputes_analytics(bins: int = Query(24, ge=6, le=60)):
    return services.disputes_analytics(bins=bins)


@api.get("/quote-curve")
def get_quote_curve(category: str = "politics", price: float = Query(0.62, gt=0.0, lt=1.0),
                    horizon_days: float = Query(5.0, gt=0.0), steps: int = Query(41, ge=5, le=81)):
    try:
        return services.quote_curve(category=category, price=price, horizon_days=horizon_days, steps=steps)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"quote-curve failed: {e}")


@api.get("/live/status")
def get_live_status():
    """Live Envio HyperIndex reachability + head + round-trip latency."""
    return live.indexer_status()


@api.get("/live/disputes")
def get_live_disputes(limit: int = Query(25, ge=1, le=100), since_ts: int | None = None):
    """The latest OOv2 disputes straight from the indexer (real-time)."""
    return live.live_disputes(limit=limit, since_ts=since_ts)


# --- testnet: on-chain PolyLambda market on Polygon Amoy ------------------------------------------
@api.get("/testnet/status")
def get_testnet_status():
    return chain.status()


@api.get("/testnet/market")
def get_testnet_market():
    return chain.market()


@api.get("/testnet/position")
def get_testnet_position(address: str = Query(..., min_length=42, max_length=42)):
    return chain.position(address)


@api.get("/testnet/events")
def get_testnet_events(limit: int = Query(30, ge=1, le=100)):
    return chain.events(limit=limit)


@api.post("/testnet/engine-quote")
def post_testnet_quote(req: EngineQuoteRequest):
    try:
        return chain.post_quote(price=req.price, category=req.category)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"engine quote failed: {e}")


@api.post("/testnet/dispute")
def post_testnet_dispute():
    try:
        return chain.flag_dispute()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"flag dispute failed: {e}")


@api.post("/testnet/resolve")
def post_testnet_resolve(req: ResolveRequest):
    try:
        return chain.resolve(req.yes_won)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"resolve failed: {e}")


@api.get("/health")
def get_health():
    return {"ok": True}
