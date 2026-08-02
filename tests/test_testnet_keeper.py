"""execution/testnet_keeper.py — the full production loop over the FakeChain, end to end.

Covers: honest session logs (simulated=False throughout), on-chain fill provenance (tx hash,
queue_model="onchain"), the manual-trigger dispute defense (exit record -> exactly one real
flagDispute + defensive light re-quote), ablation-reader compatibility, a killed governor
silencing all signing while ticks continue, and mode-refusal in runner/run_loop.
"""
import pytest

from execution.loop import run_loop
from execution.proposal_feed import ConfirmedProposalDetector
from execution.risk import RiskGovernor, RiskLimits
from execution.testnet_chain import FleetMarket
from execution.testnet_clob import TestnetClob
from execution.testnet_keeper import TestnetKeeper
from fakes_testnet import FakeChain, FakeSigner
from forwardtest import session_log

ADDR = "0x" + "aa" * 20
TOKEN = f"tn-{ADDR[:10].lower()}"
CID = "0xcid-real"


def _keeper(tmp_path, *, chain=None):
    chain = chain or FakeChain()
    signer = FakeSigner(chain)
    risk = RiskGovernor(RiskLimits(kill_switch_path=str(tmp_path / "KILL")),
                        ledger_dir=str(tmp_path / "risk"), clock=lambda: 1_784_000_000.0)
    fleet = [FleetMarket(address=ADDR, deployed_block=1, category="politics",
                         tracks_cid=CID, keeper_managed=True)]
    clob = TestnetClob(fleet, signer, chain, risk=risk, confirmations=3)
    detector = ConfirmedProposalDetector(fleet, fetch=lambda: [],
                                         manual_path=str(tmp_path / "TRIGGERS"))
    k = TestnetKeeper(interval_s=0.0, out_path=str(tmp_path / "session.jsonl"),
                      clob=clob, detector=detector, risk=risk)
    return chain, signer, risk, k


def test_session_log_is_honest_and_fills_carry_tx(tmp_path):
    chain, signer, risk, k = _keeper(tmp_path)
    k.run(n_ticks=2)
    chain.user_buy(0.3)          # a real user trade lands on-chain
    chain.head += 3              # ... and gets confirmation depth
    k.run(n_ticks=2)
    recs = session_log.read(str(tmp_path / "session.jsonl"))
    assert recs, "log written"
    assert all(r["simulated"] is False for r in recs), "testnet mode owns simulated=False"
    assert all(r["mode"] == "testnet" for r in recs)
    fills = [r for r in recs if r["type"] == "fill"]
    assert len(fills) == 1
    f = fills[0]
    assert f["queue_model"] == "onchain" and f["tx"].startswith("0xbuy")
    assert f["side"] == "SELL" and f["inventory_after"] == pytest.approx(-0.3)
    # quotes were signed on-chain (postQuote), debounced across identical ticks
    posted = [c for c in signer.sent if c.name == "postQuote"]
    assert len(posted) >= 1
    types = {r["type"] for r in recs}
    assert {"session_start", "tick", "quote", "session_end"} <= types


def test_manual_trigger_fires_real_dispute_defense_once(tmp_path):
    chain, signer, risk, k = _keeper(tmp_path)
    k.run(n_ticks=1)                      # initial quote posted
    chain.user_buy(0.3)                   # engine now short 0.3 (nonzero inventory)
    chain.head += 3
    k.run(n_ticks=2)                      # fill lands; λ-only exits may fire (politics λ > λ*)...
    assert [c for c in signer.sent if c.name == "flagDispute"] == [], \
        "λ-only exits must NOT burn the market: flagDispute is proposal-triggered only"
    (tmp_path / "TRIGGERS").write_text(CID + "\n")
    k.run(n_ticks=3)                      # confirmed proposal -> exit gate -> REAL defense
    recs = session_log.read(str(tmp_path / "session.jsonl"))
    exits = [r for r in recs if r["type"] == "exit"]
    assert any(r["trigger"] == "proposal" for r in exits)
    # reduce_fraction forced to 0 in testnet mode: no fictional taker-reduce
    assert all(r["inventory_before"] == r["inventory_after"] for r in exits)
    flags = [c for c in signer.sent if c.name == "flagDispute"]
    assert len(flags) == 1, "flagDispute signed exactly once (idempotent afterwards)"
    assert chain.state["disputed"] is True
    flagged = [r for r in recs if r["type"] == "dispute_flagged"]
    assert flagged and flagged[0]["cid"] == CID and flagged[0]["tx"].startswith("0xtx")
    # defensive re-quote: later quote sizes shrink by light_factor
    quotes = [r for r in recs if r["type"] == "quote"]
    defensive = [q for q in quotes if q["defensive"]]
    assert defensive, "re-quoted light while defensive, not vanished"
    normal_sz = max(q["ask_size"] for q in quotes if not q["defensive"])
    assert all(q["ask_size"] < normal_sz for q in defensive)


def test_ablation_reader_parses_testnet_session(tmp_path):
    chain, signer, risk, k = _keeper(tmp_path)
    k.run(n_ticks=2)
    from forwardtest.ablation import run_live_ablation
    out = run_live_ablation(str(tmp_path / "session.jsonl"))
    assert out is not None                # pure reader keyed on type/arm: no schema break


def test_killed_governor_stops_signing_but_ticks_continue(tmp_path):
    chain, signer, risk, k = _keeper(tmp_path)
    risk.kill("test")
    out = k.run(n_ticks=3)
    assert k.ticks_done == 3              # loop kept running
    assert signer.sent == []              # zero signed transactions
    assert risk.status()["halted"] is True
    st = k.status()
    assert st["risk"]["killed"] is True and st["ticks_done"] == 3
    # the λ-on vs λ-off edge is surfaced to the API (per-market + per-arm rollup)
    assert "markets" in st and "per_arm" in st
    assert st["per_arm"].get("lambda_on", {}).get("n_markets", 0) >= 1


def test_status_reports_autostart_and_engine_ready(tmp_path, monkeypatch):
    import execution.testnet_chain as tc
    _, _, _, k = _keeper(tmp_path)
    monkeypatch.setenv("KEEPER_AUTOSTART", "1")
    monkeypatch.setattr(tc, "engine_key", lambda: "0x" + "11" * 32)
    st = k.status()
    assert st["autostart"] is True and st["engine_ready"] is True
    # both off → the two reasons the live keeper wouldn't be signing
    monkeypatch.delenv("KEEPER_AUTOSTART", raising=False)
    monkeypatch.setattr(tc, "engine_key", lambda: None)
    st2 = k.status()
    assert st2["autostart"] is False and st2["engine_ready"] is False


def test_state_persists_across_bursts(tmp_path):
    chain, signer, risk, k = _keeper(tmp_path)
    k.run(n_ticks=1)
    chain.user_buy(0.2)
    chain.head += 3
    k.run(n_ticks=1)
    inv_after_fill = k.markets[0].inventory
    assert inv_after_fill == pytest.approx(-0.2)
    k.run(n_ticks=1)                      # a new burst must NOT rebuild/reset MarketState
    assert k.markets[0].inventory == pytest.approx(inv_after_fill)


def test_background_start_stop(tmp_path):
    chain, signer, risk, k = _keeper(tmp_path)
    k.run(n_ticks=1)                      # synchronous warm-up (estimator/market build is slow)
    k.interval_s = 0.05
    assert k.start_background() is True
    assert k.start_background() is False  # already running
    import time
    time.sleep(0.2)
    assert k.stop(timeout=5.0) is True
    assert k.running is False
    assert k.ticks_done >= 1


def test_runner_refuses_testnet_mode():
    from forwardtest.runner import run
    with pytest.raises(RuntimeError, match="paper"):
        run(mode="testnet", n_ticks=1)


def test_run_loop_refuses_testnet_without_injected_clob():
    with pytest.raises(RuntimeError, match="TestnetClob"):
        run_loop([], mode="testnet", n_ticks=1, interval_s=0.0)


# ---------------------------------------------------------------------------------------------
# session resolution — the dashboard reads the LATEST real on-chain session, live or archived.
# This is what lets a fresh deploy (with no keeper running) still surface a genuine past session
# from the committed forwardtest/results/ log instead of an empty "keeper hasn't run" page.
# ---------------------------------------------------------------------------------------------
import execution.testnet_keeper as tk


def _write_session(path, day_ts=None):
    import json as _json
    with open(path, "w") as fh:
        fh.write(_json.dumps({"type": "session_start", "mode": "testnet", "simulated": False}) + "\n")
        fh.write(_json.dumps({"type": "session_end", "n_disputes_witnessed": 0,
                              "per_market": [], "ticks_done": 1}) + "\n")


def test_market_cap_helpers(monkeypatch):
    from execution.testnet_keeper import _cap_markets, _max_markets_env
    m = ["a", "b", "c", "d", "e", "f"]
    # cap slicing
    assert _cap_markets(m, 0) == m          # 0 = all
    assert _cap_markets(m, 2) == ["a", "b"]  # trimmed to first N (deterministic order)
    assert _cap_markets(m, 99) == m          # cap >= len is a no-op
    assert _cap_markets(m, -1) == m          # negative = all
    # env parsing
    monkeypatch.delenv("KEEPER_MAX_MARKETS", raising=False)
    assert _max_markets_env() == 0
    monkeypatch.setenv("KEEPER_MAX_MARKETS", "1")
    assert _max_markets_env() == 1
    monkeypatch.setenv("KEEPER_MAX_MARKETS", "")     # empty → all
    assert _max_markets_env() == 0
    monkeypatch.setenv("KEEPER_MAX_MARKETS", "junk")  # invalid → all, never raises
    assert _max_markets_env() == 0


def test_session_date_from_path_parses_and_rejects():
    assert tk.session_date_from_path("x/session-testnet-20260721.jsonl") == "2026-07-21"
    assert tk.session_date_from_path("x/session-testnet-bogus.jsonl") is None
    assert tk.session_date_from_path("") is None


def test_latest_session_path_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(tk, "_keeper", None)
    monkeypatch.setattr(tk, "_SESSION_DIRS", (str(tmp_path / "nope"),))
    path, is_live = tk.latest_session_path()
    assert path is None and is_live is False


def test_latest_session_path_falls_back_to_newest_archived(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    d.mkdir()
    old = d / "session-testnet-20260720.jsonl"
    new = d / "session-testnet-20260721.jsonl"
    _write_session(old, None)
    _write_session(new, None)
    monkeypatch.setattr(tk, "_keeper", None)      # no live keeper writing today
    monkeypatch.setattr(tk, "_SESSION_DIRS", (str(d),))
    path, is_live = tk.latest_session_path()
    assert path == str(new)          # newest by embedded date wins
    assert is_live is False          # archived, not the live keeper


def test_latest_session_path_prefers_running_keeper(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    d.mkdir()
    archived = d / "session-testnet-20260721.jsonl"
    live = d / "session-testnet-20260726.jsonl"
    _write_session(archived, None)
    _write_session(live, None)

    class _LiveKeeper:
        out_path = str(live)
        running = True
    monkeypatch.setattr(tk, "_keeper", _LiveKeeper())
    monkeypatch.setattr(tk, "_SESSION_DIRS", (str(d),))
    path, is_live = tk.latest_session_path()
    assert path == str(live) and is_live is True
