"""The dispute-defense scenario — the dashboard's live centerpiece.

Drives the REAL execution loop (`execution.loop.tick`, `should_exit`, `forgone_rewards_if_exit`,
`pricing.quote.compute_quote`) on two mirror markets that are IDENTICAL until a dispute is detected:
one runs the λ-ON reward-aware exit policy, one holds (λ-OFF). The only webapp-layer glue is:
  * ScenarioClob — a PaperClob with a frozen book (no random walk / no incidental fills) plus a
    one-time price GAP at the dispute (a real dispute is a directional jump; DECISIONS.md).
  * a no-sleep driver (run_loop couples its decision-clock step to a wall-clock sleep; we don't
    want to sleep in a request), which reuses run_loop's exact fill-crediting glue.
  * on detection, E[loss|jump] is raised to the calibrated realized-jump magnitude (kappa_loss=0.76,
    data/calibrate.py) — i.e. the posterior once a proposal is actually on-chain (P(jump)→1).

Everything is stamped `simulated: true`. This is an honest, controlled ILLUSTRATION of the exit
mechanism; the statistical edge claim lives in the powered replay ablation (constants.ABLATION_*).
"""
from __future__ import annotations

DAY = 86400.0
DEFAULT_START_TS = 1_780_000_000.0  # matches forwardtest.runner.PAPER_START_TS (~2026-05-27)


def _scenario_clob(token_ids, *, entry_price: float, dispute_tick: int, gap_logit: float):
    from execution.paper import PaperClob, SyntheticBook

    class ScenarioClob(PaperClob):
        def __init__(self):
            super().__init__(token_ids, seed=7)
            for t in token_ids:
                b = SyntheticBook(t, seed=7, p0=entry_price, sigma_step=0.0)
                b.advance = lambda: None            # freeze the latent walk (clean A/B)
                b.taker_prints = lambda now_ts: []  # no incidental fills — inventory only moves on exit
                self.books[t] = b
            self._i = 0

        def step(self, now_ts):
            if self._i == dispute_tick + 1:         # the dispute price GAP (post-detection)
                for b in self.books.values():
                    b.x += gap_logit
            self._i += 1
            return super().step(now_ts)

    return ScenarioClob()


def run_dispute_defense(*, category: str = "politics", entry_price: float = 0.62,
                        inventory: float = 100.0, dispute_tick: int = 5, gap_logit: float = -1.35,
                        n_ticks: int = 13, base_rate: float | None = None) -> dict:
    """Run the A/B and return an animate-ready event stream + summary. Deterministic (seed 7)."""
    from config.loader import load_config
    from estimators.lambda_engine import LambdaOutput
    from execution.loop import MarketState, tick

    cfg = load_config()
    kappa = cfg.kappa_loss
    if base_rate is None:
        base_rate = _category_base_rate_point(category)
    # calm λ (pre-dispute): base-rate jump intensity → E[loss] too small to justify the exit haircut.
    lam_calm = LambdaOutput(base_rate, base_rate, -0.9 * base_rate, kappa * base_rate,
                            base_rate * 0.5, base_rate * 1.5)
    # active λ (on detection): jump is imminent → E[loss|jump] = the calibrated realized-jump magnitude.
    lam_active = LambdaOutput(base_rate, 0.9, -0.9 * 0.9, kappa * 1.0, base_rate * 0.5, base_rate * 1.5)

    markets = [
        MarketState(cid=f"0x{arm}", token_id=f"tok-{arm}", category=category, arm=arm,
                    end_date_ts=DEFAULT_START_TS + 2 * DAY, inventory=inventory,
                    cash=-inventory * entry_price, sigma_prior=cfg.sigma_ref,
                    lam=lam_calm if arm == "lambda_on" else None)
        for arm in ("lambda_on", "lambda_off")
    ]
    by_token = {m.token_id: m for m in markets}
    clob = _scenario_clob([m.token_id for m in markets], entry_price=entry_price,
                          dispute_tick=dispute_tick, gap_logit=gap_logit)

    events: list[dict] = []
    log = lambda rt, **f: events.append({"type": rt, **f})  # noqa: E731

    now = DEFAULT_START_TS
    step_s = 6 * 3600.0
    for i in range(n_ticks):
        for fill in clob.step(now):                      # real run_loop fill-crediting glue
            s = by_token[fill["token_id"]]
            signed = fill["size"] if fill["side"] == "BUY" else -fill["size"]
            s.inventory += signed
            s.cash -= signed * fill["price"]
        if i == dispute_tick:                            # proposal detected → update E[loss|jump]
            by_token["tok-lambda_on"].lam = lam_active
        for s in markets:
            detected = i >= dispute_tick and s.arm == "lambda_on"
            tick(s, clob.get_book(s.token_id), now, cfg, clob, log, proposal_detected=detected)
        now += step_s

    return _summarize(events, markets, dispute_tick=dispute_tick, entry_price=entry_price,
                      inventory=inventory, gap_logit=gap_logit, category=category, cfg=cfg)


def _summarize(events, markets, *, dispute_tick, entry_price, inventory, gap_logit, category, cfg):
    def series(cid):
        out = []
        for r in events:
            if r["type"] == "tick" and r["cid"] == cid:
                out.append({"i": len(out), "mid": round(r["mid"], 5),
                            "inventory": round(r["inventory"], 2),
                            "equity": round(r["equity_mark"], 4),
                            "cash": round(r["cash"], 4)})
        return out

    on, off = series("0xlambda_on"), series("0xlambda_off")
    exits = [{"i": None, "cid": e["cid"], "trigger": e["trigger"],
              "inventory_before": round(e["inventory_before"], 2),
              "inventory_after": round(e["inventory_after"], 2),
              "exit_price": round(e["exit_price"], 4), "haircut_paid": round(e["haircut_paid"], 4),
              "lambda_jump": round(e["lambda_jump"], 4), "lambda_star": e["lambda_star"],
              "e_jump_loss": round(e["e_jump_loss"], 4), "forgone_rewards": round(e["forgone_rewards"], 4)}
             for e in events if e["type"] == "exit"]
    on_final = on[-1]["equity"] if on else 0.0
    off_final = off[-1]["equity"] if off else 0.0
    protected = on_final - off_final
    return {
        "simulated": True,
        "scenario": "dispute_defense",
        "params": {"category": category, "entry_price": entry_price, "inventory": inventory,
                   "dispute_tick": dispute_tick, "gap_tick": dispute_tick + 1,
                   "gap_logit": gap_logit, "lambda_star": cfg.lambda_star, "kappa_loss": cfg.kappa_loss,
                   "n_ticks": len(on)},
        "series": {"lambda_on": on, "lambda_off": off},
        "exits": exits,
        "summary": {
            "on_final_equity": round(on_final, 2), "off_final_equity": round(off_final, 2),
            "protected": round(protected, 2),
            "loss_reduction_pct": round(100 * (1 - abs(on_final) / abs(off_final)), 1)
            if off_final < 0 else 0.0,
            "n_exits": len(exits),
        },
        "narrative": (
            f"Two identical {category} positions ({inventory:.0f} tokens long at "
            f"{entry_price:.2f}) hold together until a dispute is detected at tick {dispute_tick}. "
            f"The λ-ON arm's real should_exit gate fires and surgically reduces before the "
            f"{abs(gap_logit):.2f}-logit price gap; the λ-OFF arm holds and eats it. "
            f"Result: λ-ON ends at {on_final:+.2f} vs λ-OFF at {off_final:+.2f}."
        ),
    }


def _category_base_rate_point(category: str) -> float:
    """The real category dispute base rate (offline via cached inputs); constant fallback."""
    try:
        from data.base_rates import category_base_rate
        from . import cache
        counts, _ = cache.base_rate_counts()
        disp, _ = cache.dispute_counts_by_category()
        return float(category_base_rate(category, disp, counts)["rate"]) or 0.0183
    except Exception:
        return 0.0183


def run_live_quoting(*, n_ticks: int = 40, n_markets: int = 4, seed: int = 7) -> dict:
    """The genuine multi-market paper session via the REAL runner.run — shows the engine quoting
    (mid / σ / spread / size / inventory-cap dynamics). Honest: fills are rare in a driftless sim,
    so this is presented as quoting behavior, not a P&L race."""
    from forwardtest.runner import run

    n_ticks = max(5, min(int(n_ticks), 80))
    n_markets = max(2, min(int(n_markets), 6))
    summary = run(mode="paper", source="synthetic", n_ticks=n_ticks, n_markets=n_markets, seed=seed)
    events = _read_session_log(summary.get("out_path"))
    per_market = {}
    for r in events:
        if r.get("type") == "tick":
            per_market.setdefault(r["cid"], []).append({
                "i": len(per_market.get(r["cid"], [])), "mid": round(r["mid"], 5),
                "sigma": round(r["sigma"], 5), "T_t": round(r["T_t"], 4),
                "best_bid": r["best_bid"], "best_ask": r["best_ask"],
                "spread": round(r["best_ask"] - r["best_bid"], 5),
                "equity": round(r["equity_mark"], 4), "quoting": r["quoting"]})
    quotes = {}
    for r in events:
        if r.get("type") == "quote":
            quotes.setdefault(r["cid"], []).append({
                "i": len(quotes.get(r["cid"], [])), "bid": r["bid"], "ask": r["ask"],
                "bid_size": r["bid_size"], "ask_size": r["ask_size"],
                "risk_scale": round(r.get("risk_scale", 1.0), 4), "pos_cap": round(r.get("pos_cap", 0.0), 2)})
    return {"simulated": True, "scenario": "live_quoting", "summary": summary,
            "series": per_market, "quotes": quotes, "n_fills": sum(1 for r in events if r.get("type") == "fill")}


def _read_session_log(path) -> list[dict]:
    import json
    from .cache import PROJECT_ROOT
    if not path:
        return []
    p = (PROJECT_ROOT / path) if not str(path).startswith("/") else path
    try:
        with open(p) as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception:
        return []
