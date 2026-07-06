"""execution/clob.py — read-path normalization (fixture-tested; live shapes ISP-blocked from this
network, see the module docstring) + the LiveGateError write-path gate."""
import os

import pytest

import execution.clob as clob
from execution.clob import LiveGateError, _round_to_tick

GATE_KEYS = ("MODE", "JURISDICTION_ACK", "MAX_CAPITAL_USDC", "BUILDER_CODE")


def _swap_http(fake):
    orig = clob._http_get
    clob._http_get = fake
    return orig


def _clear_env():
    return {k: os.environ.pop(k, None) for k in GATE_KEYS}


def _restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# --- read path -------------------------------------------------------------------------------

def test_read_book_normalizes_strings_and_sorts_best_first():
    fake_raw = {"bids": [{"price": "0.44", "size": "50"}, {"price": "0.45", "size": "100"}],
                "asks": [{"price": "0.47", "size": "80"}, {"price": "0.46", "size": "20"}]}
    orig = _swap_http(lambda url, params=None, timeout=15: fake_raw)
    try:
        book = clob.read_book("123")
        assert book["bids"][0] == (0.45, 100.0)      # best bid first (desc)
        assert book["asks"][0] == (0.46, 20.0)       # best ask first (asc)
        assert all(isinstance(p, float) for p, _ in book["bids"] + book["asks"])
    finally:
        clob._http_get = orig


def test_get_market_microstructure_merges_clob_and_gamma():
    def fake(url, params=None, timeout=15):
        if "tick-size" in url:
            return {"minimum_tick_size": "0.001"}
        return [{"conditionId": "0xc", "question": "Q?", "endDate": "2026-12-31T00:00:00Z",
                 "orderMinSize": 5, "rewardsMinSize": 50, "rewardsMaxSpread": 3.5,
                 "clobRewards": '[{"rewardsDailyRate": 40}, {"rewardsDailyRate": 10}]',
                 "negRisk": True, "gameStartTime": None}]
    orig = _swap_http(fake)
    try:
        m = clob.get_market_microstructure("123")
        assert m["tick_size"] == 0.001
        assert m["condition_id"] == "0xc" and m["neg_risk"] is True
        assert m["min_order_size"] == 5.0 and m["reward_min_size"] == 50.0
        assert m["max_incentive_spread"] == pytest.approx(0.035)   # 3.5% -> fraction
        assert m["rewards_daily_rate_usd"] == pytest.approx(50.0)  # summed across programs
    finally:
        clob._http_get = orig


def test_read_trades_filters_since_and_sorts():
    fake_raw = [{"price": "0.5", "size": "10", "side": "buy", "timestamp": 200},
                {"price": "0.4", "size": "5", "side": "sell", "timestamp": 100},
                {"price": "0.6", "size": "1", "side": "buy", "timestamp": 300}]
    orig = _swap_http(lambda url, params=None, timeout=15: fake_raw)
    try:
        tr = clob.read_trades("123", since_ts=150)
        assert [t["timestamp"] for t in tr] == [200, 300]          # filtered + oldest-first
        assert tr[0]["side"] == "BUY" and tr[0]["price"] == 0.5
    finally:
        clob._http_get = orig


# --- write path: the gate --------------------------------------------------------------------

def test_write_path_gated_by_default():
    saved = _clear_env()
    try:
        for fn, args in ((clob.place_order, ("123", "BUY", 0.5, 10.0)),
                         (clob.cancel_orders, (["a"],)),
                         (clob.wrap_usdce_to_pusd, (10.0,))):
            with pytest.raises(LiveGateError):
                fn(*args)
    finally:
        _restore_env(saved)


def test_gate_requires_all_three_conditions():
    saved = _clear_env()
    try:
        os.environ["MODE"] = "live"
        with pytest.raises(LiveGateError):                          # no ACK yet
            clob.place_order("123", "BUY", 0.5, 10.0)
        os.environ["JURISDICTION_ACK"] = "RESOLVED_SEE_JURISDICTION_MD"
        with pytest.raises(LiveGateError):                          # no capital cap yet
            clob.place_order("123", "BUY", 0.5, 10.0)
    finally:
        _restore_env(saved)


def test_place_order_builds_v2_order_no_nonce_ms_timestamp_and_cap():
    saved = _clear_env()
    posted = {}

    class FakeClient:
        def post_order(self, order):
            posted.update(order)
            return {"order_id": "oid-1"}

    orig_client = clob._live_client
    orig_spent = clob._live_notional_spent
    try:
        os.environ.update({"MODE": "live", "JURISDICTION_ACK": "RESOLVED_SEE_JURISDICTION_MD",
                           "MAX_CAPITAL_USDC": "50", "BUILDER_CODE": "0xbeef"})
        clob._live_client = lambda: FakeClient()
        clob._live_notional_spent = 0.0
        oid = clob.place_order("123", "buy", 0.4567, 10.0, tick_size=0.01)
        assert oid == "oid-1"
        assert posted["price"] == pytest.approx(0.45)               # bid rounded DOWN to tick
        assert "nonce" not in posted                                # CLOB V2: NO nonce
        assert isinstance(posted["timestamp_ms"], int)              # ms-timestamp uniqueness
        assert posted["post_only"] is True and posted["builder_code"] == "0xbeef"
        with pytest.raises(LiveGateError):                          # cumulative cap enforced
            clob.place_order("123", "buy", 0.5, 1000.0, tick_size=0.01, min_order_size=1)
    finally:
        clob._live_client = orig_client
        clob._live_notional_spent = orig_spent
        _restore_env(saved)


def test_round_to_tick_directions():
    assert _round_to_tick(0.4567, 0.01, "BUY") == pytest.approx(0.45)   # bid down
    assert _round_to_tick(0.4512, 0.01, "SELL") == pytest.approx(0.46)  # ask up
