"""Backend chain layer — Polygon Amoy testnet (the engine wallet + PolyLambdaMarket contract).

Read path: connection/engine status, on-chain market snapshot, a user's position, decoded events.
Write path (engine-signed): post a fresh quote from the REAL estimators, flag a dispute, resolve.

Signing mirrors execution/clob.py:353-362 but is UNGATED (never calls _require_live_gate — that's the
mainnet jurisdiction gate). Instead it is testnet-guarded: it refuses unless ENGINE_PRIVATE_KEY is set
AND the connected chain is Amoy (80002). Everything degrades gracefully to "engine offline" (like
live.py) so a missing key / undeployed contract never breaks the app.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
try:  # local dev convenience; Render injects real env vars (load_dotenv won't override those)
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

# drpc supports eth_getLogs (the events feed); the official rpc-amoy rejects log ranges.
AMOY_RPC = os.environ.get("AMOY_RPC_URL", "https://polygon-amoy.drpc.org")
AMOY_CHAIN_ID = 80002
EXPLORER = "https://amoy.polygonscan.com"
USDC_ADDR = os.environ.get("AMOY_USDC_ADDRESS", "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582")
_MARKET_JSON = _ROOT / "webapp" / "backend" / "market.json"

MAX_QUOTE_SIZE = int(float(os.environ.get("ENGINE_MAX_TRADE", "0.5")) * 1e6)  # YES-share cap per trade (low)

_ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "a", "type": "address"}], "outputs": [{"type": "uint256"}]},
]

_lock = threading.Lock()           # serialize engine nonces
_cache: dict[str, tuple[float, object]] = {}
_singletons: dict[str, object] = {}


def _meta() -> dict:
    try:
        return json.loads(_MARKET_JSON.read_text())
    except Exception:
        return {"address": None, "abi": [], "usdc": USDC_ADDR}


def market_address() -> str | None:
    return os.environ.get("MARKET_ADDRESS") or _meta().get("address")


def _w3():
    if "w3" not in _singletons:
        from web3 import Web3
        from web3.middleware import ExtraDataToPOAMiddleware  # Amoy is POA
        w3 = Web3(Web3.HTTPProvider(AMOY_RPC, request_kwargs={"timeout": 12}))
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        _singletons["w3"] = w3
    return _singletons["w3"]


def _acct():
    if "acct" not in _singletons:
        key = os.environ.get("ENGINE_PRIVATE_KEY")
        if not key:
            _singletons["acct"] = None
        else:
            from eth_account import Account
            _singletons["acct"] = Account.from_key(key)
    return _singletons["acct"]


def _market():
    addr = market_address()
    if "market" not in _singletons and addr and _meta().get("abi"):
        from web3 import Web3
        _singletons["market"] = _w3().eth.contract(address=Web3.to_checksum_address(addr), abi=_meta()["abi"])
    return _singletons.get("market")


def engine_address() -> str | None:
    a = _acct()
    return a.address if a else None


def _cached(key: str, ttl: float, fn):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = fn()
    _cache[key] = (now + ttl, val)
    return val


# ---------------------------------------------------------------------------------------------
# writes (engine-signed, ungated, testnet-guarded)
# ---------------------------------------------------------------------------------------------
def _send(fn, value: int = 0) -> str:
    w3, acct = _w3(), _acct()
    if acct is None:
        raise RuntimeError("engine wallet not configured (ENGINE_PRIVATE_KEY missing)")
    if w3.eth.chain_id != AMOY_CHAIN_ID:
        raise RuntimeError("refusing to sign: connected chain is not Amoy (80002)")
    fee = w3.to_wei(int(os.environ.get("AMOY_GAS_GWEI", "30")), "gwei")  # base ~0; explicit low tip
    with _lock:
        tx = fn.build_transaction({"from": acct.address,
                                   "nonce": w3.eth.get_transaction_count(acct.address),
                                   "chainId": AMOY_CHAIN_ID, "value": value,
                                   "maxFeePerGas": fee, "maxPriorityFeePerGas": fee})
        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction  # web3 v7/v6
        h = w3.eth.send_raw_transaction(raw)
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=120)
    if rcpt["status"] != 1:
        raise RuntimeError(f"amoy tx reverted: {h.hex()}")
    return h.hex()


def post_quote(price: float | None = None, category: str | None = None) -> dict:
    """Recompute a two-sided quote from the REAL estimators and post it on-chain (engine-signed)."""
    from . import services
    m = _market()
    if m is None:
        raise RuntimeError("market not deployed")
    snap = _read_snapshot()
    cat = category or snap["category"] or "politics"
    mid = price if price is not None else (snap["bid"] + snap["ask"]) / 2 or 0.62
    resp = services.score_market(category=cat, fill_count=800, price=mid, inventory=0.0, horizon_days=5.0)
    q = resp["quote"]
    bid6, ask6 = int(round(q["bid"] * 1e6)), int(round(q["ask"] * 1e6))
    lam_bps = int(round(resp["lambda"]["lambda_jump"] * 10000))
    sig_bps = int(round(q["sigma"] * 10000))
    h = _send(m.functions.postQuote(bid6, ask6, MAX_QUOTE_SIZE, cat, lam_bps, sig_bps))
    return {"tx": h, "bid": q["bid"], "ask": q["ask"], "category": cat,
            "lambda_jump": resp["lambda"]["lambda_jump"], "sigma": q["sigma"], "explorer": f"{EXPLORER}/tx/{h}"}


def flag_dispute() -> dict:
    m = _market()
    if m is None:
        raise RuntimeError("market not deployed")
    h = _send(m.functions.flagDispute())
    return {"tx": h, "explorer": f"{EXPLORER}/tx/{h}"}


def resolve(yes_won: bool) -> dict:
    m = _market()
    if m is None:
        raise RuntimeError("market not deployed")
    h = _send(m.functions.resolve(bool(yes_won)))
    return {"tx": h, "yesWon": bool(yes_won), "explorer": f"{EXPLORER}/tx/{h}"}


# ---------------------------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------------------------
def _read_snapshot() -> dict:
    m = _market()
    if m is None:
        return {"deployed": False, "bid": 0.0, "ask": 0.0, "category": None}
    s = m.functions.snapshot().call()
    return {"deployed": True, "bid": s[0] / 1e6, "ask": s[1] / 1e6, "max_trade": s[2] / 1e6,
            "quote_ts": int(s[3]), "disputed": bool(s[4]), "resolved": bool(s[5]), "yes_won": bool(s[6]),
            "total_yes": s[7] / 1e6, "escrow_usdc": s[8] / 1e6, "category": s[9],
            "lambda_jump": s[10] / 10000, "sigma": s[11] / 10000}


def status() -> dict:
    def fetch():
        try:
            w3 = _w3()
            block = w3.eth.block_number
            eng = engine_address()
            pol = (w3.eth.get_balance(eng) / 1e18) if eng else None
            addr = market_address()
            return {"reachable": True, "chain_id": w3.eth.chain_id, "block": block,
                    "engine": eng, "engine_pol": pol, "engine_ready": eng is not None,
                    "market_address": addr, "usdc": USDC_ADDR, "explorer": EXPLORER,
                    "market": _read_snapshot() if addr else {"deployed": False}}
        except Exception as e:  # noqa: BLE001
            return {"reachable": False, "error": str(e)[:200], "engine": engine_address(),
                    "market_address": market_address()}
    return _cached("status", 3.0, fetch)


def market() -> dict:
    return _cached("market", 2.0, _read_snapshot)


def position(address: str) -> dict:
    from web3 import Web3
    m = _market()
    if m is None or not address:
        return {"shares": 0.0, "reachable": m is not None}
    addr = Web3.to_checksum_address(address)
    usdc = _w3().eth.contract(address=Web3.to_checksum_address(USDC_ADDR), abi=_ERC20_ABI)
    def fetch():
        shares = m.functions.yesShares(addr).call() / 1e6
        snap = _read_snapshot()
        mid = (snap["bid"] + snap["ask"]) / 2
        return {"reachable": True, "address": addr, "shares": shares, "mark": mid,
                "mark_value": shares * mid, "usdc": usdc.functions.balanceOf(addr).call() / 1e6,
                "disputed": snap["disputed"], "resolved": snap["resolved"], "yes_won": snap["yes_won"]}
    return _cached(f"pos:{addr}", 2.0, fetch)


_EVENT_NAMES = ("QuotePosted", "Traded", "Disputed", "Resolved", "Redeemed", "Collateral")


def events(limit: int = 30) -> dict:
    m = _market()
    if m is None:
        return {"reachable": False, "events": []}

    def fetch():
        w3 = _w3()
        latest = w3.eth.block_number
        # ONE get_logs by contract address over a small recent window — tiny range every RPC accepts
        # (per-topic queries over a wide range trip "block range exceeds limit" on public nodes).
        win = int(os.environ.get("EVENTS_WINDOW_BLOCKS", "2000"))
        dep = int(_meta().get("deployed_block") or 0)
        logs = None
        for w in (win, 400):  # retry with a tiny window if a node rejects the range
            try:
                logs = w3.eth.get_logs({"address": m.address, "fromBlock": max(dep, latest - w), "toBlock": latest})
                break
            except Exception:  # noqa: BLE001
                continue
        if logs is None:
            return {"reachable": True, "events": [], "note": "logs unavailable on this RPC"}
        out = []
        for log in logs:
            for name in _EVENT_NAMES:  # decode by trying each event ABI until one matches
                try:
                    out.append(_fmt_event(name, getattr(m.events, name)().process_log(log)))
                    break
                except Exception:
                    continue
        out.sort(key=lambda e: (e["block"], e["log_index"]), reverse=True)
        return {"reachable": True, "events": out[:limit], "explorer": EXPLORER}
    return _cached("events", 4.0, fetch)


def _fmt_event(name: str, log) -> dict:
    a = dict(log["args"])
    e = {"type": name, "block": log["blockNumber"], "log_index": log["logIndex"],
         "tx": log["transactionHash"].hex()}
    # normalize the money/price fields to human units
    if name == "Traded":
        e.update({"user": a.get("user"), "buy": bool(a.get("buy")), "size": a.get("size", 0) / 1e6,
                  "usdc": a.get("usdc", 0) / 1e6})
    elif name == "QuotePosted":
        e.update({"bid": a.get("bid", 0) / 1e6, "ask": a.get("ask", 0) / 1e6,
                  "category": a.get("category"), "lambda_jump": a.get("lambdaBps", 0) / 10000})
    elif name == "Resolved":
        e.update({"yes_won": bool(a.get("yesWon"))})
    elif name == "Redeemed":
        e.update({"user": a.get("user"), "payout": a.get("payout", 0) / 1e6})
    elif name == "Collateral":
        e.update({"amount": a.get("amount", 0) / 1e6})
    return e
