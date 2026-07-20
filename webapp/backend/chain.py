"""Backend chain layer — Polygon Amoy testnet fleet reads for the dashboard.

Read-only: per-market on-chain snapshots across the keeper-managed fleet registry (markets.json),
reusing one cached web3 connection. All engine-signed WRITES live in the execution layer
(execution/testnet_keeper.py + execution/testnet_clob.py, risk-governed); this module never signs.
Everything degrades gracefully to "unreachable" so a missing key / RPC blip never breaks the app.
"""
from __future__ import annotations

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

# drpc supports eth_getLogs (the fleet reader); the official rpc-amoy rejects log ranges.
AMOY_RPC = os.environ.get("AMOY_RPC_URL", "https://polygon-amoy.drpc.org")
AMOY_CHAIN_ID = 80002
EXPLORER = "https://amoy.polygonscan.com"
USDC_ADDR = os.environ.get("AMOY_USDC_ADDRESS", "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582")

_lock = threading.Lock()           # serialize engine nonces (shared with the keeper's signer)
_cache: dict[str, tuple[float, object]] = {}
_singletons: dict[str, object] = {}


def _w3():
    if "w3" not in _singletons:
        from web3 import Web3
        from web3.middleware import ExtraDataToPOAMiddleware  # Amoy is POA
        w3 = Web3(Web3.HTTPProvider(AMOY_RPC, request_kwargs={"timeout": 12}))
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        _singletons["w3"] = w3
    return _singletons["w3"]


def _cached(key: str, ttl: float, fn):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = fn()
    _cache[key] = (now + ttl, val)
    return val


def fleet() -> dict:
    """Per-market on-chain snapshots across the keeper-managed fleet registry (markets.json).

    Read-only and graceful: no registry -> empty list; RPC failure -> per-market error entries."""
    def fetch():
        from execution.testnet_chain import ChainReader, load_fleet
        markets, abi = load_fleet()
        if not markets:
            return {"reachable": True, "markets": [], "note": "no fleet registry yet"}
        out = []
        try:
            reader = ChainReader(_w3(), abi)
            for m in markets:
                row = {"address": m.address, "category": m.category, "token_id": m.token_id,
                       "tracks_cid": m.tracks_cid, "end_date_ts": m.end_date_ts,
                       "keeper_managed": m.keeper_managed, "label": m.label,
                       "explorer": f"{EXPLORER}/address/{m.address}"}
                try:
                    row.update(reader.snapshot(m.address))
                except Exception as e:  # noqa: BLE001
                    row.update({"deployed": False, "error": str(e)[:120]})
                out.append(row)
            return {"reachable": True, "markets": out, "explorer": EXPLORER}
        except Exception as e:  # noqa: BLE001
            return {"reachable": False, "error": str(e)[:200], "markets": out}
    return _cached("fleet", 5.0, fetch)
