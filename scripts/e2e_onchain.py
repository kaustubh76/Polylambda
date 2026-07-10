"""End-to-end REAL on-chain proof of the PolyLambda testnet stack (Polygon Amoy).

Deploys an EPHEMERAL PolyLambdaMarket and drives the full lifecycle with real signed transactions —
engine (backend wallet) + a freshly-generated throwaway user — asserting every on-chain state
transition AND the hardened guards. The live demo market is untouched; this runs on its own instance.

Lifecycle:  fund → postQuote → (user) approve+buyYes → [guards revert] → flagDispute → resolve → redeem
Run:        AMOY_RPC_URL=https://polygon-amoy.drpc.org .venv/bin/python scripts/e2e_onchain.py
"""
from __future__ import annotations

import os
import re

from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from deploy_market import compile_market  # reuse the solc compile (same contract)

ROOT = os.path.dirname(os.path.dirname(__file__))
AMOY_RPC = os.environ.get("AMOY_RPC_URL", "https://polygon-amoy.drpc.org")
USDC_ADDR = Web3.to_checksum_address(os.environ.get("AMOY_USDC_ADDRESS", "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582"))
EXPLORER = "https://amoy.polygonscan.com"
GWEI = int(os.environ.get("AMOY_GAS_GWEI", "30"))

ERC20 = [
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "v", "type": "uint256"}], "outputs": [{"type": "bool"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "s", "type": "address"}, {"name": "v", "type": "uint256"}], "outputs": [{"type": "bool"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "a", "type": "address"}], "outputs": [{"type": "uint256"}]},
]


def _engine_key() -> str:
    # source ENGINE_PRIVATE_KEY from the shell env or the gitignored .env (never printed)
    key = os.environ.get("ENGINE_PRIVATE_KEY")
    if key:
        return key
    p = os.path.join(ROOT, ".env")
    for line in open(p) if os.path.exists(p) else []:
        m = re.match(r"\s*ENGINE_PRIVATE_KEY\s*=\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    raise SystemExit("ENGINE_PRIVATE_KEY not found (shell env or .env)")


def _link(h: str) -> str:
    return f"{EXPLORER}/tx/{h}"


class Chain:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(AMOY_RPC, request_kwargs={"timeout": 20}))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        assert self.w3.is_connected() and self.w3.eth.chain_id == 80002, "not connected to Amoy"
        self.txs: list[tuple[str, str]] = []

    def send(self, acct, fn=None, *, to=None, value=0, label=""):
        fee = self.w3.to_wei(GWEI, "gwei")
        base = {"from": acct.address, "nonce": self.w3.eth.get_transaction_count(acct.address),
                "chainId": 80002, "value": value, "maxFeePerGas": fee, "maxPriorityFeePerGas": fee}
        if fn is not None:
            tx = fn.build_transaction(base)
        else:
            tx = {**base, "to": Web3.to_checksum_address(to), "gas": 21000}
        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        h = self.w3.eth.send_raw_transaction(raw)
        rcpt = self.w3.eth.wait_for_transaction_receipt(h, timeout=180)
        hx = h.hex()
        hx = hx if hx.startswith("0x") else "0x" + hx
        assert rcpt["status"] == 1, f"{label} REVERTED: {hx}"
        if label:
            print(f"  ✓ {label:<28} {hx}")
            self.txs.append((label, hx))
        return rcpt

    def expect_revert(self, fn, frm, want: str, label: str):
        """Static eth_call that MUST revert with `want` — proves a guard without changing state."""
        try:
            fn.call({"from": frm})
        except Exception as e:  # noqa: BLE001
            ok = want in str(e)
            detail = f'"{want}"' if ok else f"UNEXPECTED: {str(e)[:60]}"
            print(f"  ✓ guard {label:<22} reverts ({detail})")
            assert ok, f"guard {label}: wrong revert — {e}"
            return
        raise AssertionError(f"guard {label}: NO revert (expected '{want}')")


def main() -> None:
    c = Chain()
    w3 = c.w3
    engine = Account.from_key(_engine_key())
    abi, binn = compile_market()
    usdc = w3.eth.contract(address=USDC_ADDR, abi=ERC20)
    bal = lambda a: usdc.functions.balanceOf(Web3.to_checksum_address(a)).call()
    U = 10 ** 6

    print(f"engine {engine.address} | POL {w3.from_wei(w3.eth.get_balance(engine.address),'ether'):.4f} | USDC {bal(engine.address)/U:.2f}")
    print("\n[1] deploy ephemeral market")
    C = w3.eth.contract(abi=abi, bytecode=binn)
    rc = c.send(engine, C.constructor(USDC_ADDR), label="deploy")
    addr = rc["contractAddress"]
    m = w3.eth.contract(address=addr, abi=abi)
    print(f"      market {addr}  ({EXPLORER}/address/{addr})")

    print("\n[2] engine funds collateral + posts a quote")
    c.send(engine, usdc.functions.approve(addr, 1 * U), label="engine approve USDC")
    c.send(engine, m.functions.fund(1 * U), label="fund 1 USDC")
    rc = c.send(engine, m.functions.postQuote(600_000, 640_000, 500_000, "politics", 183, 470), label="postQuote 0.60/0.64")
    assert len(m.events.QuotePosted().process_receipt(rc)) == 1, "no QuotePosted"
    assert m.functions.yesAsk().call() == 640_000

    print("\n[3] fund a throwaway USER, then approve + buyYes 0.3")
    user = Account.create()
    print(f"      user {user.address}")
    c.send(engine, to=user.address, value=w3.to_wei("0.03", "ether"), label="fund user POL")
    c.send(engine, usdc.functions.transfer(user.address, 500_000), label="fund user 0.5 USDC")
    esc0 = bal(addr)
    c.send(user, usdc.functions.approve(addr, 500_000), label="user approve USDC")
    rc = c.send(user, m.functions.buyYes(300_000), label="user buyYes 0.3")
    tr = m.events.Traded().process_receipt(rc)
    assert len(tr) == 1 and tr[0]["args"]["buy"] and tr[0]["args"]["size"] == 300_000, "bad Traded"
    cost = 300_000 * 640_000 // U  # 0.192 USDC
    assert m.functions.yesShares(user.address).call() == 300_000, "shares not credited"
    assert bal(addr) - esc0 == cost, f"escrow delta {bal(addr)-esc0} != {cost}"
    print(f"      user shares 0.30 YES | cost {cost/U:.3f} USDC | escrow {bal(addr)/U:.3f}")

    print("\n[4] hardened-guard checks (static — no state change)")
    c.expect_revert(m.functions.withdraw(1), engine.address, "unresolved", "withdraw pre-resolve")

    print("\n[5] engine flags dispute (λ-defense) → buys halt")
    rc = c.send(engine, m.functions.flagDispute(), label="flagDispute")
    assert m.functions.disputed().call() is True
    assert len(m.events.Disputed().process_receipt(rc)) == 1
    c.expect_revert(m.functions.buyYes(100_000), user.address, "closed", "buyYes after dispute")

    print("\n[6] engine resolves YES → settlement")
    rc = c.send(engine, m.functions.resolve(True), label="resolve YES")
    assert m.functions.resolved().call() is True and m.functions.yesWon().call() is True
    assert len(m.events.Resolved().process_receipt(rc)) == 1
    c.expect_revert(m.functions.resolve(False), engine.address, "resolved", "double-resolve")

    print("\n[7] user redeems 0.3 YES → 1:1 USDC payout")
    u0 = bal(user.address)
    rc = c.send(user, m.functions.redeem(), label="user redeem")
    rd = m.events.Redeemed().process_receipt(rc)
    assert len(rd) == 1 and rd[0]["args"]["payout"] == 300_000, "bad Redeemed"
    assert bal(user.address) - u0 == 300_000, "payout not received"
    assert m.functions.yesShares(user.address).call() == 0, "shares not zeroed"
    print(f"      user USDC +0.300 (redeemed 1:1) | shares now 0")

    print("\n=== ALL ON-CHAIN ASSERTIONS PASSED ===")
    print(f"market {addr}")
    for label, h in c.txs:
        print(f"  {label:<28} {_link(h)}")


if __name__ == "__main__":
    main()
