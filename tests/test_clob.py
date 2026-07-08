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


def test_gate_rejects_non_finite_or_nonpositive_cap():
    """NaN poisons every `> cap` comparison to False — a junk cap would silently DISABLE the
    notional bound while the gate reads satisfied. The gate must fail closed instead."""
    saved = _clear_env()
    try:
        os.environ.update({"MODE": "live", "JURISDICTION_ACK": "RESOLVED_SEE_JURISDICTION_MD"})
        for bad in ("nan", "inf", "-5", "0", "junk"):
            os.environ["MAX_CAPITAL_USDC"] = bad
            with pytest.raises(LiveGateError, match="finite positive"):
                clob.place_order("123", "BUY", 0.5, 10.0)
            with pytest.raises(LiveGateError, match="finite positive"):
                clob.wrap_usdce_to_pusd(1.0)
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


# --- write path: the implemented live client (py-sdk adapter, fully mocked — no network/SDK) ----

LIVE_ENV = {"MODE": "live", "JURISDICTION_ACK": "RESOLVED_SEE_JURISDICTION_MD",
            "MAX_CAPITAL_USDC": "50"}


def _fake_sdk_modules(monkeypatch, created):
    """Install a fake `polymarket` package in sys.modules so _live_client never imports the real
    pinned SDK (hermetic even where polymarket-client IS installed)."""
    import sys
    import types

    pkg = types.ModuleType("polymarket")
    errors = types.ModuleType("polymarket.errors")

    class RateLimitError(Exception):
        pass

    errors.RateLimitError = RateLimitError

    class FakeSecureClient:
        @classmethod
        def create(cls, *, private_key, wallet=None, credentials=None):
            created.update(private_key=private_key, wallet=wallet, credentials=credentials)
            return cls()

    class FakeApiKeyCreds:
        def __init__(self, *, key, secret, passphrase):
            created["creds_kwargs"] = (key, secret, passphrase)

    pkg.SecureClient = FakeSecureClient
    pkg.ApiKeyCreds = FakeApiKeyCreds
    pkg.errors = errors
    monkeypatch.setitem(sys.modules, "polymarket", pkg)
    monkeypatch.setitem(sys.modules, "polymarket.errors", errors)
    return RateLimitError


def test_live_client_is_gated_and_requires_wallet_key(monkeypatch):
    saved = _clear_env()
    try:
        monkeypatch.setattr(clob, "_client", None)
        with pytest.raises(LiveGateError):                      # defense in depth: gated by itself
            clob._live_client()
        os.environ.update(LIVE_ENV)
        monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
        with pytest.raises(LiveGateError, match="WALLET_PRIVATE_KEY"):
            clob._live_client()
    finally:
        _restore_env(saved)


def test_live_client_builds_sdk_adapter_and_caches(monkeypatch):
    saved = _clear_env()
    created = {}
    _fake_sdk_modules(monkeypatch, created)
    try:
        os.environ.update(LIVE_ENV)
        monkeypatch.setenv("WALLET_PRIVATE_KEY", "0xkey")
        for k in ("CLOB_API_KEY", "CLOB_API_SECRET", "CLOB_API_PASSPHRASE", "POLY_FUNDER_WALLET"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(clob, "_client", None)
        client = clob._live_client()
        assert isinstance(client, clob._SdkOrderAdapter)
        assert created["private_key"] == "0xkey"
        assert created["credentials"] is None                   # no env creds → L1 EIP-712 derivation
        assert clob._live_client() is client                    # constructed once, then cached

        # explicit L2 env creds short-circuit the derivation
        monkeypatch.setattr(clob, "_client", None)
        monkeypatch.setenv("CLOB_API_KEY", "k")
        monkeypatch.setenv("CLOB_API_SECRET", "s")
        monkeypatch.setenv("CLOB_API_PASSPHRASE", "p")
        clob._live_client()
        assert created["creds_kwargs"] == ("k", "s", "p")
        assert created["credentials"] is not None
    finally:
        _restore_env(saved)


def test_sdk_order_adapter_maps_dict_and_normalizes_response():
    calls = {}

    class FakeSdk:
        def place_limit_order(self, **kw):
            calls.update(kw)

            class Resp:
                order_id = "oid-9"
                status = "live"

            return Resp()

    out = clob._SdkOrderAdapter(FakeSdk()).post_order(
        {"token_id": "123", "side": "BUY", "price": 0.45, "size": 10.0, "post_only": True,
         "order_type": "GTC", "timestamp_ms": 1234, "builder_code": "0xbeef"})
    assert out["order_id"] == "oid-9" and out["status"] == "live"
    assert calls == {"token_id": "123", "price": 0.45, "size": 10.0, "side": "BUY",
                     "post_only": True, "builder_code": "0xbeef"}
    assert "timestamp_ms" not in calls and "nonce" not in calls  # the SDK stamps the V2 struct itself


def test_sdk_order_adapter_maps_rate_limit_to_429(monkeypatch):
    created = {}
    RateLimitError = _fake_sdk_modules(monkeypatch, created)

    class Throttled:
        def cancel_orders(self, *, order_ids):
            raise RateLimitError("slow down")

    with pytest.raises(RuntimeError, match="429"):              # cancel_orders' backoff keys on "429"
        clob._SdkOrderAdapter(Throttled()).cancel_orders(["a"])


def test_cancel_orders_backs_off_on_429_and_reraises_the_rest(monkeypatch):
    saved = _clear_env()
    sleeps = []

    class Flaky:
        n = 0

        def cancel_orders(self, ids):
            Flaky.n += 1
            if Flaky.n < 3:
                raise RuntimeError("HTTP 429 too many requests")

    try:
        os.environ.update(LIVE_ENV)
        monkeypatch.setattr(clob, "_live_client", lambda: Flaky())
        monkeypatch.setattr(clob.time, "sleep", sleeps.append)
        clob.cancel_orders(["a", "b"])
        assert Flaky.n == 3 and sleeps == [0.5, 1.0]            # exponential, resumes after backoff

        class Broken:
            def cancel_orders(self, ids):
                raise RuntimeError("400 bad order id")

        monkeypatch.setattr(clob, "_live_client", lambda: Broken())
        with pytest.raises(RuntimeError, match="400"):          # non-429 fails fast, no retry loop
            clob.cancel_orders(["a"])
    finally:
        _restore_env(saved)


def test_place_order_retries_once_on_invalid_tick_with_fresh_tick(monkeypatch):
    saved = _clear_env()
    posted = []

    class StaleTick:
        def post_order(self, order):
            posted.append(dict(order))
            if len(posted) == 1:
                raise RuntimeError("INVALID_TICK: price not aligned")
            return {"order_id": "oid-2"}

    orig_spent = clob._live_notional_spent
    try:
        os.environ.update(LIVE_ENV)
        monkeypatch.setattr(clob, "_live_client", lambda: StaleTick())
        monkeypatch.setattr(clob, "_http_get",
                            lambda url, params=None, timeout=15: {"minimum_tick_size": "0.001"})
        clob._live_notional_spent = 0.0
        oid = clob.place_order("123", "buy", 0.4567, 10.0, tick_size=0.01)
        assert oid == "oid-2" and len(posted) == 2
        assert posted[0]["price"] == pytest.approx(0.45)        # stale 0.01 tick
        assert posted[1]["price"] == pytest.approx(0.456)       # re-rounded DOWN on the fresh tick

        class WouldCross:
            def post_order(self, order):
                raise RuntimeError("post_only_would_cross")

        monkeypatch.setattr(clob, "_live_client", lambda: WouldCross())
        spent_before = clob._live_notional_spent
        with pytest.raises(RuntimeError, match="post_only_would_cross"):  # legit refusal: no retry
            clob.place_order("123", "buy", 0.4567, 10.0, tick_size=0.01)
        # ambiguous/unretried failure keeps the RESERVATION (fail-closed): the order might rest live
        assert clob._live_notional_spent == pytest.approx(spent_before + 4.5)
    finally:
        clob._live_notional_spent = orig_spent
        _restore_env(saved)


def test_place_order_retry_is_exactly_once_and_releases_on_definite_rejection(monkeypatch):
    saved = _clear_env()
    posts = []

    class AlwaysInvalid:
        def post_order(self, order):
            posts.append(dict(order))
            raise RuntimeError("INVALID_TICK: still not aligned")

    orig_spent = clob._live_notional_spent
    try:
        os.environ.update(LIVE_ENV)
        monkeypatch.setattr(clob, "_live_client", lambda: AlwaysInvalid())
        monkeypatch.setattr(clob, "_http_get",
                            lambda url, params=None, timeout=15: {"minimum_tick_size": "0.001"})
        clob._live_notional_spent = 0.0
        with pytest.raises(RuntimeError, match="INVALID_TICK"):
            clob.place_order("123", "buy", 0.4567, 10.0, tick_size=0.01)
        assert len(posts) == 2                                  # exactly one retry, never a loop
        # both attempts were DEFINITE exchange-side rejections → the reservation is fully released
        assert clob._live_notional_spent == 0.0
    finally:
        clob._live_notional_spent = orig_spent
        _restore_env(saved)


# --- write path: the pUSD wrap (fake web3 — approve-then-wrap, 6-decimal units, gate-first) -----

def _fake_web3_module(monkeypatch, sent, *, allowance=0):
    import sys
    import types

    class Acct:
        address = "0xME"

        @staticmethod
        def sign_transaction(tx):
            return types.SimpleNamespace(raw_transaction=tx)   # web3 v7 attr name

    class FnCall:
        def __init__(self, name, args):
            self.name, self.args = name, args

        def call(self):
            return allowance                                    # current on-chain allowance

        def build_transaction(self, params):
            return {"fn": self.name, "args": self.args, **params}

    class Functions:
        def allowance(self, owner, spender):
            return FnCall("allowance", (owner, spender))

        def approve(self, spender, amount):
            return FnCall("approve", (spender, amount))

        def wrap(self, amount):
            return FnCall("wrap", (amount,))

    class Contract:
        def __init__(self, address):
            self.address, self.functions = address, Functions()

    class Eth:
        account = types.SimpleNamespace(from_key=lambda k: Acct())

        @staticmethod
        def contract(address, abi):
            return Contract(address)

        @staticmethod
        def get_transaction_count(addr):
            return 7

        @staticmethod
        def send_raw_transaction(tx):
            sent.append((tx["fn"], tx["args"]))
            return types.SimpleNamespace(hex=lambda: f"0x{tx['fn']}")

        @staticmethod
        def wait_for_transaction_receipt(h):
            return {"status": 1}

    class W3:
        def __init__(self, provider):
            self.eth = Eth()

        class HTTPProvider:
            def __init__(self, url):
                pass

        @staticmethod
        def to_checksum_address(a):
            return a

    mod = types.ModuleType("web3")
    mod.Web3 = W3
    monkeypatch.setitem(sys.modules, "web3", mod)


def test_wrap_usdce_to_pusd_approves_then_wraps_in_6_decimals(monkeypatch):
    saved = _clear_env()
    sent = []
    _fake_web3_module(monkeypatch, sent)
    try:
        os.environ.update(LIVE_ENV)
        monkeypatch.setenv("WALLET_PRIVATE_KEY", "0xkey")
        monkeypatch.setenv("POLYGON_RPC_URL", "http://rpc")
        tx = clob.wrap_usdce_to_pusd(10.5)
        assert tx == "0xwrap"
        assert sent == [("approve", (clob.COLLATERAL_ONRAMP, 10_500_000)),  # allowance short first
                        ("wrap", (10_500_000,))]                # then onramp.wrap, 6-decimal units

        with pytest.raises(ValueError):
            clob.wrap_usdce_to_pusd(0)
        with pytest.raises(LiveGateError):                      # never wrap more than the capital cap
            clob.wrap_usdce_to_pusd(51.0)
        monkeypatch.delenv("POLYGON_RPC_URL")
        with pytest.raises(LiveGateError, match="POLYGON_RPC_URL"):
            clob.wrap_usdce_to_pusd(1.0)
    finally:
        _restore_env(saved)


def test_wrap_skips_approve_when_allowance_sufficient(monkeypatch):
    saved = _clear_env()
    sent = []
    _fake_web3_module(monkeypatch, sent, allowance=10_500_000)  # already approved enough
    try:
        os.environ.update(LIVE_ENV)
        monkeypatch.setenv("WALLET_PRIVATE_KEY", "0xkey")
        monkeypatch.setenv("POLYGON_RPC_URL", "http://rpc")
        assert clob.wrap_usdce_to_pusd(10.5) == "0xwrap"
        assert sent == [("wrap", (10_500_000,))]                # no redundant approve tx
    finally:
        _restore_env(saved)


def test_module_import_stays_dependency_free():
    """The lazy-import contract: importing execution.clob must pull neither requests nor the pinned
    py-sdk nor web3 (paper modes stay dependency-free; test_data_layer smoke relies on it)."""
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = ("import sys; import execution.clob; "
            "bad = [m for m in ('requests', 'web3', 'polymarket') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-c", code], cwd=root).returncode == 0
