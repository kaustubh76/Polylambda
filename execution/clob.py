"""
clob — Polymarket CLOB V2 wrapper: ungated public READ path + hard-gated WRITE path.

Corrected design (see ../DECISIONS.md #4-#7):
  * Use the official Polymarket py-sdk (CLOB V2), pinned as `polymarket-client==0.1.0b13`
    (import name `polymarket`; see requirements.txt). ⚠ NOT py-clob-client (archived / V1 / dead).
  * Auth: L1 EIP-712 wallet sig derives creds; L2 HMAC for order ops.
  * Orders: GTC/GTD, POST-ONLY for guaranteed maker status; batch order/cancel; rate-aware backoff.
  * Order uniqueness = MILLISECOND TIMESTAMP (NO nonce logic).
  * Collateral is pUSD — wrap USDC.e via the CollateralOnramp before trading.
  * Attach a Builder Code (bytes32) for reward attribution.
  * Per-market tick_size / min_order_size are DYNAMIC — read at runtime; handle INVALID_TICK.
  * Book reads (REST/WS) need NO auth -> used by paper-live.

READ vs WRITE seam: the read functions (read_book / get_market_microstructure / read_trades) are
public, no-auth, and safe under any jurisdiction — paper-live uses ONLY these. The write functions
(place_order / cancel_orders / wrap_usdce_to_pusd) call _require_live_gate() as their FIRST line:
they refuse to run unless MODE=live AND JURISDICTION_ACK is explicitly set (JURISDICTION.md's
standing default is paper-only). Paper adapters (execution/paper.py) never import the write path.

Endpoint shapes: encoded from the documented public API (Gamma markets / CLOB book / Data-API
trades). ⚠ Live shape verification was BLOCKED from this network on 2026-07-05 — the ISP
DNS+SNI-blocks *.polymarket.com (resolves to a sinkhole; TLS with polymarket SNI is reset). The
normalizers are therefore fixture-tested; on an unblocked network (e.g. VPN), re-verify with:
  curl 'https://clob.polymarket.com/book?token_id=<id>'
  curl 'https://gamma-api.polymarket.com/markets?closed=false&limit=1'
"""
from __future__ import annotations

import json
import os
import time

# --- single endpoint registry (env-overridable; the ONLY place URLs live) ---------------------
CLOB_REST = os.environ.get("POLY_CLOB_URL", "https://clob.polymarket.com")
GAMMA_REST = os.environ.get("POLY_GAMMA_URL", "https://gamma-api.polymarket.com")
DATA_API = os.environ.get("POLY_DATA_URL", "https://data-api.polymarket.com")
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"  # USDC.e -> pUSD (DECISIONS.md §D)
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"             # bridged USDC (DECISIONS.md §D)
# ⚠ DECISIONS.md §D: re-confirm both addresses on Polygonscan before any live use.


def _http_get(url: str, params: dict | None = None, timeout: int = 15) -> dict | list:
    """GET url?params -> parsed JSON. The single testability seam: tests swap clob._http_get.

    requests is imported lazily so importing this module stays network- and dependency-free
    (the tests/test_data_layer.py import smoke test relies on that).
    """
    import requests

    r = requests.get(url, params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ==============================================================================================
# READ PATH — public, no auth. Paper-live consumes ONLY these.
# ==============================================================================================

def read_book(token_id: str) -> dict:
    """Public order-book snapshot, normalized to the shape estimate_fair_value consumes:
    {"bids": [(price, size), ...], "asks": [...]}, floats, sorted best-first."""
    raw = _http_get(f"{CLOB_REST}/book", {"token_id": token_id})
    # documented shape: {"market": ..., "asset_id": ..., "bids": [{"price": "0.45", "size": "100"}...],
    # "asks": [...]} — price/size are STRINGS; bids may arrive in either order -> sort explicitly.
    bids = sorted(((float(l["price"]), float(l["size"])) for l in raw.get("bids", [])),
                  key=lambda t: -t[0])
    asks = sorted(((float(l["price"]), float(l["size"])) for l in raw.get("asks", [])),
                  key=lambda t: t[0])
    return {"bids": bids, "asks": asks}


def _parse_json_maybe(v):
    """Gamma stores some array fields as JSON STRINGS (e.g. clobTokenIds='[\"123\",\"456\"]')."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return v
    return v


def get_market_microstructure(token_id: str) -> dict:
    """Per-market dynamic params, one flat dict:
    tick_size, min_order_size (CLOB) + end_date_iso, max_incentive_spread, reward_min_size,
    rewards_daily_rate_usd, game_start_time, neg_risk, condition_id, question (Gamma).

    Microstructure is dynamic (ticks tighten near 0/1) but not per-second — the loop caches this
    and refreshes every N ticks."""
    out: dict = {"token_id": token_id}
    # CLOB: authoritative tick/min-size for the token
    try:
        tick = _http_get(f"{CLOB_REST}/tick-size", {"token_id": token_id})
        out["tick_size"] = float(tick.get("minimum_tick_size", tick.get("tick_size", 0.01)))
    except Exception:
        out["tick_size"] = 0.01  # documented default; INVALID_TICK is handled at order build
    # Gamma: market metadata + the Liquidity Rewards params
    rows = _http_get(f"{GAMMA_REST}/markets", {"clob_token_ids": token_id, "limit": 1})
    m = rows[0] if isinstance(rows, list) and rows else {}
    rewards = _parse_json_maybe(m.get("clobRewards")) or []
    daily = 0.0
    for r in rewards if isinstance(rewards, list) else []:
        daily += float(r.get("rewardsDailyRate", 0) or 0)
    out.update({
        "condition_id": m.get("conditionId"),
        "question": m.get("question"),
        "end_date_iso": m.get("endDate"),
        "game_start_time": m.get("gameStartTime"),
        "neg_risk": bool(m.get("negRisk", False)),
        "min_order_size": float(m.get("orderMinSize", 5) or 5),
        "max_incentive_spread": float(m.get("rewardsMaxSpread", 0.035) or 0.035) / (
            100.0 if float(m.get("rewardsMaxSpread", 0) or 0) > 1 else 1.0),  # % vs fraction guard
        "reward_min_size": float(m.get("rewardsMinSize", 0) or 0),
        "rewards_daily_rate_usd": daily,
    })
    return out


def read_trades(token_id: str, since_ts: int = 0, *, limit: int = 500) -> list[dict]:
    """Recent public prints for one token: [{price, size, side, timestamp}], oldest-first.
    Feeds the paper-live ConservativeFillModel (and the sigma tape)."""
    raw = _http_get(f"{DATA_API}/trades", {"asset": token_id, "limit": limit})
    rows = raw if isinstance(raw, list) else raw.get("trades", [])
    out = []
    for t in rows:
        ts = int(t.get("timestamp", t.get("matchTime", 0)) or 0)
        if ts <= since_ts:
            continue
        out.append({"price": float(t.get("price", 0) or 0), "size": float(t.get("size", 0) or 0),
                    "side": (t.get("side") or "").upper(), "timestamp": ts,
                    "maker": t.get("maker", ""), "taker": t.get("taker", "")})
    return sorted(out, key=lambda r: r["timestamp"])


# ==============================================================================================
# WRITE PATH — hard-gated. JURISDICTION.md: paper-only is the standing default.
# ==============================================================================================

class LiveGateError(RuntimeError):
    """Raised when the real-order path is invoked without the explicit live-mode gate."""


def _require_live_gate() -> None:
    if os.environ.get("MODE") != "live":
        raise LiveGateError("real-order path requires MODE=live (JURISDICTION.md: paper-only default)")
    if os.environ.get("JURISDICTION_ACK") != "RESOLVED_SEE_JURISDICTION_MD":
        raise LiveGateError("set JURISDICTION_ACK=RESOLVED_SEE_JURISDICTION_MD only after updating "
                            "JURISDICTION.md's resolution log")
    cap = os.environ.get("MAX_CAPITAL_USDC")
    if not cap:
        raise LiveGateError("MAX_CAPITAL_USDC must be set (tiny) for any live order")
    import math

    try:
        valid = math.isfinite(float(cap)) and float(cap) > 0
    except ValueError:
        valid = False
    if not valid:
        # NaN poisons every `> cap` comparison to False — a "nan"/inf/junk cap would silently
        # DISABLE the notional bound while the gate reads as satisfied. Fail closed instead.
        raise LiveGateError(f"MAX_CAPITAL_USDC must be a finite positive number, got {cap!r}")


class _SdkOrderAdapter:
    """Maps this module's plain order dict onto the pinned py-sdk `SecureClient`.

    place_order/cancel_orders (and the tests' FakeClient) rely on exactly two methods:
    `post_order(order_dict) -> {"order_id": ...}` and `cancel_orders(list[str])`. The SDK's own
    surface is `place_limit_order(**kwargs) -> OrderResponse` and `cancel_orders(order_ids=[...])`;
    every SDK call site is isolated HERE so a BETA rename is a one-class fix.

    NB: the SDK signs the CLOB V2 order struct itself (ms-timestamp uniqueness, NO nonce — it
    resolves the live tick and the neg-risk exchange address per token). Our dict's `timestamp_ms`
    is the client-side audit copy, deliberately not forwarded."""

    def __init__(self, sdk_client):
        self._sdk = sdk_client

    def post_order(self, order: dict) -> dict:
        kwargs = dict(token_id=order["token_id"], price=order["price"], size=order["size"],
                      side=order["side"], post_only=order.get("post_only", True))
        if order.get("builder_code"):
            kwargs["builder_code"] = order["builder_code"]   # bytes32 attribution (DECISIONS.md #12)
        resp = self._sdk.place_limit_order(**kwargs)
        return {"order_id": str(getattr(resp, "order_id", resp)),
                "status": getattr(resp, "status", None)}

    def cancel_orders(self, order_ids: list[str]) -> None:
        from polymarket.errors import RateLimitError

        try:
            self._sdk.cancel_orders(order_ids=list(order_ids))
        except RateLimitError as e:
            # keep "429" in str(e): cancel_orders' backoff loop keys on it
            raise RuntimeError(f"429 rate-limited: {e}") from e


_client = None  # constructed-once cache; lives INSIDE the seam so tests can swap _live_client itself


def _live_client():
    """The pinned py-sdk client factory (lazy import; swappable in tests as `clob._live_client`).

    Never constructed in paper modes: every caller passes _require_live_gate() first, and this
    re-checks it (defense in depth) before touching key material. Auth per DECISIONS.md #4:
    `SecureClient.create` performs the L1 EIP-712 wallet-sig credential derivation when no L2 creds
    are supplied; explicit CLOB_API_KEY/SECRET/PASSPHRASE env creds are used when all three are set.
    L2 HMAC signing then happens inside the SDK per request."""
    global _client
    if _client is not None:
        return _client
    _require_live_gate()
    key = os.environ.get("WALLET_PRIVATE_KEY")
    if not key:
        raise LiveGateError("WALLET_PRIVATE_KEY required to derive CLOB V2 credentials (L1 EIP-712)")
    try:
        from polymarket import ApiKeyCreds, SecureClient  # lazy: paper modes never import the sdk
    except ImportError as e:
        raise LiveGateError("polymarket-client not installed — `pip install -r requirements.txt` on "
                            "the live deployment (paper / paper-live never needs it)") from e
    ak, sec, pw = (os.environ.get(k) for k in
                   ("CLOB_API_KEY", "CLOB_API_SECRET", "CLOB_API_PASSPHRASE"))
    creds = ApiKeyCreds(key=ak, secret=sec, passphrase=pw) if (ak and sec and pw) else None
    sdk = SecureClient.create(private_key=key, wallet=os.environ.get("POLY_FUNDER_WALLET"),
                              credentials=creds)
    _client = _SdkOrderAdapter(sdk)
    return _client


_live_notional_spent = 0.0  # cumulative session notional, enforced against MAX_CAPITAL_USDC


def _round_to_tick(price: float, tick: float, side: str) -> float:
    """Post-only-safe rounding: bid DOWN, ask UP — never crosses from rounding."""
    steps = price / tick
    import math

    return (math.floor(steps) if side == "BUY" else math.ceil(steps)) * tick


def place_order(token_id: str, side: str, price: float, size: float, *, post_only: bool = True,
                builder_code: str | None = None, tick_size: float = 0.01,
                min_order_size: float = 5.0) -> str:
    """Post-only maker order via py-sdk (CLOB V2): ms-timestamp uniqueness (NO nonce), builder_code
    attribution, dynamic tick/min-size rounding, cumulative notional <= MAX_CAPITAL_USDC (reserved
    BEFORE the send and kept on ambiguous failure — fail-closed, see the inline note).
    GATED: raises LiveGateError unless the live gate is explicitly satisfied."""
    _require_live_gate()
    global _live_notional_spent
    if size < min_order_size:
        raise ValueError(f"size {size} < min_order_size {min_order_size}")
    px = _round_to_tick(price, tick_size, side.upper())
    notional = px * size
    cap = float(os.environ["MAX_CAPITAL_USDC"])
    if _live_notional_spent + notional > cap:
        raise LiveGateError(f"order notional {notional:.2f} would exceed MAX_CAPITAL_USDC={cap}")
    order = {
        "token_id": token_id, "side": side.upper(), "price": px, "size": size,
        "post_only": post_only, "order_type": "GTC",
        "timestamp_ms": int(time.time() * 1000),           # CLOB V2 uniqueness — NO nonce field
        **({"builder_code": builder_code} if builder_code else
           {"builder_code": os.environ.get("BUILDER_CODE")} if os.environ.get("BUILDER_CODE") else {}),
    }
    # RESERVE against the cap BEFORE the send. An ambiguous failure (timeout/reset/5xx AFTER the
    # exchange persisted the order) can leave it resting on the book — the cap must bound worst-case
    # on-exchange exposure, not just confirmed acks. Fail-closed: only a definite exchange-side
    # rejection (INVALID_TICK, retried below) releases the reservation; any other error keeps it.
    _live_notional_spent += notional
    try:
        resp = _live_client().post_order(order)
    except Exception as e:  # noqa: BLE001 - single retry ONLY for a stale tick (DECISIONS.md #7)
        if "INVALID_TICK" not in str(e):
            raise  # post_only_would_cross / ambiguous errors: no retry, reservation kept
        _live_notional_spent -= notional                    # definite rejection — nothing rested
        # tick moved under us (dynamic near 0/1): re-read it, re-round post-only-safe, retry once
        fresh = _http_get(f"{CLOB_REST}/tick-size", {"token_id": token_id})
        tick_size = float(fresh.get("minimum_tick_size", fresh.get("tick_size", tick_size)))
        order["price"] = px = _round_to_tick(price, tick_size, side.upper())
        notional = px * size
        if _live_notional_spent + notional > cap:
            raise LiveGateError(f"order notional {notional:.2f} would exceed MAX_CAPITAL_USDC={cap}")
        _live_notional_spent += notional
        try:
            resp = _live_client().post_order(order)
        except Exception as e2:  # noqa: BLE001 - release only on another definite rejection
            if "INVALID_TICK" in str(e2):
                _live_notional_spent -= notional
            raise
    return str(resp.get("order_id", resp) if isinstance(resp, dict) else resp)


def cancel_orders(order_ids: list[str]) -> None:
    """Batch cancel with rate-aware exponential backoff on 429. GATED like place_order."""
    _require_live_gate()
    client = _live_client()
    delay = 0.5
    for attempt in range(5):
        try:
            client.cancel_orders(list(order_ids))
            return
        except Exception as e:  # noqa: BLE001 - backoff only on rate-limit, re-raise the rest
            if "429" not in str(e) or attempt == 4:
                raise
            time.sleep(delay)
            delay *= 2


# minimal inline ABIs — only the three calls the wrap needs (no ABI files to drift)
_ERC20_ABI = [
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]
_ONRAMP_ABI = [
    {"name": "wrap", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "amount", "type": "uint256"}], "outputs": []},
]


def wrap_usdce_to_pusd(amount: float) -> str:
    """Wrap `amount` USDC.e -> pUSD via the CollateralOnramp (0x93070a…, DECISIONS.md #5/§D).
    GATED behind _require_live_gate; returns the wrap tx hash.

    approve(onramp, amount) is sent first only when the current allowance is short, then
    onramp.wrap(amount). Amounts are 6-decimal USDC units. Needs WALLET_PRIVATE_KEY + a Polygon RPC
    (POLYGON_RPC_URL). The on-chain tx nonce here is normal Polygon plumbing — the NO-nonce rule
    (DECISIONS.md #4) is about CLOB V2 *order* structs, not transactions.
    ⚠ §D standing caveat: re-confirm the onramp address + wrap ABI on Polygonscan before live use —
    encoded from the documented onramp, fixture-tested only (this network SNI-blocks polymarket)."""
    _require_live_gate()
    if amount <= 0:
        raise ValueError(f"wrap amount must be positive, got {amount}")
    cap = float(os.environ["MAX_CAPITAL_USDC"])
    if amount > cap:
        raise LiveGateError(f"wrap amount {amount:.2f} exceeds MAX_CAPITAL_USDC={cap}")
    key = os.environ.get("WALLET_PRIVATE_KEY")
    if not key:
        raise LiveGateError("WALLET_PRIVATE_KEY required for the pUSD wrap")
    rpc = os.environ.get("POLYGON_RPC_URL")
    if not rpc:
        raise LiveGateError("POLYGON_RPC_URL required for the pUSD wrap")
    from web3 import Web3  # lazy: module import stays dependency-free (test_data_layer smoke)

    w3 = Web3(Web3.HTTPProvider(rpc))
    acct = w3.eth.account.from_key(key)
    raw = int(round(amount * 1e6))                          # USDC.e is 6-decimals
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=_ERC20_ABI)
    onramp = w3.eth.contract(address=Web3.to_checksum_address(COLLATERAL_ONRAMP), abi=_ONRAMP_ABI)

    def _send(fn) -> str:
        tx = fn.build_transaction({"from": acct.address,
                                   "nonce": w3.eth.get_transaction_count(acct.address)})
        signed = acct.sign_transaction(tx)
        raw_tx = getattr(signed, "raw_transaction", None) or signed.rawTransaction  # web3 v7 / v6
        h = w3.eth.send_raw_transaction(raw_tx)
        rcpt = w3.eth.wait_for_transaction_receipt(h)
        if rcpt["status"] != 1:
            raise RuntimeError(f"tx reverted: {h.hex()}")
        return h.hex()

    if usdc.functions.allowance(acct.address, onramp.address).call() < raw:
        _send(usdc.functions.approve(onramp.address, raw))
    return _send(onramp.functions.wrap(raw))
