"""Compile + deploy PolyLambdaMarket to Polygon Amoy, fund a little collateral, post an initial quote,
and write webapp/backend/market.json (address + ABI) for the app to load.

Prereqs: `python scripts/gen_engine_wallet.py` then FUND the printed address on Amoy (POL for gas;
test-USDC optional for collateral). Run:  python scripts/deploy_market.py
"""
from __future__ import annotations

import json
import os
import re

import solcx
from eth_account import Account
from web3 import Web3

ROOT = os.path.dirname(os.path.dirname(__file__))
AMOY_RPC = os.environ.get("AMOY_RPC_URL", "https://rpc-amoy.polygon.technology")
USDC = Web3.to_checksum_address(os.environ.get("AMOY_USDC_ADDRESS", "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582"))
OUT = os.path.join(ROOT, "webapp", "backend", "market.json")
SOL = os.path.join(ROOT, "contracts", "PolyLambdaMarket.sol")

_ERC20_APPROVE = [{"name": "approve", "type": "function", "stateMutability": "nonpayable",
                   "inputs": [{"name": "s", "type": "address"}, {"name": "v", "type": "uint256"}],
                   "outputs": [{"type": "bool"}]},
                  {"name": "balanceOf", "type": "function", "stateMutability": "view",
                   "inputs": [{"name": "a", "type": "address"}], "outputs": [{"type": "uint256"}]}]


def _env() -> dict:
    p = os.path.join(ROOT, ".env")
    d = {}
    if os.path.exists(p):
        for line in open(p):
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
            if m:
                d[m.group(1)] = m.group(2)
    return {**d, **{k: v for k, v in os.environ.items() if k in d or k.startswith(("ENGINE_", "AMOY_"))}}


def compile_market():
    solcx.set_solc_version("0.8.24")
    out = solcx.compile_source(open(SOL).read(), output_values=["abi", "bin"],
                               solc_version="0.8.24", optimize=True, optimize_runs=200)
    key = [k for k in out if k.endswith(":PolyLambdaMarket")][0]
    return out[key]["abi"], out[key]["bin"]


def _send(w3, acct, fn, value=0):
    # Amoy base fee is ~0; web3 auto-inflates the priority fee. Set an explicit low tip (~30 gwei,
    # the validator floor) so the whole deploy fits a small faucet balance.
    fee = w3.to_wei(int(os.environ.get("AMOY_GAS_GWEI", "30")), "gwei")
    tx = fn.build_transaction({"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address),
                               "chainId": w3.eth.chain_id, "value": value,
                               "maxFeePerGas": fee, "maxPriorityFeePerGas": fee})
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    h = w3.eth.send_raw_transaction(raw)
    r = w3.eth.wait_for_transaction_receipt(h)
    if r["status"] != 1:
        raise RuntimeError(f"tx reverted: {h.hex()}")
    return r


def main() -> None:
    env = _env()
    key = env.get("ENGINE_PRIVATE_KEY")
    assert key, "ENGINE_PRIVATE_KEY missing — run scripts/gen_engine_wallet.py and fund the address first"
    w3 = Web3(Web3.HTTPProvider(AMOY_RPC))
    from web3.middleware import ExtraDataToPOAMiddleware  # Amoy is POA (extraData > 32 bytes)
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    assert w3.is_connected() and w3.eth.chain_id == 80002, "not connected to Amoy (80002)"
    acct = Account.from_key(key)
    pol = w3.eth.get_balance(acct.address)
    print(f"engine {acct.address} | POL {w3.from_wei(pol,'ether')} | chain {w3.eth.chain_id}")
    assert pol > 0, "engine wallet has no POL for gas — fund it via the Polygon Amoy faucet first"

    abi, binn = compile_market()
    print("compiled; deploying…")
    C = w3.eth.contract(abi=abi, bytecode=binn)
    rcpt = _send(w3, acct, C.constructor(USDC))
    addr = rcpt["contractAddress"]
    print("DEPLOYED PolyLambdaMarket at", addr, "block", rcpt["blockNumber"])
    market = w3.eth.contract(address=addr, abi=abi)

    # optional collateral (LOW — conserve faucet funds; env-overridable)
    usdc = w3.eth.contract(address=USDC, abi=_ERC20_APPROVE)
    ubal = usdc.functions.balanceOf(acct.address).call()
    cap = int(float(os.environ.get("ENGINE_COLLATERAL_USDC", "1")) * 1e6)  # default 1 USDC
    if ubal > 0:
        fund_amt = min(ubal // 2, cap)
        if fund_amt > 0:
            _send(w3, acct, usdc.functions.approve(addr, fund_amt))
            _send(w3, acct, market.functions.fund(fund_amt))
            print(f"funded collateral: {fund_amt/1e6:.2f} USDC")
    else:
        print("engine holds 0 test-USDC — skipping collateral (buys still work; fund later for payouts)")

    # initial quote (LOW cap; the backend re-posts live from the estimators)
    max_trade = int(float(os.environ.get("ENGINE_MAX_TRADE", "0.5")) * 1e6)  # default 0.5 YES/trade
    _send(w3, acct, market.functions.postQuote(600_000, 640_000, max_trade, "politics", 183, 470))
    print(f"posted initial quote: bid 0.60 / ask 0.64, maxTrade {max_trade/1e6} YES, politics")

    with open(OUT, "w") as f:
        json.dump({"address": addr, "usdc": USDC, "engine": acct.address,
                   "deployed_block": rcpt["blockNumber"], "abi": abi}, f, indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
