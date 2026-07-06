"""forwardtest/runner.py — the paper forward-test harness drives the loop and writes a schema-
complete, deterministic, honest session log (no network; live/paper-live not exercised here)."""
import json

import pytest

from forwardtest.runner import run
from forwardtest.session_log import read


def _run(tmp_path, seed=7, n_ticks=12, n_markets=4):
    out = str(tmp_path / "session.jsonl")
    return run(mode="paper", n_ticks=n_ticks, interval_s=0.0, seed=seed,
               n_markets=n_markets, out_path=out), out


def test_writes_session_log_with_start_and_end(tmp_path):
    summary, out = _run(tmp_path)
    recs = read(out)
    assert sum(1 for r in recs if r["type"] == "session_start") == 1
    assert sum(1 for r in recs if r["type"] == "session_end") == 1
    # the stream in between is the loop's tick/quote (and any fill/exit) records
    assert any(r["type"] == "tick" for r in recs)
    assert any(r["type"] == "quote" for r in recs)


def test_both_arms_present(tmp_path):
    summary, out = _run(tmp_path)
    assert set(summary["per_arm_totals"]) == {"lambda_on", "lambda_off"}
    start = next(r for r in read(out) if r["type"] == "session_start")
    arms = {m["arm"] for m in start["markets"]}
    assert arms == {"lambda_on", "lambda_off"}
    # lambda_on markets carry a resolved lambda; lambda_off markets do not
    for m in start["markets"]:
        if m["arm"] == "lambda_on":
            assert m["lambda_jump"] is not None
        else:
            assert m["lambda_jump"] is None


def test_every_record_is_flagged_simulated(tmp_path):
    _, out = _run(tmp_path)
    recs = read(out)
    assert recs and all(r.get("simulated") is True for r in recs)


def test_pnl_excludes_sim_reward_score(tmp_path):
    """Honesty invariant: equity/P&L is cash + inventory·mark ONLY; the simulated reward score is
    reported separately and never folded in (MarketState:122 / JURISDICTION.md)."""
    summary, _ = _run(tmp_path)
    for row in summary["per_market"]:
        assert row["pnl"] == pytest.approx(row["cash"] + row["inventory"] * row["mark_mid"])
        assert row["pnl"] == pytest.approx(row["equity_mark"])
        assert "sim_reward_score" in row              # present, but a separate field
    for arm in summary["per_arm_totals"].values():
        assert arm["pnl"] == pytest.approx(arm["equity_mark"])


def test_deterministic_under_fixed_seed(tmp_path):
    a, _ = _run(tmp_path / "a", seed=7)
    b, _ = _run(tmp_path / "b", seed=7)
    key = lambda s: [(r["token_id"], r["inventory"], r["cash"], r["pnl"]) for r in s["per_market"]]
    assert key(a) == key(b)


def test_different_seed_changes_the_path(tmp_path):
    a, _ = _run(tmp_path / "a", seed=7)
    b, _ = _run(tmp_path / "b", seed=99)
    # at least the underlying book paths differ (mark mids), even if wide quotes rarely fill
    assert [r["mark_mid"] for r in a["per_market"]] != [r["mark_mid"] for r in b["per_market"]]


def test_live_mode_is_refused(tmp_path):
    with pytest.raises(RuntimeError):
        run(mode="live", n_ticks=1, out_path=str(tmp_path / "x.jsonl"))


def test_uptime_fraction_in_unit_range(tmp_path):
    summary, _ = _run(tmp_path)
    assert 0.0 <= summary["uptime_fraction"] <= 1.0


def test_real_market_builder_uses_real_base_rate_lambda():
    """source='data' routes markets through estimate_lambda with REAL category base rates (the
    engine, no longer bypassed): politics is far more dispute-prone than crypto (diagram ~22×)."""
    pytest.importorskip("duckdb")
    from config.loader import load_config
    from forwardtest.runner import build_markets

    markets = build_markets([{"cid": "0xa", "category": "politics", "price": 0.8},
                             {"cid": "0xb", "category": "crypto", "price": 0.5}])
    assert [m.arm for m in markets] == ["lambda_on", "lambda_off"]
    pol = markets[0]
    assert pol.lam is not None and pol.lam.lambda_jump > 0 and pol.lam.ci_high > pol.lam.ci_low
    assert markets[1].lam is None                              # lambda_off carries no engine output
    crypto = build_markets([{"cid": "0xc", "category": "crypto", "price": 0.5}])[0]
    assert pol.lam.lambda_jump > crypto.lam.lambda_jump        # politics ≫ crypto dispute base rate
    assert pol.sigma_prior == load_config().sigma_ref         # σ prior falls back when no corpus
