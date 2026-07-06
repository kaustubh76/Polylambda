"""forwardtest/ablation.py — the live ablation is a pure, crash-tolerant reader of the session log
that splits lambda_on vs lambda_off and ALWAYS reports the underpowered caveat."""
import json

import pytest

from forwardtest.ablation import MIN_DISPUTES_FOR_SIGNAL, run_live_ablation
from forwardtest.runner import run
from forwardtest import session_log


def _write(path, records):
    fh = session_log.open_log(str(path))
    for rt, fields in records:
        session_log.append(fh, rt, mode="paper", **fields)
    fh.close()
    return str(path)


def test_reads_runner_output_and_splits_arms(tmp_path):
    out = str(tmp_path / "s.jsonl")
    run(mode="paper", n_ticks=10, interval_s=0.0, seed=7, n_markets=4, out_path=out)
    ab = run_live_ablation(out)
    assert ab["lambda_on"]["arm"] == "lambda_on"
    assert ab["lambda_off"]["arm"] == "lambda_off"
    assert set(ab["delta_on_minus_off"]) == {"pnl", "n_exits", "sim_reward_score"}
    assert ab["n_disputes"] == 0
    assert ab["underpowered"] is True
    assert "UNDERPOWERED" in ab["caveat"] and "replay" in ab["caveat"]


def test_delta_and_dispute_count_from_session_end(tmp_path):
    per_market = [
        {"cid": "0x1", "arm": "lambda_on", "equity_mark": 12.0, "cash": 12.0,
         "inventory": 0.0, "sim_reward_score": 3.0},
        {"cid": "0x2", "arm": "lambda_off", "equity_mark": 5.0, "cash": 5.0,
         "inventory": 0.0, "sim_reward_score": 4.0},
    ]
    path = _write(tmp_path / "e.jsonl", [
        ("session_start", {"markets": []}),
        ("fill", {"cid": "0x1", "arm": "lambda_on", "side": "BUY", "price": 0.4, "size": 10}),
        ("exit", {"cid": "0x1", "arm": "lambda_on", "trigger": "lambda"}),
        ("fill", {"cid": "0x2", "arm": "lambda_off", "side": "SELL", "price": 0.6, "size": 10}),
        ("dispute_witnessed", {"cid": "0x1", "source": "test", "note": "n/a"}),
        ("session_end", {"per_market": per_market, "per_arm_totals": {},
                         "n_disputes_witnessed": 2, "uptime_fraction": 1.0}),
    ])
    ab = run_live_ablation(path)
    assert ab["lambda_on"]["pnl"] == pytest.approx(12.0)
    assert ab["lambda_off"]["pnl"] == pytest.approx(5.0)
    assert ab["lambda_on"]["n_fills"] == 1 and ab["lambda_on"]["n_exits"] == 1
    assert ab["lambda_off"]["n_fills"] == 1 and ab["lambda_off"]["n_exits"] == 0
    assert ab["delta_on_minus_off"]["pnl"] == pytest.approx(7.0)
    assert ab["delta_on_minus_off"]["n_exits"] == 1
    # sim_reward_score is reported in the delta but is NEVER part of pnl
    assert ab["delta_on_minus_off"]["sim_reward_score"] == pytest.approx(-1.0)
    assert ab["n_disputes"] == 2
    assert ab["underpowered"] is True


def test_underpowered_flips_when_enough_disputes(tmp_path):
    path = _write(tmp_path / "p.jsonl", [
        ("session_start", {"markets": []}),
        ("session_end", {"per_market": [], "per_arm_totals": {},
                         "n_disputes_witnessed": MIN_DISPUTES_FOR_SIGNAL, "uptime_fraction": 1.0}),
    ])
    ab = run_live_ablation(path)
    assert ab["n_disputes"] == MIN_DISPUTES_FOR_SIGNAL
    assert ab["underpowered"] is False
    assert "UNDERPOWERED" not in ab["caveat"]


def test_crash_tolerant_fallback_without_session_end(tmp_path):
    """A session killed before session_end: rollup falls back to last-seen tick equity per cid,
    attributed to the arm via the arm-tagged quote/fill records."""
    path = _write(tmp_path / "c.jsonl", [
        ("session_start", {"markets": []}),
        ("quote", {"cid": "0xA", "arm": "lambda_on", "bid": 0.4, "ask": 0.5}),
        ("tick", {"cid": "0xA", "mid": 0.45, "equity_mark": 8.0, "cash": 8.0, "inventory": 0.0}),
        ("quote", {"cid": "0xB", "arm": "lambda_off", "bid": 0.4, "ask": 0.5}),
        ("tick", {"cid": "0xB", "mid": 0.45, "equity_mark": 3.0, "cash": 3.0, "inventory": 0.0}),
    ])
    ab = run_live_ablation(path)
    assert ab["lambda_on"]["equity_mark"] == pytest.approx(8.0)
    assert ab["lambda_off"]["equity_mark"] == pytest.approx(3.0)
    assert ab["delta_on_minus_off"]["pnl"] == pytest.approx(5.0)
    assert ab["underpowered"] is True


def test_torn_trailing_line_is_skipped(tmp_path):
    path = str(tmp_path / "t.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps({"type": "session_start", "markets": [], "simulated": True}) + "\n")
        f.write('{"type": "session_end", "per_market": [], "n_disputes_witnessed": 0')  # torn
    ab = run_live_ablation(path)  # must not raise
    assert ab["n_records"] == 1
