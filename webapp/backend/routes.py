"""API routes — every handler delegates to the service layer (which calls the real engine)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from . import live, services
from .schemas import ScoreRequest, SessionRequest

api = APIRouter(prefix="/api")


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
def post_session(req: SessionRequest):
    try:
        return services.run_session(
            scenario_name=req.scenario, category=req.category, entry_price=req.entry_price,
            inventory=req.inventory, dispute_tick=req.dispute_tick, gap_logit=req.gap_logit,
            n_ticks=req.n_ticks, n_markets=req.n_markets, seed=req.seed)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"session failed: {e}")


@api.get("/ablation")
def get_ablation():
    return services.ablation()


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


@api.get("/sigma")
def get_sigma():
    return services.sigma_surface()


@api.get("/live/status")
def get_live_status():
    """Live Envio HyperIndex reachability + head + round-trip latency."""
    return live.indexer_status()


@api.get("/live/disputes")
def get_live_disputes(limit: int = Query(25, ge=1, le=100), since_ts: int | None = None):
    """The latest OOv2 disputes straight from the indexer (real-time)."""
    return live.live_disputes(limit=limit, since_ts=since_ts)


@api.get("/health")
def get_health():
    return {"ok": True}
