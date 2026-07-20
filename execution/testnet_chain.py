"""testnet_chain — fleet registry + Amoy signing/read plumbing for testnet execution mode.

The fleet is a list of deployed PolyLambdaMarket contracts described by webapp/backend/markets.json
(shared abi + one entry per market). With no markets.json, load_fleet returns an empty fleet.

AmoySigner is the engine-signed write path — ENGINE_PRIVATE_KEY + chain_id==80002 guard, POA
middleware, nonce lock, AMOY_GAS_GWEI — plus a single nonce-refresh retry (the keeper shares the
engine wallet with the GH cron, so a cross-process nonce race is expected occasionally) and per-tx
gas accounting for the RiskGovernor's daily POL budget.

Everything network-touching goes through an injected w3, so tests never open a socket.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
MARKETS_JSON = _ROOT / "webapp" / "backend" / "markets.json"

AMOY_RPC = os.environ.get("AMOY_RPC_URL", "https://polygon-amoy.drpc.org")
AMOY_CHAIN_ID = 80002
EXPLORER = "https://amoy.polygonscan.com"
USDC_ADDR = os.environ.get("AMOY_USDC_ADDRESS", "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582")

_ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "a", "type": "address"}], "outputs": [{"type": "uint256"}]},
]

# tx errors that mean "our nonce view raced another signer on the same wallet" — refetch and retry once
_NONCE_RACE = ("nonce too low", "replacement transaction underpriced", "already known")


@dataclass
class FleetMarket:
    address: str
    deployed_block: int
    category: str
    token_id: str = ""                # loop key; defaults to tn-<addr[:10]>
    tracks_cid: str | None = None     # real Polymarket conditionId this market mirrors (dispute map)
    end_date_ts: float = 0.0          # feeds MarketState.end_date_ts (0 = far future at load time)
    keeper_managed: bool = True       # False = the keeper never signs on this market (the demo)
    label: str = ""

    def __post_init__(self):
        if not self.token_id:
            self.token_id = f"tn-{self.address[:10].lower()}"


def engine_key() -> str | None:
    """ENGINE_PRIVATE_KEY from the shell env or the gitignored .env (never printed/logged)."""
    key = os.environ.get("ENGINE_PRIVATE_KEY")
    if key:
        return key
    p = _ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            m = re.match(r"\s*ENGINE_PRIVATE_KEY\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return None


def load_fleet(path: str | os.PathLike | None = None) -> tuple[list[FleetMarket], list]:
    """Read the fleet registry -> (markets, shared_abi).

    Precedence: explicit path > MARKETS_JSON env > webapp/backend/markets.json.
    Missing everything -> ([], []).
    """
    p = Path(path or os.environ.get("MARKETS_JSON") or MARKETS_JSON)
    if p.exists():
        doc = json.loads(p.read_text())
        markets = [FleetMarket(
            address=m["address"], deployed_block=int(m.get("deployed_block") or 0),
            category=m.get("category") or "politics", token_id=m.get("token_id") or "",
            tracks_cid=m.get("tracks_cid"), end_date_ts=float(m.get("end_date_ts") or 0.0),
            keeper_managed=bool(m.get("keeper_managed", True)), label=m.get("label") or "",
        ) for m in doc.get("markets", [])]
        return markets, doc.get("abi", [])
    return [], []


def append_market(entry: dict, *, abi: list, path: str | os.PathLike | None = None) -> dict:
    """Append one market entry to the registry, creating it on first use.

    Never rewrites existing entries; duplicate addresses are refused. Returns the written document.
    """
    p = Path(path or os.environ.get("MARKETS_JSON") or MARKETS_JSON)
    if p.exists():
        doc = json.loads(p.read_text())
    else:
        doc = {"abi": abi, "markets": []}
    if any(m["address"].lower() == entry["address"].lower() for m in doc["markets"]):
        raise ValueError(f"market {entry['address']} already in registry")
    doc["markets"].append(entry)
    if not doc.get("abi"):
        doc["abi"] = abi
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1))
    return doc


def make_w3(rpc: str | None = None, *, timeout: float = 12.0):
    """Real Amoy web3 (POA middleware). Only called from live paths, never from tests."""
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware
    w3 = Web3(Web3.HTTPProvider(rpc or AMOY_RPC, request_kwargs={"timeout": timeout}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


class AmoySigner:
    """Engine-signed writes, testnet-guarded, nonce-locked, gas-accounted."""

    def __init__(self, w3, key: str | None = None):
        self.w3 = w3
        self._key = key if key is not None else engine_key()
        self._acct = None
        self._lock = threading.Lock()

    @property
    def acct(self):
        if self._acct is None and self._key:
            from eth_account import Account
            self._acct = Account.from_key(self._key)
        return self._acct

    @property
    def address(self) -> str | None:
        return self.acct.address if self.acct else None

    def send(self, fn, value: int = 0) -> dict:
        """Sign + send + wait. Returns {"tx", "gas_pol", "block"}. Raises on revert/guard."""
        if self.acct is None:
            raise RuntimeError("engine wallet not configured (ENGINE_PRIVATE_KEY missing)")
        if self.w3.eth.chain_id != AMOY_CHAIN_ID:
            raise RuntimeError("refusing to sign: connected chain is not Amoy (80002)")
        fee = self.w3.to_wei(int(os.environ.get("AMOY_GAS_GWEI", "30")), "gwei")
        last_err: Exception | None = None
        for attempt in (0, 1):  # second pass only for a cross-process nonce race
            try:
                with self._lock:
                    tx = fn.build_transaction({
                        "from": self.acct.address,
                        "nonce": self.w3.eth.get_transaction_count(self.acct.address),
                        "chainId": AMOY_CHAIN_ID, "value": value,
                        "maxFeePerGas": fee, "maxPriorityFeePerGas": fee})
                    signed = self.acct.sign_transaction(tx)
                    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
                    h = self.w3.eth.send_raw_transaction(raw)
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt == 0 and any(s in str(e).lower() for s in _NONCE_RACE):
                    time.sleep(0.5)
                    continue
                raise
        else:  # pragma: no cover — loop always breaks or raises
            raise last_err  # type: ignore[misc]
        rcpt = self.w3.eth.wait_for_transaction_receipt(h, timeout=120)
        if rcpt["status"] != 1:
            raise RuntimeError(f"amoy tx reverted: {h.hex()}")
        gas_price = rcpt.get("effectiveGasPrice", fee)
        hx = h.hex()
        return {"tx": hx if hx.startswith("0x") else "0x" + hx,
                "gas_pol": rcpt["gasUsed"] * gas_price / 1e18,
                "block": rcpt["blockNumber"]}


class ChainReader:
    """Read path over the fleet: snapshots, Traded logs, head block, engine balances."""

    def __init__(self, w3, abi: list):
        self.w3 = w3
        self.abi = abi
        self._contracts: dict[str, object] = {}
        self._block_ts: dict[int, int] = {}

    def contract(self, address: str):
        if address not in self._contracts:
            from web3 import Web3
            self._contracts[address] = self.w3.eth.contract(
                address=Web3.to_checksum_address(address), abi=self.abi)
        return self._contracts[address]

    _contract = contract  # internal alias

    def head_block(self) -> int:
        return self.w3.eth.block_number

    def snapshot(self, address: str) -> dict:
        s = self._contract(address).functions.snapshot().call()
        return {"deployed": True, "bid": s[0] / 1e6, "ask": s[1] / 1e6, "max_trade": s[2] / 1e6,
                "quote_ts": int(s[3]), "disputed": bool(s[4]), "resolved": bool(s[5]),
                "yes_won": bool(s[6]), "total_yes": s[7] / 1e6, "escrow_usdc": s[8] / 1e6,
                "category": s[9], "lambda_jump": s[10] / 10000, "sigma": s[11] / 10000}

    def _timestamp(self, block: int) -> int:
        if block not in self._block_ts:
            self._block_ts[block] = int(self.w3.eth.get_block(block)["timestamp"])
        return self._block_ts[block]

    def traded_logs(self, address: str, from_block: int, to_block: int) -> list[dict]:
        """Decoded Traded events in [from_block, to_block], oldest first."""
        if to_block < from_block:
            return []
        m = self._contract(address)
        out = []
        for lg in self.w3.eth.get_logs({"address": m.address,
                                        "fromBlock": from_block, "toBlock": to_block}):
            try:
                ev = m.events.Traded().process_log(lg)
            except Exception:  # noqa: BLE001 — other event types in the same address filter
                continue
            a = dict(ev["args"])
            tx = ev["transactionHash"].hex()
            out.append({"user": a["user"], "buy": bool(a["buy"]),
                        "size": a["size"] / 1e6, "usdc": a["usdc"] / 1e6,
                        "new_shares": a.get("newShares", 0) / 1e6,
                        "block": ev["blockNumber"], "log_index": ev["logIndex"],
                        "tx": tx if tx.startswith("0x") else "0x" + tx,
                        "timestamp": self._timestamp(ev["blockNumber"])})
        out.sort(key=lambda e: (e["block"], e["log_index"]))
        return out

    def balances(self, address: str) -> dict:
        from web3 import Web3
        addr = Web3.to_checksum_address(address)
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDR), abi=_ERC20_ABI)
        return {"pol": self.w3.eth.get_balance(addr) / 1e18,
                "usdc": usdc.functions.balanceOf(addr).call() / 1e6}
