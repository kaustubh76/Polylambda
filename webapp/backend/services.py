"""Service layer: each function is a thin wrapper that calls the REAL engine / reads a REAL
artifact and returns a JSON-able dict. No domain logic is reimplemented here — see the endpoint↔
function map in webapp/README.md.
"""
from __future__ import annotations

import math
import time

from . import cache
from . import constants as K
from . import scenario


# ---------------------------------------------------------------------------------------------
# merged dispute view — the frozen released parquet UNIONED with the live indexer feed, so the
# explorer / analytics / overview reflect the freshest available disputes and self-heal as the
# indexer catches up (they degrade to just the parquet whenever the indexer is stale/unreachable).
# ---------------------------------------------------------------------------------------------
_MERGED_TTL = 15.0  # seconds — the union is cheap but the live fetch shouldn't run per keystroke
_merged_cache: dict = {"until": 0.0, "df": None}


def _merged_disputes_df(include_live: bool = True):
    import pandas as pd

    base = cache.disputes_df()
    if not include_live or base.empty:
        return base
    now = time.monotonic()
    if _merged_cache["df"] is not None and _merged_cache["until"] > now:
        return _merged_cache["df"]
    df = base
    try:
        from . import live
        rows = live.recent_disputes(limit=200)
        if rows:
            live_df = pd.DataFrame(rows)
            for col in base.columns:               # align live rows to the released schema
                if col not in live_df.columns:
                    live_df[col] = None
            live_df = live_df[list(base.columns) + [c for c in live_df.columns if c not in base.columns]]
            combined = pd.concat([base, live_df], ignore_index=True, sort=False)
            if "conditionId" in combined and "disputeTs" in combined:
                # parquet rows come first → keep="first" prefers the enriched release row over its
                # unenriched live twin; genuinely-new live disputes survive.
                combined = combined.drop_duplicates(subset=["conditionId", "disputeTs"], keep="first")
            df = combined
    except Exception:
        df = base
    _merged_cache.update(until=now + _MERGED_TTL, df=df)
    return df


def _dataset_date_bounds(df) -> tuple[str | None, str | None]:
    if df is None or df.empty or "disputeDate" not in df.columns:
        return None, None
    s = df["disputeDate"].dropna().astype(str)
    return (s.min(), s.max()) if len(s) else (None, None)


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
    # date bounds computed from the LIVE-merged dataset (not the frozen stats.json literal), so the
    # headline "latest dispute" reflects the freshest available data instead of a stale hardcoded date.
    d_min, d_max = _dataset_date_bounds(_merged_disputes_df())
    d_min = d_min or stats.get("date_min")
    d_max = d_max or stats.get("date_max")
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
                    "date_min": d_min, "date_max": d_max},
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
                                         seed=int(kw.get("seed", 7)),
                                         source=str(kw.get("source", "synthetic")),
                                         hazard=bool(kw.get("hazard", False)))
    cache.install_offline_di()
    return scenario.run_dispute_defense(
        category=kw.get("category", "politics"), entry_price=float(kw.get("entry_price", 0.62)),
        inventory=float(kw.get("inventory", 100.0)), dispute_tick=int(kw.get("dispute_tick", 5)),
        gap_logit=float(kw.get("gap_logit", -1.35)), n_ticks=int(kw.get("n_ticks", 13)))


# ---------------------------------------------------------------------------------------------
# edge proof — λ ablation (the money chart #2)
# ---------------------------------------------------------------------------------------------
def ablation(live: bool = False) -> dict:
    source = "published"
    grid_rows = None
    live_error = None
    if live:
        # attempt the real powered replay (heavy: HF fill tape); fall back to the published artifact,
        # telling the truth about WHY it fell back so the UI's SourceTag/caveat is honest.
        import os
        url = os.environ.get("INDEXER_GRAPHQL_URL") or os.environ.get("ENVIO_GRAPHQL_URL")
        if not url:
            live_error = "no INDEXER_GRAPHQL_URL configured on this host"
        else:
            try:
                from forwardtest.replay_ablation import run_replay
            except Exception as e:  # noqa: BLE001 — slim deploy image omits sklearn/HF replay deps
                run_replay = None
                live_error = f"replay deps not installed in this image ({e.__class__.__name__})"
            if run_replay is not None:
                try:
                    grid = [0.0005, 0.001, 0.002, 0.005, 0.01]
                    grid_rows = _ablation_rows_from_replay(run_replay(url, grid))
                    if grid_rows:
                        source = "live"
                    else:
                        live_error = "live replay returned no rows"
                except Exception as e:  # noqa: BLE001
                    grid_rows = None
                    live_error = f"live replay failed: {e}"
    if grid_rows:
        rows = grid_rows                                  # source already "live"
    else:
        full_rows = _ablation_full_rows()
        if full_rows:
            rows, source = full_rows, "replay"            # a real committed powered-replay artifact
        else:
            rows = [dict(r) for r in K.ABLATION_PUBLISHED]  # last-resort hardcoded 3-arm constants
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
    out = {"source": source, "meta": K.ABLATION_META, "lambda_star_grid": grid,
           "arms": list(arms.values()),
           "headline": "Reward-aware surgical exit is the edge; blanket avoidance destroys it.",
           "caveat": ("The live forward test is statistically powerless (~1% dispute rate). This "
                      "is the powered historical counterfactual over 1,409 disputes + matched controls.")}
    if live_error:
        out["live_error"] = live_error
    return out


def _ablation_full_rows() -> list[dict] | None:
    """The richer real ablation artifact, in priority order:
      1. .data_cache/webapp/ablation_full.json — the precomputed 4-arm (incl. hazard) grid
         (precompute.build_ablation_full, needs the indexer + heavy deps).
      2. the newest forwardtest/results/replay_ablation_*.json — a committed real powered-replay
         result (its `results` list carries {arm, lambda_star, pnl_net_of_rewards, sharpe}).
    Returns None if neither is present → callers fall back to the published 3-arm constants."""
    try:
        data = cache._load_json(cache.WEBAPP_CACHE / "ablation_full.json")
        if isinstance(data, list) and data:
            return [dict(r) for r in data]
    except Exception:
        pass
    try:
        results_dir = cache.PROJECT_ROOT / "forwardtest" / "results"
        files = sorted(results_dir.glob("replay_ablation_*.json"))
        if files:
            data = cache._load_json(files[-1])  # newest by name (dated YYYY-MM-DD)
            rows = data.get("results") if isinstance(data, dict) else None
            if isinstance(rows, list) and rows:
                return [{"arm": r["arm"], "lambda_star": r["lambda_star"],
                         "pnl_net_of_rewards": r.get("pnl_net_of_rewards", r.get("pnl", 0.0)),
                         "sharpe": r.get("sharpe", 0.0)}
                        for r in rows if "arm" in r and "lambda_star" in r]
    except Exception:
        pass
    return None


def _ablation_rows_from_replay(res) -> list[dict] | None:
    """Flatten a live AblationResult into the same row shape the UI consumes. Best-effort."""
    try:
        rows = []
        for arm in getattr(res, "arms", []) or []:
            name = arm.get("arm") if isinstance(arm, dict) else getattr(arm, "arm", None)
            pts = arm.get("points") if isinstance(arm, dict) else getattr(arm, "points", [])
            for p in pts or []:
                rows.append({"arm": name, "lambda_star": p["lambda_star"],
                             "pnl_net_of_rewards": p.get("pnl_net_of_rewards", p.get("pnl", 0.0)),
                             "sharpe": p.get("sharpe", 0.0)})
        return rows or None
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------
# live reconciliation — recon/check.run_recon against the indexer + on-chain payout vectors
# ---------------------------------------------------------------------------------------------
def recon_live() -> dict:
    import os
    base = recon()
    url = os.environ.get("INDEXER_GRAPHQL_URL") or os.environ.get("ENVIO_GRAPHQL_URL")
    # accept every RPC env name used across the repo/deploy configs (AMOY_RPC_URL is what fly/render set)
    rpc = (os.environ.get("POLYGON_RPC") or os.environ.get("RPC_URL")
           or os.environ.get("AMOY_RPC_URL") or os.environ.get("POLYGON_RPC_URL") or "")
    if not url:
        base["source"] = "published"
        base["live_error"] = "no INDEXER_GRAPHQL_URL configured on this host"
        return base
    try:
        from recon.check import run_recon
        rep = run_recon(url, rpc)
        r = {
            "pass_rate": rep.pass_rate, "eligible": rep.eligible, "matched": rep.matched,
            "no_ground_truth": rep.excluded_no_ground_truth,
            "excluded": {
                "pending": rep.excluded_pending, "in_dispute": rep.excluded_in_dispute,
                "reorg_window": rep.excluded_reorg_window,
                "unsupported_adapter": rep.excluded_unsupported_adapter,
                "no_ground_truth": rep.excluded_no_ground_truth,
            },
        }
        return {"recon": r, "by_adapter": base["by_adapter"], "by_category": base["by_category"],
                "total_disputes": base["total_disputes"], "hf_joinable_pct": base["hf_joinable_pct"],
                "note": base["note"], "source": "live", "mismatches": len(rep.mismatches or [])}
    except Exception as e:  # noqa: BLE001
        base["source"] = "published"
        base["live_error"] = str(e)
        return base


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
                "trained_at": m.get("trained_at"),
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
    df = _merged_disputes_df()
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
                        "realizedJumpLogit", "disputer", "proposer", "round", "source"]
            if c in view.columns]
    page = view.iloc[offset:offset + limit][cols]
    rows = _df_records(page)
    # enrich each row with HF market context (resolution outcome, end date) for the detail modal
    ctx = cache.dispute_market_context()
    if ctx:
        for r in rows:
            hit = ctx.get(r.get("conditionId"))
            if hit:
                r["hfResolved"] = hit.get("resolved")
                r["hfResolvedOutcome"] = hit.get("resolvedOutcome")
                r["hfEndDate"] = hit.get("endDate")
                if not r.get("category") and hit.get("category"):
                    r["category"] = hit.get("category")
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
# ---------------------------------------------------------------------------------------------
# HF backbone surfaces (the dataset that powers the whole stack, finally visible in the UI)
# ---------------------------------------------------------------------------------------------
def hf_overview(live: bool = False) -> dict:
    """The HF backbone overview (resolution mix, markets-by-year, category counts, coverage). Served
    from the shipped precomputed cache; `live=True` recomputes from the HF Hub when HF_TOKEN + network
    allow (guarded + timed out by the route), else falls back to the cache."""
    base = cache.hf_overview()
    out = dict(base) if base else {}
    out["source"] = "cache"
    if live:
        import os
        if not os.environ.get("HF_TOKEN"):
            out["live_error"] = "no HF_TOKEN configured — showing the shipped cache"
            return out
        try:
            from webapp.backend.precompute import build_hf_overview
            build_hf_overview(force=True)
            cache.hf_overview.cache_clear()
            fresh = cache.hf_overview()
            if fresh:
                out = dict(fresh); out["source"] = "live"
        except Exception as e:  # noqa: BLE001
            out["live_error"] = f"live HF query failed: {e}"
    return out


def hf_markets(*, q: str | None = None, category: str | None = None, sort: str = "startDate",
               desc: bool = True, limit: int = 50, offset: int = 0) -> dict:
    """Browse recent Polymarket markets from the HF dataset (filter by text/category, sort, paginate)."""
    data = cache.hf_markets()
    rows = list(data.get("markets") or [])
    if category:
        rows = [r for r in rows if r.get("category") == category]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in str(r.get("marketName", "")).lower()
                or ql in str(r.get("marketSlug", "")).lower()
                or ql in str(r.get("conditionId", "")).lower()]
    if sort in ("startDate", "endDate", "category", "marketName", "resolvedOutcome"):
        rows.sort(key=lambda r: (r.get(sort) is None, r.get(sort) or ""), reverse=bool(desc))
    total = len(rows)
    cats = sorted({r.get("category") for r in (data.get("markets") or []) if r.get("category")})
    page = rows[offset:offset + limit]
    return {"total": total, "rows": page, "categories": cats, "n_cached": data.get("n", 0),
            "note": data.get("note", "")}


def recon() -> dict:
    stats = cache.dataset_stats()
    r = dict(stats.get("recon") or {})
    r.setdefault("excluded", {"no_ground_truth": r.get("no_ground_truth")})
    return {"recon": r, "by_adapter": stats.get("by_adapter"),
            "by_category": stats.get("by_category_joinable"),
            "total_disputes": stats.get("total_disputes"),
            "hf_joinable_pct": stats.get("hf_joinable_pct"), "source": "published",
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


# ---------------------------------------------------------------------------------------------
# proposer leaderboard — the raw proposer_reliability signal (before the CEM-matched null)
# ---------------------------------------------------------------------------------------------
def proposers(limit: int = 15) -> dict:
    by = cache.disputes_by_proposer() or {}
    rows = sorted(({"proposer": k, "disputes": int(v)} for k, v in by.items() if k and k != "null"),
                  key=lambda r: r["disputes"], reverse=True)[: max(1, int(limit))]
    return {"rows": rows, "total_proposers": len(by),
            "note": ("Disputes attributed to each proposer address across the released layer — the raw "
                     "proposer_reliability signal, which discriminates on raw data (AUC~0.70) but collapses "
                     "to a coin-flip once markets are CEM-matched on liquidity (see the model card).")}


# ---------------------------------------------------------------------------------------------
# dispute anatomy — distributions over the full released parquet
# ---------------------------------------------------------------------------------------------
def disputes_analytics(bins: int = 24) -> dict:
    import numpy as np
    df = _merged_disputes_df()
    if df.empty:
        return {"n": 0, "histogram": [], "scatter": [], "by_round": {}, "by_outcome": {}}
    out: dict = {"n": int(len(df))}
    if "realizedJumpLogit" in df.columns:
        mag = df["realizedJumpLogit"].dropna().astype(float).abs()
        if len(mag):
            hi = float(min(mag.max(), 3.0)) or 1.0
            counts, edges = np.histogram(mag, bins=int(bins), range=(0.0, hi))
            out["histogram"] = [{"x0": round(float(edges[i]), 3), "x1": round(float(edges[i + 1]), 3),
                                 "n": int(counts[i])} for i in range(len(counts))]
            out["jump_stats"] = {"mean": round(float(mag.mean()), 4), "median": round(float(mag.median()), 4),
                                 "sd": round(float(mag.std()), 4), "n": int(len(mag))}
    if "preDisputePrice" in df.columns and "postDisputePrice" in df.columns:
        sc = df[["preDisputePrice", "postDisputePrice"]].dropna().astype(float)
        if len(sc) > 600:
            # seed keyed to the row count (not a frozen 7) so the sampled cloud reshuffles whenever
            # the dataset grows — deterministic per-dataset, but visibly not a static snapshot.
            sc = sc.sample(600, random_state=len(sc) % (2**31))
        out["scatter"] = [{"pre": round(float(a), 4), "post": round(float(b), 4)}
                          for a, b in sc.itertuples(index=False)]
    if "round" in df.columns:
        out["by_round"] = {str(int(k)): int(v) for k, v in
                           df["round"].dropna().astype(int).value_counts().sort_index().items()}
    if "proposedOutcome" in df.columns:
        out["by_outcome"] = {str(k): int(v) for k, v in df["proposedOutcome"].value_counts().items()
                             if str(k) not in ("nan", "None")}
    return out


# ---------------------------------------------------------------------------------------------
# interactive A-S quote curve vs inventory (the pricing/quote.compute_quote anatomy)
# ---------------------------------------------------------------------------------------------
def quote_curve(*, category: str = "politics", price: float = 0.62, horizon_days: float = 5.0,
                inv_min: float = -200.0, inv_max: float = 200.0, steps: int = 41) -> dict:
    from config.loader import load_config
    from estimators.hazard import feature_row, market_size_feature
    from estimators.lambda_engine import category_base_rate as le_base_rate, estimate_lambda
    from estimators.sigma import category_price_prior
    from pricing.quote import compute_quote

    cache.install_offline_di()
    cfg = load_config()
    price = min(max(float(price), 0.01), 0.99)
    counts, _ = cache.base_rate_counts()
    disp, _ = cache.dispute_counts_by_category()
    br = le_base_rate(category, disp, counts)
    feats = feature_row(category_base_rate=br["rate"], market_size=market_size_feature(800),
                        proposer_reliability=0.0, latency_anomaly=0.0)
    feats.update({"category": category, "price": price})
    out = estimate_lambda("live", feats, dispute_counts=disp, model=cache.load_hazard_model(),
                          kappa_loss=cfg.kappa_loss)
    sigma = category_price_prior(cache.sigma_prior(), category, price) or cfg.sigma_ref
    n = max(2, int(steps))
    pts = []
    for i in range(n):
        q = inv_min + (inv_max - inv_min) * i / (n - 1)
        bid, ask = compute_quote(price, q, sigma, horizon_days, lam=out.lambda_jump, e_loss=out.e_loss,
                                 jump_drift=out.jump_drift, params=cfg.quote)
        pts.append({"inventory": round(q, 1), "bid": round(bid, 4), "ask": round(ask, 4),
                    "mid": round((bid + ask) / 2, 4)})
    return {"points": pts, "mid": price, "sigma": round(sigma, 5), "lambda_jump": out.lambda_jump,
            "category": category, "horizon_days": horizon_days}
