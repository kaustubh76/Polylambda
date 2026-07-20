"""Reconcile the keeper's booked position against the ground truth on-chain.

Independently re-derives engine inventory/cash for each keeper-managed fleet market straight from
its on-chain Traded events (via the production ChainReader) and checks it matches the last
inventory_after/cash_after the keeper logged in the session file. Exits non-zero on any mismatch.

    user buyYes  = engine SELL: inv -= size, cash += usdc
    user sellYes = engine BUY : inv += size, cash -= usdc

    AMOY_RPC_URL=https://polygon-amoy.drpc.org \
        .venv/bin/python scripts/smoke_reconcile.py [session-jsonl-path]
"""
from __future__ import annotations

import json
import os
import sys
import time

from execution.testnet_chain import ChainReader, load_fleet, make_w3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_session() -> str:
    day = time.strftime("%Y%m%d", time.gmtime())
    return os.path.join(ROOT, f".data_cache/sessions/session-testnet-{day}.jsonl")


def keeper_booked(session_path: str, cid: str):
    """Last inventory_after/cash_after the keeper logged for this market (+ fill count)."""
    inv = cash = 0.0
    n = 0
    for line in open(session_path):
        r = json.loads(line)
        if r.get("type") == "fill" and r.get("cid") == cid:
            inv, cash, n = r["inventory_after"], r["cash_after"], n + 1
    return inv, cash, n


def main() -> None:
    session_path = sys.argv[1] if len(sys.argv) > 1 else _default_session()
    fleet, abi = load_fleet(os.path.join(ROOT, "webapp/backend/markets.json"))
    reader = ChainReader(make_w3(), abi)
    head = reader.head_block()
    ok = True
    for fm in fleet:
        if not fm.keeper_managed:
            continue
        logs = reader.traded_logs(fm.address, fm.deployed_block, head)
        inv = cash = 0.0
        for ev in logs:
            if ev["buy"]:            # user buyYes -> engine SELL
                inv -= ev["size"]; cash += ev["usdc"]
            else:                     # user sellYes -> engine BUY
                inv += ev["size"]; cash -= ev["usdc"]
        b_inv, b_cash, n = keeper_booked(session_path, fm.tracks_cid or fm.token_id)
        inv_ok = abs(inv - b_inv) < 1e-6
        cash_ok = abs(cash - b_cash) < 1e-6
        ok = ok and inv_ok and cash_ok
        print(f"{fm.category:<8} onchain_events={len(logs)} keeper_fills={n}")
        print(f"   inventory  chain={inv:+.6f}  keeper={b_inv:+.6f}  {'OK' if inv_ok else 'MISMATCH'}")
        print(f"   cash       chain={cash:+.6f}  keeper={b_cash:+.6f}  {'OK' if cash_ok else 'MISMATCH'}")
    print("\nRECONCILE", "PASS — keeper books == on-chain Traded events" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
