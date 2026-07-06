"""
runner — forward-test harness (paper / paper-live).

  paper       : fully simulated book + fills.
  paper-live  : read the REAL live book (public WS/REST, no auth), SIMULATE fills locally, place
                NO real orders. Works under any jurisdiction outcome.
  live        : real orders (tiny capital) — JURISDICTION-GATED, see ../JURISDICTION.md.

⚠ Honesty (DECISIONS.md): paper-live CANNOT observe true queue position, fill probability, or
realized rewards/rebates (those need real resting orders). Treat it as LOGIC/microstructure
validation only — never report simulated rewards as P&L. Model a conservative fill (assume you
sit behind all existing same-price depth) calibrated from the real OrderFilled tape.

Start this loop EARLY (~day 9) so 9-10 days of tape accrue before any live ablation.

This module drives execution.loop.run_loop and WRITES the JSONL session log (schema in
forwardtest.session_log); forwardtest.ablation is the pure READER of that log. Both arms
(lambda_on / lambda_off) run side by side so the ablation has arms to split. Paper markets are
independent synthetic books (one seeded path per token_id), so the on/off arms are NOT matched on
an identical price path — the paper run is a machinery/plumbing check; the powered edge proof is
the historical replay (forwardtest.replay_ablation).
"""
from __future__ import annotations

import os

# Synthetic paper universe: (category, dispute base rate). Rates mirror DATASET.md §5b category
# base rates so lambda_jump lands on the real ~0.0004-0.021 scale (some above lambda_star=0.002).
PAPER_UNIVERSE = [
    ("politics", 0.0183),
    ("entertainment", 0.0211),
    ("crypto", 0.00085),
    ("sports", 0.0054),
]

# E[loss|jump] per unit intensity, in logit units (paper stand-in for the replay-calibrated
# kappa_loss in estimators.lambda_engine). Small on purpose: exits fire only when E[jump loss]
# actually beats forgone rewards + spread, which is rare over a short paper window — honest.
_PAPER_KAPPA_LOSS = 1.5

# The paper DECISION clock origin (fixed, so time-to-resolution and the inventory cap are
# deterministic — no wall-clock in the sim). Markets resolve a few days out from here; run_loop is
# handed the same origin so now = PAPER_START_TS + i*interval_s.
PAPER_START_TS = 1_780_000_000.0  # ~2026-05-27


def _paper_lambda(category: str, rate: float, direction: float):
    """A deterministic LambdaOutput for a paper market (no network, no HF scan)."""
    from estimators.lambda_engine import LambdaOutput

    return LambdaOutput(
        lambda_select=rate,
        lambda_jump=rate,
        jump_drift=direction * rate,
        e_loss=_PAPER_KAPPA_LOSS * rate,
        ci_low=max(rate * 0.5, 0.0),
        ci_high=rate * 1.5,
    )


def _build_paper_markets(n_markets: int, seed: int):
    """Build n_markets MarketState objects, each assigned to EXACTLY ONE arm (alternating
    lambda_on / lambda_off) — mirrors a real live ablation, where a given market's book can only be
    quoted under one policy. Unique cid + token_id per market keeps cid→arm 1:1."""
    from execution.loop import MarketState

    day = 86400.0
    markets = []
    for i in range(n_markets):
        category, rate = PAPER_UNIVERSE[i % len(PAPER_UNIVERSE)]
        direction = 1.0 if i % 2 == 0 else -1.0
        arm = "lambda_on" if i % 2 == 0 else "lambda_off"
        end_ts = PAPER_START_TS + (7 + i) * day     # resolves 7..N days out from the decision origin
        markets.append(MarketState(
            cid=f"0xpaper{i:02d}", token_id=f"paper-{i:02d}", category=category, arm=arm,
            end_date_ts=end_ts, sigma_prior=0.15,
            lam=_paper_lambda(category, rate, direction) if arm == "lambda_on" else None,
        ))
    return markets


def select_real_markets(n_markets: int) -> list[dict]:
    """Pick real markets to forward-test: the released disputed markets (they carry a category + a
    pre-dispute price). Returns [{cid, category, price}] — the offline, no-network market source that
    lets the loop run on REAL λ and σ priors (the book is still simulated in paper mode)."""
    import duckdb

    pq = "dataset_release/polymarket-oov2-disputes-v1/disputes.parquet"
    rows = duckdb.sql(
        f"SELECT conditionId, category, preDisputePrice FROM '{pq}' "
        f"WHERE hf_joinable AND preDisputePrice IS NOT NULL "
        f"ORDER BY conditionId LIMIT {int(n_markets)}").fetchall()
    return [{"cid": c, "category": cat or "other", "price": float(p)} for c, cat, p in rows]


def build_markets(market_rows: list[dict], *, hazard_model=None, sigma_corpus=None, cfg=None,
                  dispute_counts=None, seed: int = 7):
    """Build MarketState objects with REAL estimator inputs (Panel L①): λ via estimate_lambda (real
    category base rates, or the hazard logistic when a model is passed) and σ prior via the
    category×price corpus (falls back to cfg.sigma_ref when no corpus). Each market → exactly one arm
    (alternating), mirroring a live ablation. Pure given (market_rows, model, corpus, cfg)."""
    from config.loader import load_config
    from estimators.lambda_engine import estimate_lambda
    from estimators.sigma import category_price_prior
    from execution.loop import MarketState

    if cfg is None:
        cfg = load_config()
    feats_idx = {}
    if hazard_model is not None:                       # attach the exact structural features
        from estimators.hazard import market_feature_dicts

        feats_idx = market_feature_dicts([r["cid"] for r in market_rows])

    day = 86400.0
    markets = []
    for i, row in enumerate(market_rows):
        cid, cat, price = row["cid"], row.get("category", "other"), float(row.get("price", 0.5))
        features = dict(feats_idx.get(cid, {}))
        features.update({"category": cat, "price": price})
        lam = estimate_lambda(cid, features, dispute_counts=dispute_counts, model=hazard_model,
                              kappa_loss=cfg.kappa_loss)
        sig_prior = (category_price_prior(sigma_corpus, cat, price)
                     if sigma_corpus else cfg.sigma_ref)
        arm = "lambda_on" if i % 2 == 0 else "lambda_off"
        markets.append(MarketState(
            cid=cid, token_id=f"real-{i:03d}-{cid[:10]}", category=cat, arm=arm,
            end_date_ts=PAPER_START_TS + (7 + i) * day, sigma_prior=sig_prior,
            lam=lam if arm == "lambda_on" else None))
    return markets


def _mark_mid(book: dict) -> float:
    """Mark = midpoint of the best bid/ask; 0.5 if the book is empty."""
    if book.get("bids") and book.get("asks"):
        return 0.5 * (book["bids"][0][0] + book["asks"][0][0])
    return 0.5


def run(mode: str = "paper", markets: list | None = None, *, n_ticks: int = 20,
        interval_s: float = 0.0, out_path: str | None = None, seed: int = 7,
        n_markets: int = 4, cfg=None, source: str = "synthetic", hazard: bool = False) -> dict:
    """Drive execution.loop.run_loop, log every session event, and return the session summary.

    Logs session_start (config + per-market snapshot) → the loop's tick/quote/fill/exit stream →
    session_end (per-market + per-arm totals). P&L is cash + inventory·mark ONLY; the accrued
    sim_reward_score is reported separately and NEVER folded into any P&L figure (MarketState:122).
    """
    from config.loader import load_config
    from execution.loop import run_loop
    from forwardtest import session_log

    if cfg is None:
        cfg = load_config()
    if mode not in ("paper", "paper-live"):
        # live has no v1 loop adapter (JURISDICTION.md); paper-live still simulates fills.
        raise RuntimeError(f"runner.run supports paper | paper-live only (got {mode!r}); "
                           "live is jurisdiction-gated at the clob layer")

    if markets is None:
        if source == "data":
            from data.prior_corpus import load_sigma_prior
            from estimators.hazard import load_hazard_model

            hm = load_hazard_model() if hazard else None
            markets = build_markets(select_real_markets(n_markets), hazard_model=hm,
                                    sigma_corpus=load_sigma_prior(), cfg=cfg, seed=seed)
        else:
            markets = _build_paper_markets(n_markets, seed)
    token_ids = [m.token_id for m in markets]

    if mode == "paper":
        from execution.paper import PaperClob

        clob = PaperClob(token_ids, seed=seed)
    else:  # paper-live: REAL public read path, simulated conservative fills
        from execution.paper import PaperLiveClob

        clob = PaperLiveClob(token_ids)

    if out_path is None:
        out_path = os.path.join(".data_cache", "sessions", f"session-{mode}-s{seed}-n{n_ticks}.jsonl")
    fh = session_log.open_log(out_path)

    # a log(type, **fields) closure bound to mode; also tallies quotes for uptime_fraction
    counters = {"quotes": 0}

    def log(record_type: str, **fields):
        if record_type == "quote":
            counters["quotes"] += 1
        return session_log.append(fh, record_type, mode=mode, **fields)

    try:
        log("session_start",
            config={"lambda_star": cfg.lambda_star, "quote_size": cfg.quote_size,
                    "positioning": cfg.positioning, "gamma": cfg.quote.gamma, "k": cfg.quote.k,
                    "kappa": cfg.quote.kappa},
            arm_rule="lambda_on evaluates the reward-aware exit gate; lambda_off never does",
            n_ticks=n_ticks, interval_s=interval_s, seed=seed,
            markets=[{"cid": m.cid, "token_id": m.token_id, "category": m.category,
                      "end_date_ts": m.end_date_ts, "arm": m.arm,
                      "lambda_select": (m.lam.lambda_select if m.lam else None),
                      "lambda_jump": (m.lam.lambda_jump if m.lam else None),
                      "ci_low": (m.lam.ci_low if m.lam else None),
                      "ci_high": (m.lam.ci_high if m.lam else None),
                      "micro": clob.get_micro(m.token_id), "seed": seed} for m in markets])

        run_loop(markets, mode=mode, n_ticks=n_ticks, interval_s=interval_s, clob=clob,
                 log=log, cfg=cfg, start_ts=PAPER_START_TS)

        # --- settle marks and roll up per-market / per-arm totals ---
        per_market = []
        per_arm: dict[str, dict] = {}
        for m in markets:
            mid = _mark_mid(clob.get_book(m.token_id))
            equity = m.cash + m.inventory * mid       # P&L vs a flat/zero start
            row = {"cid": m.cid, "token_id": m.token_id, "arm": m.arm, "category": m.category,
                   "inventory": m.inventory, "cash": m.cash, "mark_mid": mid,
                   "equity_mark": equity, "pnl": equity, "sim_reward_score": m.sim_reward_score,
                   "n_exits": m.n_exits}
            per_market.append(row)
            a = per_arm.setdefault(m.arm, {"n_markets": 0, "equity_mark": 0.0, "cash": 0.0,
                                           "inventory": 0.0, "sim_reward_score": 0.0, "n_exits": 0})
            a["n_markets"] += 1
            a["equity_mark"] += equity
            a["pnl"] = a.get("pnl", 0.0) + equity      # pnl excludes sim_reward_score by construction
            a["cash"] += m.cash
            a["inventory"] += m.inventory
            a["sim_reward_score"] += m.sim_reward_score
            a["n_exits"] += m.n_exits

        denom = max(n_ticks * len(markets), 1)
        uptime = min(counters["quotes"] / denom, 1.0)
        summary = {"mode": mode, "n_ticks": n_ticks, "n_markets": len(markets), "seed": seed,
                   "out_path": out_path, "per_market": per_market, "per_arm_totals": per_arm,
                   "n_disputes_witnessed": 0, "uptime_fraction": uptime}
        log("session_end", per_market=per_market, per_arm_totals=per_arm,
            n_disputes_witnessed=0, uptime_fraction=uptime)
        return summary
    finally:
        fh.close()


if __name__ == "__main__":
    import json
    import sys

    def _arg(flag, default):
        return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default

    mode = _arg("--mode", "paper")
    summary = run(mode=mode, n_ticks=int(_arg("--ticks", 20)),
                  interval_s=float(_arg("--interval", 0.0)), seed=int(_arg("--seed", 7)),
                  n_markets=int(_arg("--markets", 4)), out_path=_arg("--out", None),
                  source=_arg("--source", "synthetic"), hazard=("--hazard" in sys.argv))
    print(json.dumps({k: v for k, v in summary.items() if k != "per_market"}, indent=2))
    print(f"session log -> {summary['out_path']}")
