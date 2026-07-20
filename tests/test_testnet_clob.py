"""execution/testnet_clob.py — adapter over an in-memory FakeChain (PolyLambdaMarket semantics).

Covers: pair-collapse into one postQuote, the debounce gas guard, one-sided quote mapping,
Traded-event -> engine-fill conversion (side inversion, tx hash, confirmation lag), the escrow
solvency bound on maxTrade, cancel bookkeeping, resolved -> empty book, and a killed RiskGovernor
silencing all signing while the loop keeps running.
"""
import pytest

from execution.risk import RiskGovernor, RiskLimits
from execution.testnet_chain import FleetMarket
from execution.testnet_clob import TESTNET_MICRO, TestnetClob
from fakes_testnet import FakeChain, FakeSigner

ADDR = "0x" + "aa" * 20


def _mk(tmp_path, chain=None, **clob_kw):
    chain = chain or FakeChain()
    signer = FakeSigner(chain)
    risk = RiskGovernor(RiskLimits(kill_switch_path=str(tmp_path / "KILL")),
                        ledger_dir=str(tmp_path / "risk"), clock=lambda: 1_784_000_000.0)
    fleet = [FleetMarket(address=ADDR, deployed_block=1, category="politics",
                         tracks_cid="0xcid-real", keeper_managed=True)]
    clob = TestnetClob(fleet, signer, chain, risk=risk, confirmations=3, **clob_kw)
    return chain, signer, risk, clob


TOKEN = f"tn-{ADDR[:10].lower()}"


def test_pair_collapses_into_one_postquote(tmp_path):
    chain, signer, _, clob = _mk(tmp_path)
    clob.place(TOKEN, "BUY", 0.60, 0.4, now_ts=2000.0)
    clob.place(TOKEN, "SELL", 0.70, 0.4, now_ts=2000.0)
    clob.step(2000.0)
    assert len(signer.sent) == 1 and signer.sent[0].name == "postQuote"
    assert chain.state["bid"] == 0.60 and chain.state["ask"] == 0.70
    assert chain.state["max_trade"] == pytest.approx(0.4)


def test_debounce_suppresses_tiny_moves_but_age_reposts(tmp_path):
    chain, signer, _, clob = _mk(tmp_path)
    clob.place(TOKEN, "BUY", 0.60, 0.4, now_ts=2000.0)
    clob.place(TOKEN, "SELL", 0.70, 0.4, now_ts=2000.0)
    clob.step(2000.0)
    assert len(signer.sent) == 1
    # tiny move (< min_requote_delta=0.005), same size -> no tx
    clob.place(TOKEN, "BUY", 0.601, 0.4, now_ts=2060.0)
    clob.place(TOKEN, "SELL", 0.699, 0.4, now_ts=2060.0)
    clob.step(2060.0)
    assert len(signer.sent) == 1
    # same quote but standing quote now older than max_quote_age_s (900) -> re-post
    clob.place(TOKEN, "BUY", 0.601, 0.4, now_ts=2000.0 + 901.0)
    clob.place(TOKEN, "SELL", 0.699, 0.4, now_ts=2000.0 + 901.0)
    clob.step(2000.0 + 901.0)
    assert len(signer.sent) == 2


def test_one_sided_quote_maps_missing_side_to_dead_price(tmp_path):
    chain, signer, _, clob = _mk(tmp_path)
    clob.place(TOKEN, "SELL", 0.70, 0.4, now_ts=2000.0)  # inventory-cap gated: no BUY side
    clob.step(2000.0)
    assert len(signer.sent) == 1
    assert chain.state["bid"] == TESTNET_MICRO["tick_size"]  # economically dead bid
    assert chain.state["ask"] == 0.70


def test_escrow_solvency_bounds_max_trade(tmp_path):
    chain = FakeChain(escrow=1.0, total_yes=0.8)             # only 0.2 redeemable headroom
    chain2, signer, _, clob = _mk(tmp_path, chain=chain)
    clob.place(TOKEN, "BUY", 0.60, 0.5, now_ts=2000.0)
    clob.place(TOKEN, "SELL", 0.70, 0.5, now_ts=2000.0)
    clob.step(2000.0)
    assert chain.state["max_trade"] == pytest.approx(0.2)


def test_fills_convert_traded_events_with_confirmation_lag(tmp_path):
    chain, signer, _, clob = _mk(tmp_path)
    clob.place(TOKEN, "BUY", 0.60, 0.4, now_ts=2000.0)
    clob.place(TOKEN, "SELL", 0.70, 0.4, now_ts=2000.0)
    clob.step(2000.0)                                        # posts 0.60/0.70
    chain.user_buy(0.3)                                      # event lands at the new head
    fills = clob.step(2010.0)
    assert fills == []                                       # < 3 confirmations deep yet
    chain.head += 3
    fills = clob.step(2020.0)
    assert len(fills) == 1
    f = fills[0]
    assert f["side"] == "SELL"                               # user bought YES -> engine sold
    assert f["price"] == pytest.approx(0.70)
    assert f["queue_model"] == "onchain" and f["tx"].startswith("0xbuy")
    assert f["token_id"] == TOKEN and f["size"] == pytest.approx(0.3)
    # not double-delivered
    assert clob.step(2030.0) == []
    # user sell converts to an engine BUY at the bid
    chain.user_sell(0.1)
    chain.head += 3
    (f2,) = clob.step(2040.0)
    assert f2["side"] == "BUY" and f2["price"] == pytest.approx(0.60)
    # the tape saw both prints (taker side preserved)
    tape = clob.tape(TOKEN)
    assert [t["side"] for t in tape] == ["BUY", "SELL"]


def test_cancel_is_bookkeeping_only_and_book_reads(tmp_path):
    chain, signer, _, clob = _mk(tmp_path)
    oid = clob.place(TOKEN, "BUY", 0.60, 0.4, now_ts=2000.0)
    clob.cancel([oid])                                       # no chain action, no error
    assert signer.sent == []
    book = clob.get_book(TOKEN)
    assert book["bids"][0][0] == 0.55 and book["asks"][0][0] == 0.65  # the standing chain quote
    chain.state["resolved"] = True
    clob._snap_cache.clear()
    assert clob.get_book(TOKEN) == {"bids": [], "asks": []}  # resolved -> loop holds


def test_flag_dispute_idempotent_and_mapped_by_cid(tmp_path):
    chain, signer, _, clob = _mk(tmp_path)
    out = clob.flag_dispute_for("0xcid-real")
    assert out is not None and chain.state["disputed"] is True
    assert [c.name for c in signer.sent] == ["flagDispute"]
    assert clob.flag_dispute_for("0xcid-real") is None       # idempotent: already disputed
    assert clob.flag_dispute_for("0xunknown") is None        # unmapped cid -> no-op
    assert len(signer.sent) == 1


def test_killed_governor_blocks_all_signing_but_never_raises(tmp_path):
    chain, signer, risk, clob = _mk(tmp_path)
    risk.kill("test")
    clob.place(TOKEN, "BUY", 0.60, 0.4, now_ts=2000.0)
    clob.place(TOKEN, "SELL", 0.70, 0.4, now_ts=2000.0)
    fills = clob.step(2000.0)                                # still runs, returns fills
    assert fills == [] and signer.sent == []
    assert clob.flag_dispute_for("0xcid-real") is None
    assert signer.sent == []
    assert "kill" in clob.last_denied


def test_rpc_errors_feed_breaker_without_crashing(tmp_path):
    chain, signer, risk, clob = _mk(tmp_path)
    signer.fail_next = 1
    clob.place(TOKEN, "BUY", 0.60, 0.4, now_ts=2000.0)
    clob.place(TOKEN, "SELL", 0.70, 0.4, now_ts=2000.0)
    clob.step(2000.0)                                        # send fails -> recorded, no raise
    assert signer.sent == []
    assert risk.status()["consecutive_errors"] >= 1
