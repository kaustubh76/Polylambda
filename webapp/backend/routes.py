"""API routes — every handler delegates to the service layer (which calls the real engine)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from . import chain, live, services
from .schemas import KeeperRunRequest, ScoreRequest, SessionRequest

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
        out["live_error"] = "live replay timed out — showing the pre-computed artifact"
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
def get_disputes_analytics(bins: int = Query(24, ge=6, le=60),
                           category: str | None = Query(None),
                           adapter: str | None = Query(None)):
    return services.disputes_analytics(bins=bins, category=category, adapter=adapter)


@api.get("/quote-curve")
def get_quote_curve(category: str = "politics", price: float = Query(0.62, gt=0.0, lt=1.0),
                    horizon_days: float = Query(5.0, gt=0.0), steps: int = Query(41, ge=5, le=81)):
    try:
        return services.quote_curve(category=category, price=price, horizon_days=horizon_days, steps=steps)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"quote-curve failed: {e}")


@api.get("/hf/overview")
async def get_hf_overview(live: bool = False):
    """The HF dataset backbone overview. `?live=1` recomputes from the HF Hub (guarded) when HF_TOKEN set."""
    if not live:
        return services.hf_overview(live=False)
    try:
        return await _with_timeout(lambda: services.hf_overview(live=True), LIVE_TIMEOUT_S)
    except asyncio.TimeoutError:
        out = services.hf_overview(live=False)
        out["live_error"] = "live HF query timed out — showing the shipped cache"
        return out


@api.get("/hf/markets")
def get_hf_markets(q: str | None = None, category: str | None = None, sort: str = "startDate",
                   desc: bool = True, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """Browse recent Polymarket markets from the HF dataset."""
    return services.hf_markets(q=q, category=category, sort=sort, desc=desc, limit=limit, offset=offset)


@api.get("/live/status")
async def get_live_status():
    """Live dispute-feed reachability + head + round-trip latency. Deadline-wrapped: the probe does a
    live eth_blockNumber, and on the 0.5-CPU host a hung keyless-RPC socket must not hold a worker."""
    try:
        return await _with_timeout(live.indexer_status, LIVE_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"reachable": False, "source": "rpc", "error": "status probe timed out"}


@api.get("/live/disputes")
def get_live_disputes(limit: int = Query(25, ge=1, le=100), since_ts: int | None = None):
    """The latest OOv2 disputes straight from the indexer (real-time)."""
    return live.live_disputes(limit=limit, since_ts=since_ts)


# --- testnet fleet + keeper: the CONTINUOUS testnet execution engine ------------------------------
def _keeper():
    from execution.testnet_keeper import get_keeper
    return get_keeper()


def _risk():
    """The keeper's governor, constructed on demand so kill/risk work before any keeper run."""
    k = _keeper()
    if k.risk is None:
        from config.loader import load_config
        from execution.risk import RiskGovernor, RiskLimits
        k.risk = RiskGovernor(RiskLimits.from_config(load_config()))
    return k.risk


@api.get("/testnet/fleet")
def get_testnet_fleet():
    """Per-market on-chain snapshots across the keeper-managed fleet registry."""
    return chain.fleet()


@api.get("/testnet/keeper")
def get_testnet_keeper():
    return _keeper().status()


@api.post("/testnet/keeper/start")
def post_keeper_start():
    k = _keeper()
    started = k.start_background()
    return {"started": started, "running": k.running}


@api.post("/testnet/keeper/stop")
def post_keeper_stop():
    k = _keeper()
    stopped = k.stop()
    return {"stopped": stopped, "running": k.running}


@api.post("/testnet/keeper/run")
def post_keeper_run(req: KeeperRunRequest):
    """Watchdog burst (the GH cron target): start a finite background run if idle; never blocks."""
    k = _keeper()
    if k.running:
        return {"started": False, "running": True, "note": "keeper already running"}
    started = k.start_background(burst_ticks=req.ticks)
    return {"started": started, "running": k.running, "ticks": req.ticks}


@api.get("/testnet/risk")
def get_testnet_risk():
    return _risk().status()


@api.post("/testnet/kill")
def post_testnet_kill():
    """Cross-process kill-switch: writes the kill file — every signing path halts within one tick."""
    r = _risk()
    r.kill("api")
    return r.status()


@api.post("/testnet/unkill")
def post_testnet_unkill():
    r = _risk()
    removed = r.unkill()
    return {"removed": removed, **r.status()}


@api.get("/health")
def get_health():
    return {"ok": True}


@api.post("/admin/refresh")
def post_admin_refresh():
    """Bust the artifact lru_caches so a scheduled regenerate (data.export_disputes + precompute +
    retrain) is picked up without a restart. Idempotent; safe to call anytime."""
    from . import cache
    cache.refresh()
    return {"ok": True, "refreshed": True}
