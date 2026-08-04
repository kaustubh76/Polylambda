"""
replay_full — the FULL-PnL historical simulation (v2 of the edge proof).

Where replay_ablation.py is a reduced-form counterfactual of the EXIT POLICY (fixed 10-token
position, reward-proxy accounting, gate fed the REALIZED jump), this module simulates the actual
product over the same historical universe:

  * the PRODUCTION quoter (`pricing.quote.compute_quote`) with the frozen params,
  * the PRODUCTION sizing / time-decaying position cap / defensive light-quoting
    (the exact formulas of execution/loop.py tick()),
  * the PRODUCTION exit gate (`execution.loop.should_exit`) fed the lambda-implied EXPECTED
    loss — NO hindsight: the gate never sees the realized jump or the dispute timestamp,
  * queue-pessimistic tape fills: a resting bid fills only when a historical print goes STRICTLY
    through it (price-equal prints never fill — we rest behind the queue), the tape analogue of
    execution/paper.py's ConservativeFillModel,
  * clean USD accounting: PnL = cash + inventory x mark. NO reward income is folded in
    (mirrors the live engine: sim_reward_score is a diagnostic, never PnL).

Honest simplifications (documented in REPORT.md section 4.3):
  * mid = EWMA of wash-filtered trade prints — the HF dataset has no historical order book, so
    depth-weighted fair value (estimators/fair_value.py) cannot be computed; the tilt is omitted.
  * T-t uses the end of the fill tape as the resolution proxy.
  * no historical per-market reward params exist, so forgone_rewards_if_exit returns 0 and the
    gate reduces to E[jump loss] > spread cost — the same honest degradation as the zero-rewards
    testnet (notes/13). This REMOVES the reward brake, so lambda-on arms exit more readily than
    a rewards-live deployment would.

Run:  DATA_SOURCE=hf python -m forwardtest.replay_full [--limit N] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

MID_EWMA_ALPHA = 0.30        # trade-print mid smoothing (no book -> no depth-weighted mid)
SIGMA_REFRESH_EVERY = 25     # recompute sigma every N tape events (trailing window below)
SIGMA_WINDOW = 200           # trailing fills fed to estimators.sigma.estimate_sigma_from_fills
SIGMA_PRIOR = 0.15           # same default prior as MarketState.sigma_prior
LAMBDA_STAR_GRID = [0.0005, 0.002, 0.01]
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 20260802


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


# ----------------------------------------------------------------------------------------------
# 1) decision path: one chronological pass per market -> (ts, px, size, mid, sigma, T_t) events
# ----------------------------------------------------------------------------------------------
def build_path(fills: list[dict], cfg) -> list[tuple]:
    """Precompute the per-event decision inputs shared by every arm/lambda_star policy."""
    from estimators.sigma import estimate_sigma_from_fills, wash_filter

    clean = wash_filter(fills, min_size=1.0)
    if len(clean) < 2:
        return []
    end_ts = float(clean[-1]["timestamp"])
    mid = float(clean[0]["price"])
    sigma = SIGMA_PRIOR
    path = []
    for i, f in enumerate(clean):
        px, ts = float(f["price"]), float(f["timestamp"])
        mid = (1 - MID_EWMA_ALPHA) * mid + MID_EWMA_ALPHA * px
        if i % SIGMA_REFRESH_EVERY == 0 and i >= cfg.min_trades_for_sigma:
            sigma = estimate_sigma_from_fills(
                clean[max(0, i - SIGMA_WINDOW):i], prior=SIGMA_PRIOR, b=cfg.ewma_b,
                min_trades=cfg.min_trades_for_sigma, strength=cfg.shrinkage_strength)
        T_t = max((end_ts - ts) / 86400.0, 0.0)
        path.append((ts, px, float(f["size"]), mid, sigma, T_t))
    return path


# ----------------------------------------------------------------------------------------------
# 2) one policy simulation over a precomputed path
# ----------------------------------------------------------------------------------------------
def simulate(path: list[tuple], lam: float, lambda_star: float, cfg, *, lam_on: bool) -> dict:
    """Event-driven sim of the production quote/size/cap/exit logic. Returns per-market results.

    Fill rule (queue-pessimistic): a print STRICTLY below our resting bid fills the bid at OUR
    price (someone sold through the level); strictly above the ask fills the ask. Price-equal
    prints never fill. Fill size = min(quoted size, print size).
    """
    from execution.loop import forgone_rewards_if_exit, should_exit
    from pricing.quote import compute_quote

    inventory = cash = 0.0
    defensive = False
    n_exits = n_fills = 0
    turnover = 0.0
    bid = ask = None
    bid_size = ask_size = 0.0
    day_equity: dict[int, float] = {}
    mid = path[0][3] if path else 0.5

    for ts, px, sz, mid, sigma, T_t in path:
        # --- 1) fills against the quotes standing from the previous event ---
        if bid is not None and bid_size > 0 and px < bid:
            take = min(bid_size, sz)
            inventory += take
            cash -= take * bid
            turnover += take * bid
            n_fills += 1
        elif ask is not None and ask_size > 0 and px > ask:
            take = min(ask_size, sz)
            inventory -= take
            cash += take * ask
            turnover += take * ask
            n_fills += 1

        # --- 2) reward-aware exit gate (lambda-ON arms only), EXPECTED loss — no hindsight ---
        if lam_on and inventory != 0.0 and bid is not None and ask is not None:
            e_loss = cfg.kappa_loss * lam                       # lambda_engine: e_loss = kappa*lam
            e_jump_usd = abs(inventory) * e_loss * mid * (1.0 - mid)
            reduce_size = abs(inventory) * cfg.reduce_fraction
            spread_cost = 0.5 * (ask - bid) * reduce_size
            forgone = forgone_rewards_if_exit({"mid": mid, "our_bid": bid, "our_ask": ask,
                                               "bid_size": bid_size, "ask_size": ask_size})
            if should_exit(lam, lambda_star, e_jump_usd, forgone, spread_cost, False):
                half = 0.5 * (ask - bid)
                px_exit = mid - half if inventory > 0 else mid + half   # taker at the touch
                delta = -reduce_size if inventory > 0 else reduce_size
                cash += -delta * px_exit
                inventory += delta
                defensive = True
                n_exits += 1

        # --- 3) requote with the production quoter + sizing + time-decaying position cap ---
        lam_q = lam if lam_on else 0.0
        e_loss_q = cfg.kappa_loss * lam_q
        drift = math.copysign(e_loss_q, _logit(mid)) if lam_q > 0 else 0.0
        try:
            b, a = compute_quote(mid, inventory, sigma, T_t, lam=lam_q, e_loss=e_loss_q,
                                 jump_drift=drift, params=cfg.quote)
        except (ValueError, OverflowError):
            b = a = None
        if b is not None:
            risk_scale = (min(max(cfg.sigma_ref / max(sigma, 1e-9), cfg.size_floor), 1.0)
                          / (1.0 + cfg.size_lambda_k * lam_q))
            size = cfg.quote_size * risk_scale * (cfg.light_factor if defensive else 1.0)
            horizon = cfg.inventory_cap_horizon_days
            pos_cap = cfg.quote.base_inventory_cap * (min(1.0, T_t / horizon) if horizon > 0 else 1.0)
            bid, ask = b, a
            bid_size = size if inventory < pos_cap else 0.0
            ask_size = size if inventory > -pos_cap else 0.0
        else:
            bid = ask = None
            bid_size = ask_size = 0.0

        day_equity[int(ts // 86400)] = cash + inventory * mid

    pnl = cash + inventory * mid                                # settle at the last event mark
    return {"pnl": pnl, "n_exits": n_exits, "n_fills": n_fills, "turnover": turnover,
            "day_equity": day_equity}


# ----------------------------------------------------------------------------------------------
# 3) portfolio metrics
# ----------------------------------------------------------------------------------------------
def _sharpe_cross(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    var = sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)
    return mu / math.sqrt(var) if var > 0 else 0.0


def portfolio_daily(per_market: list[dict]) -> list[float]:
    """Global daily portfolio equity: each market contributes 0 before its first event, its
    last-known equity while active (carried forward), and its final PnL after its tape ends."""
    all_days: set[int] = set()
    prepped = []
    for m in per_market:
        de = m["day_equity"]
        if not de:
            continue
        days = sorted(de)
        prepped.append((days, de, m["pnl"]))
        all_days.update(days)
    if not prepped:
        return []
    days_sorted = sorted(all_days)
    equity = []
    for d in days_sorted:
        tot = 0.0
        for days, de, final in prepped:
            if d < days[0]:
                continue
            if d >= days[-1]:
                tot += final
            else:
                tot += de[days[bisect_right(days, d) - 1]]
        equity.append(tot)
    return equity


def timeseries_stats(equity: list[float]) -> tuple[float, float]:
    """(annualized daily Sharpe, max drawdown in USD) from the daily equity curve."""
    if len(equity) < 3:
        return 0.0, 0.0
    rets = [equity[i] - equity[i - 1] for i in range(1, len(equity))]
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    sharpe = (mu / math.sqrt(var)) * math.sqrt(365.0) if var > 0 else 0.0
    peak, max_dd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        max_dd = max(max_dd, peak - e)
    return sharpe, max_dd


def bootstrap_delta(pnl_a: list[float], pnl_b: list[float],
                    n: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED) -> dict:
    """Market-level bootstrap 95% CI on sum(pnl_b) - sum(pnl_a) (paired by market)."""
    deltas = [b - a for a, b in zip(pnl_a, pnl_b)]
    rng = random.Random(seed)
    m = len(deltas)
    sums = sorted(sum(deltas[rng.randrange(m)] for _ in range(m)) for _ in range(n))
    return {"delta": sum(deltas), "ci_low": sums[int(0.025 * n)], "ci_high": sums[int(0.975 * n)],
            "n_bootstrap": n}


# ----------------------------------------------------------------------------------------------
# 4) the run
# ----------------------------------------------------------------------------------------------
def run_full(limit: int | None = None) -> dict:
    from config.loader import load_config
    from data.fills import fetch_fills_hf
    from forwardtest.replay_ablation import load_disputes, power_calc

    cfg = load_config()
    url = os.environ.get("GRAPHQL_URL", "http://localhost:8080/v1/graphql")

    disputes = load_disputes(url)
    dispute_ts = {d["conditionId"]: d["disputeTs"] for d in disputes}
    disputed_ids = list(dict.fromkeys(d["conditionId"] for d in disputes))
    controls: list[str] = []
    if disputed_ids:
        from data.prior_corpus import sampled_condition_ids

        pool = [c for c in sampled_condition_ids(per_category=cfg.control_ratio * len(disputed_ids))
                if c not in set(disputed_ids)]
        controls = pool[: cfg.control_ratio * len(disputed_ids)]
    if limit:
        disputed_ids = disputed_ids[: limit]
        controls = controls[: cfg.control_ratio * limit]

    exp = power_calc(len(disputed_ids) + len(controls), dispute_rate=0.011, resting_fraction=1.0)
    print(f"[replay_full] disputes={len(disputed_ids)} controls={len(controls)} "
          f"power_calc={exp:.2f}")

    # tapes + decision paths
    paths: dict[str, list] = {}
    for i, cid in enumerate(disputed_ids + controls):
        fills = fetch_fills_hf(cid, limit=cfg.fill_limit)
        if not fills:
            continue
        p = build_path(fills, cfg)
        if p:
            paths[cid] = p
        if (i + 1) % 200 == 0:
            print(f"[replay_full] tapes {i + 1}/{len(disputed_ids) + len(controls)}")
    n_disp = sum(1 for c in paths if c in dispute_ts)
    n_ctrl = len(paths) - n_disp
    print(f"[replay_full] paths built: {n_disp} disputed + {n_ctrl} controls")

    # lambda signals (same plumbing as replay_ablation)
    from data.base_rates import category_base_rate, category_counts_hf
    from data.disputes import dispute_counts_by_category
    from data.hf import query, table_path
    from data.metadata import category_case_sql

    counts = category_counts_hf()
    dcounts = dispute_counts_by_category()
    cids = list(paths)
    inl = ",".join(f"'{c}'" for c in cids)
    cat_of = {c: cat for c, cat in query(
        f"SELECT condition, any_value({category_case_sql()}) FROM '{table_path('market_data')}' "
        f"WHERE condition IN ({inl}) GROUP BY condition")}
    lam_sel = {c: category_base_rate(cat_of.get(c, "other"), dcounts, counts)["rate"] for c in cids}

    lam_haz: dict[str, float] | None = None
    from estimators.hazard import load_hazard_model
    haz = load_hazard_model()
    if haz is not None:
        from estimators.hazard import _fill_count_map, market_size_feature

        counts_by_cid = _fill_count_map(cids)
        lam_haz = {c: float(haz.predict_proba(
            [[lam_sel[c], market_size_feature(counts_by_cid.get(c, 0)), 0.0, 0.0]])[0, 1]) for c in cids}

    # arm A (lambda-off) is lambda_star-independent: simulate once
    sim_A = {c: simulate(paths[c], 0.0, 0.0, cfg, lam_on=False) for c in cids}

    results = []
    boot = {}
    for ls in LAMBDA_STAR_GRID:
        per_arm: dict[str, list[dict]] = {"diffusion_only": [sim_A[c] for c in cids]}
        per_arm["lambda_jump"] = [simulate(paths[c], lam_sel[c], ls, cfg, lam_on=True) for c in cids]
        zero = {"pnl": 0.0, "n_exits": 0, "n_fills": 0, "turnover": 0.0, "day_equity": {}}
        per_arm["lambda_select"] = [zero if lam_sel[c] > ls else sim_A[c] for c in cids]
        if lam_haz is not None:
            per_arm["lambda_jump_hazard"] = [simulate(paths[c], lam_haz[c], ls, cfg, lam_on=True)
                                             for c in cids]
        for arm, sims in per_arm.items():
            pnls = [s["pnl"] for s in sims]
            eq = portfolio_daily(sims)
            ts_sharpe, max_dd = timeseries_stats(eq)
            results.append({
                "arm": arm, "lambda_star": ls,
                "pnl_usd": round(sum(pnls), 2),
                "sharpe_cross": round(_sharpe_cross(pnls), 3),
                "sharpe_daily_ann": round(ts_sharpe, 3),
                "max_drawdown_usd": round(max_dd, 2),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
                "n_exits": sum(s["n_exits"] for s in sims),
                "n_fills": sum(s["n_fills"] for s in sims),
                "turnover_usd": round(sum(s["turnover"] for s in sims), 2),
            })
            print(f"  {arm:<19} l*={ls:<7} pnl={sum(pnls):+11.2f} "
                  f"sx={_sharpe_cross(pnls):+.3f} sd={ts_sharpe:+.3f} dd={max_dd:.2f} "
                  f"win={results[-1]['win_rate']:.3f} exits={results[-1]['n_exits']}")
        if ls == cfg.lambda_star:                      # frozen threshold: bootstrap the deltas
            a = [s["pnl"] for s in per_arm["diffusion_only"]]
            boot["lambda_jump_minus_diffusion"] = bootstrap_delta(
                a, [s["pnl"] for s in per_arm["lambda_jump"]])
            if lam_haz is not None:
                boot["lambda_jump_hazard_minus_diffusion"] = bootstrap_delta(
                    a, [s["pnl"] for s in per_arm["lambda_jump_hazard"]])

    return {
        "run_date": date.today().isoformat(),
        "command": "DATA_SOURCE=hf python -m forwardtest.replay_full",
        "meta": {"n_disputes_with_paths": n_disp, "n_controls_with_paths": n_ctrl,
                 "lambda_star_grid": LAMBDA_STAR_GRID, "lambda_star_frozen": cfg.lambda_star,
                 "quote_size": cfg.quote_size, "fill_limit": cfg.fill_limit,
                 "power_calc_expected_disputes": round(exp, 2),
                 "accounting": "pnl = cash + inventory*mark; NO reward income; USD units",
                 "fill_model": "queue-pessimistic tape fills (strict price-through only)",
                 "exit_gate": "expected-loss gate (no hindsight); no historical reward params -> "
                              "gate reduces to E[jump loss] > spread cost"},
        "results": results,
        "bootstrap_frozen_lambda_star": boot,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="cap disputed markets (smoke runs)")
    ap.add_argument("--out", default=None, help="write the artifact JSON here")
    args = ap.parse_args()
    out = run_full(limit=args.limit)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(out, f, indent=1)
        print(f"[replay_full] wrote {args.out}")
    else:
        print(json.dumps(out["results"], indent=1))
