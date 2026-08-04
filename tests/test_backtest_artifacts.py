"""Regression tests that PIN the committed edge-proof artifacts, so an estimator/quoter change can't
silently move the headline numbers (REPORT.md §7 #14 / §5.9 / notes/05: "no regression test pins the
ablation numbers"). These read the SHIPPED artifacts (the same ones the UI serves), not a recompute.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "forwardtest" / "results"
FROZEN_LAMBDA_STAR = 0.002  # config/model.yaml lambda_star


def _newest(pattern: str) -> Path | None:
    files = sorted(RESULTS.glob(pattern))
    return files[-1] if files else None


# ---------------------------------------------------------------------------------------------
# 1) the counterfactual λ-ablation (replay_ablation) — the pre-registered arm ordering
# ---------------------------------------------------------------------------------------------
def test_ablation_arm_ordering_pinned():
    """At the frozen λ*, the reward-aware surgical exit beats always-hold beats blanket-avoid:
    lambda_jump > diffusion_only > lambda_select on pnl_net_of_rewards. This is THE headline claim;
    if a refit flips it, this test must go red (not the dashboard silently)."""
    art = _newest("replay_ablation_*.json")
    if art is None:
        pytest.skip("no committed replay_ablation artifact in this checkout")
    rows = json.loads(art.read_text())["results"]
    at = {r["arm"]: r["pnl_net_of_rewards"]
          for r in rows if abs(r["lambda_star"] - FROZEN_LAMBDA_STAR) < 1e-9}
    assert {"lambda_jump", "diffusion_only", "lambda_select"} <= set(at), at
    assert at["lambda_jump"] > at["diffusion_only"] > at["lambda_select"], at


# ---------------------------------------------------------------------------------------------
# 2) the clean-USD backtest (replay_full) — the Δ edge the Edge-proof UI leads with
# ---------------------------------------------------------------------------------------------
def test_backtest_delta_edge_is_positive_and_significant():
    """The committed clean-USD backtest must show the λ-jump surgical exit avoiding directional loss
    vs always-hold — a strictly positive Δ whose bootstrap 95% CI excludes zero. Absolute pnl_usd is
    negative by design (no reward income); the SIGNAL is this Δ. Served by /api/backtest, so we read it
    through the same service the UI uses."""
    if _newest("replay_full_*.json") is None:
        pytest.skip("no committed replay_full artifact in this checkout")
    from webapp.backend import services

    d = services.backtest_full()
    assert d["available"] is True
    delta = d["headline"]["delta_jump_minus_diffusion"]
    assert delta is not None and "ci_low" in delta and "ci_high" in delta, delta
    # well-formed CI bracketing the point estimate
    assert delta["ci_low"] <= delta["pnl_usd"] <= delta["ci_high"], delta
    # the edge: strictly positive and significant (CI excludes zero)
    assert delta["pnl_usd"] > 0, delta
    assert delta["ci_low"] > 0, delta


def test_backtest_pnl_rows_are_finite():
    """Every arm × λ* row carries finite pnl_usd / sharpe / drawdown (the to_prob overflow that used to
    crash replay_full would surface here as inf/nan)."""
    import math

    art = _newest("replay_full_*.json")
    if art is None:
        pytest.skip("no committed replay_full artifact in this checkout")
    rows = json.loads(art.read_text())["results"]
    assert rows
    for r in rows:
        for k in ("pnl_usd", "sharpe_cross", "sharpe_daily_ann", "max_drawdown_usd", "win_rate"):
            assert math.isfinite(r[k]), (r["arm"], r["lambda_star"], k, r[k])
