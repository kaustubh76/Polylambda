"""
clob — Polymarket CLOB V2 wrapper: ungated public READ path + hard-gated WRITE path.

Corrected design (see ../DECISIONS.md #4-#7):
  * Use the official Polymarket py-sdk (CLOB V2). ⚠ NOT py-clob-client (archived / V1 / dead).
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
    if not os.environ.get("MAX_CAPITAL_USDC"):
        raise LiveGateError("MAX_CAPITAL_USDC must be set (tiny) for any live order")


def _live_client():
    """The pinned py-sdk client factory (lazy; swappable in tests). Never constructed in paper modes."""
    raise LiveGateError("live client wiring is v2: pin Polymarket/py-sdk (BETA), then implement "
                        "EIP-712 L1 derive + L2 HMAC here (DECISIONS.md #4)")


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
    attribution, dynamic tick/min-size rounding, cumulative notional <= MAX_CAPITAL_USDC.
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
    resp = _live_client().post_order(order)
    _live_notional_spent += notional
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


def wrap_usdce_to_pusd(amount: float) -> str:
    """Wrap USDC.e -> pUSD via the CollateralOnramp (0x93070a…). GATED; on-chain wiring is v2."""
    _require_live_gate()
    raise NotImplementedError(
        f"v2 wiring: approve USDC.e then CollateralOnramp({COLLATERAL_ONRAMP}).wrap({amount}); "
        "requires WALLET_PRIVATE_KEY and a Polygon RPC — deliberately not implemented while "
        "JURISDICTION.md is unresolved")
