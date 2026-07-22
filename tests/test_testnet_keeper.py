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
