"""Deploy a FLEET of PolyLambdaMarket contracts to Polygon Amoy for the testnet keeper.

Per market: deploy -> fund collateral -> initial postQuote from the REAL estimators -> append to
webapp/backend/markets.json (the fleet registry; created on first run by importing the legacy demo
market as keeper_managed=false — the keeper never signs on the demo).

Each fleet market TRACKS a real Polymarket conditionId (picked from the released dispute layer by
category, or passed via --tracks): the confirmed-dispute detector watches those cids, so a live
dispute on the tracked market fires the on-chain defense on its Amoy twin.

Run:  .venv/bin/python scripts/deploy_fleet.py --n 2 --categories politics,crypto
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EXPLORER = "https://amoy.polygonscan.com"


def pick_tracked_cids(categories: list[str], per_category_offset: int = 0) -> dict[str, str]:
    """Most-recent hf-joinable disputed conditionId per category, from the released dispute layer."""
    import duckdb

    pq = os.path.join(ROOT, "dataset_release", "polymarket-oov2-disputes-v1", "disputes.parquet")
    out: dict[str, str] = {}
    for cat in dict.fromkeys(categories):
        rows = duckdb.execute(
            f"SELECT conditionId FROM '{pq}' WHERE hf_joinable AND category = ? "
            f"ORDER BY disputeTs DESC LIMIT 1 OFFSET {int(per_category_offset)}", [cat]).fetchall()
        if rows:
            out[cat] = rows[0][0]
    return out


def initial_quote(category: str) -> tuple[int, int, int, int]:
    """(bid6, ask6, lam_bps, sig_bps) from the REAL estimators. No fabricated fallback: if the
    estimators are unavailable we fail loud rather than seed a market with a made-up quote, so the
    on-chain initial quote is always a real estimate (nothing simulated ever touches the chain)."""
    from webapp.backend import services
    resp = services.score_market(category=category, fill_count=800, price=0.5,
                                 inventory=0.0, horizon_days=14.0)
    q = resp["quote"]
    return (int(round(q["bid"] * 1e6)), int(round(q["ask"] * 1e6)),
            int(round(resp["lambda"]["lambda_jump"] * 10000)),
            int(round(q["sigma"] * 10000)))


def main() -> None:
    ap = argparse.ArgumentParser(description="Deploy a PolyLambdaMarket fleet to Amoy")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--categories", default="politics,crypto")
    ap.add_argument("--collateral-usdc", type=float,
                    default=float(os.environ.get("ENGINE_COLLATERAL_USDC", "1")))
    ap.add_argument("--max-trade", type=float,
                    default=float(os.environ.get("ENGINE_MAX_TRADE", "0.5")))
    ap.add_argument("--horizon-days", type=float, default=14.0)
    ap.add_argument("--tracks", default="", help="comma-separated conditionIds to track (optional)")
    ap.add_argument("--registry", default=None, help="markets.json path (default: webapp registry)")
    args = ap.parse_args()

    from eth_account import Account
    from web3 import Web3

    from deploy_market import _ERC20_APPROVE, _env, _send, compile_market
    from execution.testnet_chain import AMOY_RPC, _rpc_retry, append_market, make_w3

    env = _env()
    key = env.get("ENGINE_PRIVATE_KEY")
    assert key, "ENGINE_PRIVATE_KEY missing — run scripts/gen_engine_wallet.py first"
    w3 = make_w3(AMOY_RPC)
    assert w3.is_connected() and w3.eth.chain_id == 80002, "not connected to Amoy (80002)"
    acct = Account.from_key(key)
    usdc_addr = Web3.to_checksum_address(
        os.environ.get("AMOY_USDC_ADDRESS", "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582"))
    usdc = w3.eth.contract(address=usdc_addr, abi=_ERC20_APPROVE)
    pol0 = _rpc_retry(w3.eth.get_balance, acct.address) / 1e18
    usdc0 = _rpc_retry(usdc.functions.balanceOf(acct.address).call) / 1e6
    print(f"engine {acct.address} | POL {pol0:.4f} | USDC {usdc0:.2f}")

    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    tracks = [t.strip() for t in args.tracks.split(",") if t.strip()]
    if not tracks:
        try:
            by_cat = pick_tracked_cids(cats)
        except Exception as e:  # noqa: BLE001
            print(f"tracked-cid picker unavailable ({e}); markets will track nothing")
            by_cat = {}
    abi, binn = compile_market()
    print("compiled PolyLambdaMarket (solc 0.8.24)")

    deployed = []
    for i in range(args.n):
        cat = cats[i % len(cats)]
        tracks_cid = tracks[i] if i < len(tracks) else by_cat.get(cat)
        print(f"\n[{i + 1}/{args.n}] deploy {cat} market (tracks {tracks_cid or '—'})")
        # compute the real estimator quote BEFORE spending any gas — if estimators are down this
        # fails loud here, so we never strand a deployed+funded market without a real quote.
        bid6, ask6, lam_bps, sig_bps = initial_quote(cat)
        C = w3.eth.contract(abi=abi, bytecode=binn)
        rcpt = _send(w3, acct, C.constructor(usdc_addr))
        addr = rcpt["contractAddress"]
        market = w3.eth.contract(address=addr, abi=abi)
        print(f"  deployed {addr}  ({EXPLORER}/address/{addr})")

        fund6 = int(args.collateral_usdc * 1e6)
        bal6 = _rpc_retry(usdc.functions.balanceOf(acct.address).call)
        fund6 = min(fund6, bal6)
        if fund6 > 0:
            _send(w3, acct, usdc.functions.approve(addr, fund6))
            _send(w3, acct, market.functions.fund(fund6))
            print(f"  funded {fund6 / 1e6:.2f} USDC collateral")
        else:
            print("  WARNING: no test-USDC for collateral — sells/redeems will be unfunded")

        mt6 = int(args.max_trade * 1e6)
        _send(w3, acct, market.functions.postQuote(bid6, ask6, mt6, cat, lam_bps, sig_bps))
        print(f"  initial quote {bid6 / 1e6:.4f}/{ask6 / 1e6:.4f} λ={lam_bps / 10000:.4f} "
              f"σ={sig_bps / 10000:.4f} maxTrade {mt6 / 1e6}")

        entry = {"address": addr, "deployed_block": rcpt["blockNumber"], "category": cat,
                 "tracks_cid": tracks_cid, "end_date_ts": time.time() + args.horizon_days * 86400.0,
                 "keeper_managed": True, "label": f"fleet-{cat}-{i}"}
        append_market(entry, abi=abi, path=args.registry)
        deployed.append(entry)

    pol1 = _rpc_retry(w3.eth.get_balance, acct.address) / 1e18
    usdc1 = _rpc_retry(usdc.functions.balanceOf(acct.address).call) / 1e6
    print(f"\n=== FLEET DEPLOYED ({len(deployed)} markets) ===")
    for e in deployed:
        print(f"  {e['category']:<10} {e['address']}  tracks {e['tracks_cid'] or '—'}")
    print(f"spent: {pol0 - pol1:.4f} POL gas, {usdc0 - usdc1:.2f} USDC collateral")
    print("registry: webapp/backend/markets.json — start the keeper with "
          "`python -m execution.testnet_keeper --ticks 10 --interval 60`")


if __name__ == "__main__":
    main()
