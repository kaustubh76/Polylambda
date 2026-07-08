"""Service layer: each function is a thin wrapper that calls the REAL engine / reads a REAL
artifact and returns a JSON-able dict. No domain logic is reimplemented here — see the endpoint↔
function map in webapp/README.md.
"""
from __future__ import annotations

import math

from . import cache
from . import constants as K
from . import scenario


# ---------------------------------------------------------------------------------------------
# overview / headline
# ---------------------------------------------------------------------------------------------
def overview() -> dict:
    stats = cache.dataset_stats()
    hz = cache.hazard_models()
    deployed = hz.get("deployed") or {}
    metrics = deployed.get("metrics") or {}
    frozen, frozen_src = cache.frozen_config()
    recon = stats.get("recon") or {}
    tiles = [
        {"label": "OOv2 disputes indexed", "value": stats.get("total_disputes"), "fmt": "int",
         "sub": f"{stats.get('hf_joinable_pct', 100)}% HF-joinable · all adapters"},
        {"label": "Hazard held-out AUC", "value": round(metrics.get("holdout_auc", 0.704), 3),
         "fmt": "num", "sub": "size-only model · calibration-limited"},
        {"label": "Frozen λ*", "value": frozen.get("lambda_star", 0.002), "fmt": "num4",
         "sub": "exit threshold (config/model.yaml)"},
        {"label": "Recon pass rate", "value": round(recon.get("pass_rate", 1.0) * 100, 1),
         "fmt": "pct", "sub": f"{recon.get('eligible', 28482):,} eligible matched"},
    ]
    return {
        "thesis": K.THESIS, "thesis_nuance": K.THESIS_NUANCE, "jump_diffusion": K.JUMP_DIFFUSION,
        "mode": frozen.get("mode", "paper"), "positioning": frozen.get("positioning", "both"),
        "tiles": tiles, "frozen_params": frozen, "frozen_params_source": frozen_src,
        "dataset": {"total_disputes": stats.get("total_disputes"),
                    "hf_joinable_pct": stats.get("hf_joinable_pct"),
                    "by_year": stats.get("by_year"), "by_adapter": stats.get("by_adapter"),
                    "date_min": stats.get("date_min"), "date_max": stats.get("date_max")},
    }


# ---------------------------------------------------------------------------------------------
# λ signal — category base rates (the money chart #1)
# ---------------------------------------------------------------------------------------------
def base_rates() -> dict:
    """Per-category dispute base rate via the REAL data.base_rates.category_base_rate over cached
    inputs; falls back to the published DATASET.md §5b table."""
    counts, csrc = cache.base_rate_counts()
    disp, dsrc = cache.dispute_counts_by_category()
    rows = []
    try:
        from data.base_rates import category_base_rate
        cats = set(counts) | set(disp)
        for cat in cats:
            if cat in ("null", None):
                continue
            br = category_base_rate(cat, disp, counts)
            if br["resolved"] > 0:
                rows.append({k: br[k] for k in ("category", "disputes", "resolved",
                                                "rate", "ci_low", "ci_high")})
        source = "live" if csrc == "live" else "published"
    except Exception:
        rows, source = [], "published"
    if not rows:
        rows = [dict(r) for r in K.BASE_RATES_PUBLISHED]
        source = "published"
    rows.sort(key=lambda r: r["rate"], reverse=True)
    top, bot = rows[0], rows[-1]
    ratio = (top["rate"] / bot["rate"]) if bot["rate"] > 0 else None
    return {"source": source, "rows": rows,
            "headline": (f"{top['category']} is ~{ratio:.0f}× more dispute-prone than "
                         f"{bot['category']}" if ratio else "")}


# ---------------------------------------------------------------------------------------------
# live λ engine — score a market (the interactive wired-to-product proof)
# ---------------------------------------------------------------------------------------------
def score_market(*, category: str, fill_count: int, price: float, proposer: str | None = None,
                 inventory: float = 0.0, horizon_days: float = 7.0) -> dict:
    from config.loader import load_config
    from estimators.hazard import (feature_row, market_size_feature,
                                    proposer_reliability_feature)
    from estimators.lambda_engine import category_base_rate as le_base_rate, estimate_lambda
    from estimators.sigma import category_price_prior
    from execution.loop import forgone_rewards_if_exit, should_exit
    from pricing.quote import (QuoteParams, compute_quote, diffusion_spread_logit,
                               jump_premium_logit)

    cache.install_offline_di()
    cfg = load_config()
    counts, _ = cache.base_rate_counts()
    disp, _ = cache.dispute_counts_by_category()
    price = min(max(float(price), 0.01), 0.99)

    # --- assemble the point-in-time-safe features via the REAL feature functions ---
    br = le_base_rate(category, disp, counts)  # {rate, ci_low, ci_high, disputes, resolved}
    ms = market_size_feature(int(fill_count))
    pr = proposer_reliability_feature(proposer, cache.disputes_by_proposer())
    feats = feature_row(category_base_rate=br["rate"], market_size=ms,
                        proposer_reliability=pr, latency_anomaly=0.0)
    feats.update({"category": category, "price": price})

    # --- the real λ estimate (hazard-driven jump if the model loaded) ---
    model = cache.load_hazard_model()
    out = estimate_lambda("live", feats, dispute_counts=disp, model=model, kappa_loss=cfg.kappa_loss)

    # --- the real σ prior + A-S quote (+ spread decomposition) ---
    sigma = category_price_prior(cache.sigma_prior(), category, price) or cfg.sigma_ref
    P: QuoteParams = cfg.quote
    bid, ask = compute_quote(price, inventory, sigma, horizon_days, lam=out.lambda_jump,
                             e_loss=out.e_loss, jump_drift=out.jump_drift, params=P)
    diffusion_logit = diffusion_spread_logit(P.gamma, sigma, max(horizon_days, P.min_horizon), P.k)
    jump_logit = jump_premium_logit(P.kappa, out.lambda_jump, out.e_loss)

    # --- the real reward-aware exit gate, evaluated at this inventory ---
    from execution.paper import SIM_MICRO
    reduce_size = abs(inventory) * cfg.reduce_fraction
    e_jump_loss_usd = abs(inventory) * out.e_loss * price * (1.0 - price)
    spread_cost = 0.5 * (ask - bid) * reduce_size
    forgone = forgone_rewards_if_exit({
        "mid": price, "our_bid": bid, "our_ask": ask, "bid_size": cfg.quote_size,
        "ask_size": cfg.quote_size, "max_incentive_spread": SIM_MICRO["max_incentive_spread"],
        "reward_min_size": SIM_MICRO["min_order_size"],
        "rewards_daily_rate_usd": SIM_MICRO.get("rewards_daily_rate_usd", 0.0)})
    would_exit = should_exit(out.lambda_jump, cfg.lambda_star, e_jump_loss_usd, forgone,
                             spread_cost, proposal_detected=False)

    return {
        "inputs": {"category": category, "fill_count": int(fill_count), "price": price,
                   "proposer": proposer, "inventory": inventory, "horizon_days": horizon_days},
        "features": {"category_base_rate": br["rate"], "market_size": round(ms, 4),
                     "proposer_reliability": round(pr, 4), "latency_anomaly": 0.0},
        "base_rate": {"rate": br["rate"], "ci_low": br["ci_low"], "ci_high": br["ci_high"],
                      "disputes": br["disputes"], "resolved": br["resolved"]},
        "lambda": {"lambda_select": out.lambda_select, "lambda_jump": out.lambda_jump,
                   "jump_drift": out.jump_drift, "e_loss": out.e_loss,
                   "ci_low": out.ci_low, "ci_high": out.ci_high,
                   "model": "hazard" if model is not None else "base_rate"},
        "quote": {"mid": price, "bid": round(bid, 4), "ask": round(ask, 4),
                  "spread": round(ask - bid, 4), "sigma": round(sigma, 5),
                  "diffusion_logit": round(diffusion_logit, 5), "jump_logit": round(jump_logit, 6),
                  "jump_share": round(jump_logit / (diffusion_logit + jump_logit), 4)
                  if (diffusion_logit + jump_logit) > 0 else 0.0},
        "exit_gate": {"lambda_jump": out.lambda_jump, "lambda_star": cfg.lambda_star,
                      "e_jump_loss_usd": round(e_jump_loss_usd, 4),
                      "forgone_rewards": round(forgone, 4), "spread_cost": round(spread_cost, 4),
                      "would_exit": bool(would_exit),
                      "reason": _gate_reason(out.lambda_jump, cfg.lambda_star, e_jump_loss_usd,
                                             forgone, spread_cost, inventory)},
    }


def _gate_reason(lam, lam_star, e_loss, forgone, spread, inventory) -> str:
    if inventory == 0:
        return "flat — no inventory at risk; the gate only fires against an open position."
    trig = lam > lam_star
    if not trig:
        return f"λ_jump {lam:.4f} ≤ λ* {lam_star} — dispute intensity below the exit threshold; hold & farm."
    if e_loss > forgone + spread:
        return f"E[jump loss] ${e_loss:.2f} > forgone rewards ${forgone:.2f} + haircut ${spread:.2f} → EXIT."
    return f"E[jump loss] ${e_loss:.2f} ≤ forgone ${forgone:.2f} + haircut ${spread:.2f} — rewards worth more; hold."


# ---------------------------------------------------------------------------------------------
# forward-test scenarios (centerpiece)
# ---------------------------------------------------------------------------------------------
def run_session(*, scenario_name: str = "dispute_defense", **kw) -> dict:
    if scenario_name == "live_quoting":
        return scenario.run_live_quoting(n_ticks=int(kw.get("n_ticks", 40)),
                                         n_markets=int(kw.get("n_markets", 4)),
                                         seed=int(kw.get("seed", 7)))
    cache.install_offline_di()
    return scenario.run_dispute_defense(
        category=kw.get("category", "politics"), entry_price=float(kw.get("entry_price", 0.62)),
        inventory=float(kw.get("inventory", 100.0)), dispute_tick=int(kw.get("dispute_tick", 5)),
        gap_logit=float(kw.get("gap_logit", -1.35)), n_ticks=int(kw.get("n_ticks", 13)))


# ---------------------------------------------------------------------------------------------
# edge proof — λ ablation (the money chart #2)
# ---------------------------------------------------------------------------------------------
def ablation() -> dict:
    rows = [dict(r) for r in K.ABLATION_PUBLISHED]
    for r in rows:
        r["arm_label"] = K.ARM_LABELS.get(r["arm"], r["arm"])
    grid = sorted({r["lambda_star"] for r in rows})
    arms = {}
    for r in rows:
        arms.setdefault(r["arm"], {"arm": r["arm"], "arm_label": K.ARM_LABELS.get(r["arm"], r["arm"]),
                                   "points": []})
        arms[r["arm"]]["points"].append({"lambda_star": r["lambda_star"],
                                          "pnl_net_of_rewards": r["pnl_net_of_rewards"],
                                          "sharpe": r["sharpe"]})
    for a in arms.values():
        a["points"].sort(key=lambda p: p["lambda_star"])
    return {"source": "published", "meta": K.ABLATION_META, "lambda_star_grid": grid,
            "arms": list(arms.values()),
            "headline": "Reward-aware surgical exit is the edge; blanket avoidance destroys it.",
            "caveat": ("The live forward test is statistically powerless (~1% dispute rate). This "
                       "is the powered historical counterfactual over 1,409 disputes + matched controls.")}


# ---------------------------------------------------------------------------------------------
# hazard model card (the honest null)
# ---------------------------------------------------------------------------------------------
def hazard() -> dict:
    hz = cache.hazard_models()

    def card(m, label):
        if not m:
            return None
        met = m.get("metrics", {})
        return {"label": label, "coef": m.get("coef"), "intercept": m.get("intercept"),
                "offset": m.get("offset"), "feature_order": m.get("feature_order"),
                "holdout_auc": met.get("holdout_auc"), "brier": met.get("brier"),
                "n": met.get("n"), "positives": met.get("positives"),
                "natural_rate": met.get("natural_rate"), "discriminates": met.get("discriminates")}

    return {"deployed": card(hz.get("deployed"), "Deployed (size-only)"),
            "matched": card(hz.get("matched"), "Fair-controls (CEM-matched)"),
            "matched_eval": card(hz.get("matched_eval"), "Matched held-out eval (proposer null)"),
            "caveat": K.HAZARD_CAVEAT,
            "null_finding": ("proposer_reliability discriminates on raw data (AUC 0.70) but collapses "
                             "to a coin-flip (AUC 0.50) once markets are matched on liquidity — a "
                             "clean null. The deployed model is category base-rate + size only.")}


# ---------------------------------------------------------------------------------------------
# disputes explorer
# ---------------------------------------------------------------------------------------------
def disputes(*, category: str | None = None, adapter: str | None = None, year: int | None = None,
             q: str | None = None, sort: str = "disputeTs", desc: bool = True,
             limit: int = 50, offset: int = 0) -> dict:
    df = cache.disputes_df()
    if df.empty:
        return {"total": 0, "rows": [], "columns": [], "facets": {}}
    view = df
    if category:
        view = view[view["category"] == category]
    if adapter:
        view = view[view["adapter"] == adapter]
    if year and "disputeDate" in view.columns:
        view = view[view["disputeDate"].astype(str).str.startswith(str(year))]
    if q:
        ql = q.lower()
        mask = view["conditionId"].astype(str).str.lower().str.contains(ql)
        if "marketName" in view.columns:
            mask = mask | view["marketName"].astype(str).str.lower().str.contains(ql, na=False)
        if "disputer" in view.columns:
            mask = mask | view["disputer"].astype(str).str.lower().str.contains(ql, na=False)
        view = view[mask]
    total = len(view)
    if sort in view.columns:
        view = view.sort_values(sort, ascending=not desc, na_position="last")
    cols = [c for c in ["conditionId", "marketName", "category", "adapter", "disputeDate",
                        "proposedOutcome", "preDisputePrice", "postDisputePrice",
                        "realizedJumpLogit", "disputer", "proposer", "round"] if c in view.columns]
    page = view.iloc[offset:offset + limit][cols]
    rows = _df_records(page)
    return {"total": int(total), "rows": rows, "columns": cols,
            "facets": _facets(df)}


def _facets(df) -> dict:
    def vc(col):
        return {str(k): int(v) for k, v in df[col].value_counts().items()} if col in df.columns else {}
    years = {}
    if "disputeDate" in df.columns:
        yr = df["disputeDate"].astype(str).str[:4]
        years = {str(k): int(v) for k, v in yr.value_counts().sort_index().items()}
    return {"category": vc("category"), "adapter": vc("adapter"), "year": years}


def _df_records(page) -> list[dict]:
    import numpy as np
    import pandas as pd
    recs = []
    for _, row in page.iterrows():
        d = {}
        for k, v in row.items():
            if isinstance(v, float) and (math.isnan(v) if v == v else True) and pd.isna(v):
                d[k] = None
            elif isinstance(v, (np.integer,)):
                d[k] = int(v)
            elif isinstance(v, (np.floating,)):
                d[k] = None if pd.isna(v) else float(v)
            elif isinstance(v, (np.bool_,)):
                d[k] = bool(v)
            else:
                d[k] = None if (isinstance(v, float) and pd.isna(v)) else v
        recs.append(d)
    return recs


# ---------------------------------------------------------------------------------------------
# recon (data integrity) + sigma surface
# ---------------------------------------------------------------------------------------------
def recon() -> dict:
    stats = cache.dataset_stats()
    r = dict(stats.get("recon") or {})
    r.setdefault("excluded", {"no_ground_truth": r.get("no_ground_truth")})
    return {"recon": r, "by_adapter": stats.get("by_adapter"),
            "by_category": stats.get("by_category_joinable"),
            "total_disputes": stats.get("total_disputes"),
            "hf_joinable_pct": stats.get("hf_joinable_pct"),
            "note": ("Reconciliation is 100% on the ELIGIBLE set — indexer finalOutcome vs the "
                     "on-chain payout vector — with counted exclusion buckets (pending / in-dispute "
                     "/ reorg / unsupported-adapter / no-ground-truth), not a flat 100%.")}


def sigma_surface() -> dict:
    pts = cache.sigma_prior()
    out = []
    for p in pts:
        try:
            out.append({"category": p["category"], "price": round(float(p["price"]), 4),
                        "sigma": round(float(p["sigma"]), 5)})
        except Exception:
            continue
    cats = sorted({p["category"] for p in out})
    return {"points": out, "categories": cats, "n": len(out),
            "note": "Belief-volatility (logit-space σ) prior by category × price level — the σ estimator's shrink target."}
