"""Smoke taker: a throwaway user takes against a fleet market's standing engine quote,
producing a REAL on-chain Traded event the keeper then polls as an engine fill.

The engine wallet funds a freshly-generated throwaway user (a little POL for gas + 1 test-USDC),
which approves and buys/sells YES against a keeper-managed market from webapp/backend/markets.json.
User-signed — independent of the RiskGovernor kill-switch (that only gates engine signing).

    AMOY_RPC_URL=https://polygon-amoy.drpc.org \
        .venv/bin/python scripts/smoke_taker.py <market_index> <buy|sell> <size>
"""
from __future__ import annotations

import json
import os
import re
import sys

from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AMOY_RPC = os.environ.get("AMOY_RPC_URL", "https://polygon-amoy.drpc.org")
USDC_ADDR = Web3.to_checksum_address(os.environ.get("AMOY_USDC_ADDRESS", "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582"))
GWEI = int(os.environ.get("AMOY_GAS_GWEI", "30"))
U = 10 ** 6
ERC20 = [
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "v", "type": "uint256"}], "outputs": [{"type": "bool"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "s", "type": "address"}, {"name": "v", "type": "uint256"}], "outputs": [{"type": "bool"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "a", "type": "address"}], "outputs": [{"type": "uint256"}]},
]


def engine_key() -> str:
    key = os.environ.get("ENGINE_PRIVATE_KEY")
    if key:
        return key
    for line in open(os.path.join(ROOT, ".env")):
        m = re.match(r"\s*ENGINE_PRIVATE_KEY\s*=\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    raise SystemExit("no ENGINE_PRIVATE_KEY (shell env or .env)")


def main() -> None:
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    side = sys.argv[2] if len(sys.argv) > 2 else "buy"
    size = float(sys.argv[3]) if len(sys.argv) > 3 else 0.3
    size6 = int(round(size * U))

    w3 = Web3(Web3.HTTPProvider(AMOY_RPC, request_kwargs={"timeout": 30}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    assert w3.is_connected() and w3.eth.chain_id == 80002, "not connected to Amoy (80002)"

    reg = json.load(open(os.path.join(ROOT, "webapp/backend/markets.json")))
    abi = reg["abi"]
    managed = [m for m in reg["markets"] if m.get("keeper_managed")]
    mkt = managed[idx]
    addr = Web3.to_checksum_address(mkt["address"])
    m = w3.eth.contract(address=addr, abi=abi)
    usdc = w3.eth.contract(address=USDC_ADDR, abi=ERC20)
    engine = Account.from_key(engine_key())

    def send(acct, fn=None, *, to=None, value=0, label=""):
        fee = w3.to_wei(GWEI, "gwei")
        base = {"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address),
                "chainId": 80002, "value": value, "maxFeePerGas": fee, "maxPriorityFeePerGas": fee}
        tx = fn.build_transaction(base) if fn is not None else {**base, "to": Web3.to_checksum_address(to), "gas": 21000}
        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        h = w3.eth.send_raw_transaction(raw)
        rc = w3.eth.wait_for_transaction_receipt(h, timeout=180)
        hx = h.hex() if h.hex().startswith("0x") else "0x" + h.hex()
        assert rc["status"] == 1, f"{label} REVERTED {hx}"
        print(f"  ok {label:<24} {hx}")
        return rc

    snap = m.functions.snapshot().call()
    bid, ask, maxt = snap[0] / U, snap[1] / U, snap[2] / U
    print(f"market[{idx}] {mkt['category']} {addr}")
    print(f"  snapshot bid {bid} ask {ask} maxTrade {maxt} disputed {snap[4]} resolved {snap[5]}")
    size6 = min(size6, snap[2])  # never exceed the standing maxTrade
    print(f"  taker {side} size {size6 / U}")

    user = Account.create()
    print(f"  throwaway user {user.address}")
    send(engine, to=user.address, value=w3.to_wei("0.02", "ether"), label="fund user POL")
    send(engine, usdc.functions.transfer(user.address, U), label="fund user 1 USDC")
    send(user, usdc.functions.approve(addr, U), label="user approve USDC")

    if side == "buy":
        rc = send(user, m.functions.buyYes(size6), label=f"user buyYes {size6 / U}")
    else:
        # to sellYes the user must first hold shares: buy then sell
        send(user, m.functions.buyYes(size6), label=f"user buyYes {size6 / U} (pre-sell)")
        rc = send(user, m.functions.sellYes(size6), label=f"user sellYes {size6 / U}")

    tr = m.events.Traded().process_receipt(rc)
    print(f"  Traded: {[(t['args']['buy'], t['args']['size'] / U, t['args']['usdc'] / U) for t in tr]}")
    print(f"  user yesShares {m.functions.yesShares(user.address).call() / U}")
    print(f"  fill block {rc['blockNumber']} head {w3.eth.block_number}")


if __name__ == "__main__":
    main()
