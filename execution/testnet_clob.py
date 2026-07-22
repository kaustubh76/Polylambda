"""testnet_clob — the REAL execution adapter for testnet mode (Polygon Amoy).

Presents the same duck-typed adapter interface as execution/paper.py, but place/cancel/step map to
REAL engine-signed transactions and REAL on-chain fills against a fleet of PolyLambdaMarket
contracts. This is the honesty inversion of the paper adapters: nothing here is simulated, and
every fill carries its transaction hash (queue_model="onchain").

The order-model -> single-quote mapping (the contract has no resting order book; the engine posts
ONE two-sided quote per market and users take against it):

    place(BUY, bid, s) + place(SELL, ask, s)  ->  buffered, collapsed into ONE postQuote(bid, ask,
                                                  maxTrade) signed at the next step()
    cancel(order_ids)                          ->  bookkeeping only (no resting orders on-chain)
    step(now)                                  ->  1) flush pending quotes (debounced + risk-gated)
                                                   2) poll Traded events -> engine fills:
                                                      user buyYes  = engine SELL at yesAsk
                                                      user sellYes = engine BUY  at yesBid

Debounce (the gas guard): a buffered quote is signed only if bid or ask moved >= min_requote_delta,
maxTrade changed, or the standing on-chain quote is older than max_quote_age_s. At 60s ticks this
bounds signing to ~100-200 tx/day instead of ~1,440.

Solvency bound: maxTrade <= escrow - totalYes (worst-case YES win must stay redeemable 1:1), so the
escrowed collateral — not the engine wallet — is the hard loss bound of a standing quote.

Every signed tx passes through the RiskGovernor (allow_tx / record_tx); RPC failures feed its error
breaker and never crash the loop.
"""
from __future__ import annotations

import time
from collections import deque

from execution.testnet_chain import FleetMarket  # noqa: F401  (public type of the `fleet` arg)

# Amoy has no Liquidity Rewards program: reward params are honestly ZERO, so forgone_rewards == 0
# and the reward-aware exit gate reduces to E[jump loss] > spread cost. min_order_size is small
# because quote sizes are collateral-bounded (~1 USDC escrow per market).
TESTNET_MICRO = {"tick_size": 0.001, "min_order_size": 0.05, "max_incentive_spread": 0.0,
                 "reward_min_size": 0.0, "rewards_daily_rate_usd": 0.0, "neg_risk": False,
                 "game_start_time": None}

_U = 10**6


class _MarketBook:
    """Per-market adapter state: pending quote sides, the standing posted quote, fill scan cursor."""

    def __init__(self, m, snap: dict | None):
        self.m = m
        self.pending: dict[str, tuple[float, float] | None] = {"BUY": None, "SELL": None}
        self.dirty = False
        self.oid: dict[str, str | None] = {"BUY": None, "SELL": None}
        # seed the standing quote from chain so a keeper restart doesn't re-sign an identical one
        if snap and snap.get("deployed"):
            self.last_posted = (snap["bid"], snap["ask"], snap["max_trade"], float(snap["quote_ts"]))
        else:
            self.last_posted = (0.0, 0.0, 0.0, 0.0)
        self.last_scanned = 0  # block; set on first step() from head - lookback
        self.tape_buf: deque = deque(maxlen=500)
        self.lam_bps = 0
        self.sig_bps = 0


class TestnetClob:
    __test__ = False  # not a pytest class, despite the name

    def __init__(self, fleet: list, signer, reader, *, risk,
                 min_requote_delta: float = 0.005, max_quote_age_s: float = 900.0,
                 confirmations: int = 3, max_trade_cap: float = 0.5,
                 snapshot_ttl_s: float = 2.0, fill_lookback_blocks: int = 5_000, log=None):
        self.signer = signer
        self.reader = reader
        self.risk = risk
        self.min_requote_delta = min_requote_delta
        self.max_quote_age_s = max_quote_age_s
        self.confirmations = confirmations
        self.max_trade_cap = max_trade_cap
        self.snapshot_ttl_s = snapshot_ttl_s
        self.fill_lookback_blocks = fill_lookback_blocks
        self.log = log or (lambda *a, **k: None)
        self._n = 0
        self._snap_cache: dict[str, tuple[float, dict]] = {}
        self.by_token: dict[str, _MarketBook] = {}
        self.by_cid: dict[str, _MarketBook] = {}
        self.tx_log: list[dict] = []            # every signed tx this session (for status/audit)
        self.last_denied: str = ""
        for m in fleet:
            snap = self._try_snapshot(m.address)
            mb = _MarketBook(m, snap)
            self.by_token[m.token_id] = mb
            self.by_cid[m.tracks_cid or m.token_id] = mb

    # -- chain helpers --------------------------------------------------------------------------
    def _try_snapshot(self, address: str, *, ttl: float | None = None) -> dict | None:
        now = time.monotonic()
        hit = self._snap_cache.get(address)
        if hit and now - hit[0] < (self.snapshot_ttl_s if ttl is None else ttl):
            return hit[1]
        try:
            snap = self.reader.snapshot(address)
        except Exception as e:  # noqa: BLE001 — RPC failure must never crash the loop
            self.risk.record_error(f"snapshot {address[:10]}: {e}")
            return hit[1] if hit else None
        self._snap_cache[address] = (now, snap)
        return snap

    def _send(self, kind: str, mb: _MarketBook, fn) -> dict | None:
        ok, reason = self.risk.allow_tx(kind, market=mb.m.address)
        if not ok:
            self.last_denied = f"{kind}: {reason}"
            self.log("risk_denied", kind=kind, market=mb.m.address, reason=reason)
            return None
        try:
            out = self.signer.send(fn)
            # only a successful SEND closes the breaker — read successes must not mask a
            # persistently failing signer (a signed tx proves the whole pipe works)
            self.risk.record_success()
        except Exception as e:  # noqa: BLE001
            # tag out-of-gas distinctly so it's not lumped with RPC/revert errors in the ledger
            oog = any(s in str(e).lower() for s in ("insufficient funds", "gas required", "out of gas"))
            self.risk.record_error(f"{kind} {mb.m.address[:10]}: {'OUT-OF-GAS: ' if oog else ''}{e}")
            return None
        self.risk.record_tx(kind, out["tx"], out["gas_pol"], market=mb.m.address)
        rec = {"kind": kind, "market": mb.m.address, **out}
        self.tx_log.append(rec)
        return out

    # -- adapter protocol -----------------------------------------------------------------------
    def get_book(self, token_id: str) -> dict:
        mb = self.by_token[token_id]
        snap = self._try_snapshot(mb.m.address)
        if (not snap or not snap.get("deployed") or snap.get("resolved")
                or snap["bid"] <= 0 or snap["ask"] <= 0 or snap["bid"] >= snap["ask"]):
            return {"bids": [], "asks": []}
        size = snap["max_trade"]
        return {"bids": [(snap["bid"], size)], "asks": [(snap["ask"], size)]}

    def get_micro(self, token_id: str) -> dict:
        return dict(TESTNET_MICRO)

    def tape(self, token_id: str) -> list:
        return list(self.by_token[token_id].tape_buf)

    def place(self, token_id: str, side: str, price: float, size: float, *,
              now_ts: float = 0.0) -> str:
        mb = self.by_token[token_id]
        self._n += 1
        oid = f"tn-{self._n}"
        mb.pending[side] = (price, size)
        mb.oid[side] = oid
        mb.dirty = True
        return oid

    def cancel(self, order_ids: list) -> None:
        # No resting orders on-chain — the standing quote stays until replaced. Bookkeeping only.
        return None

    def set_display(self, token_id: str, lam_bps: int, sig_bps: int) -> None:
        """λ/σ display fields for postQuote (emitted on-chain for the activity feed)."""
        mb = self.by_token[token_id]
        mb.lam_bps = int(lam_bps)
        mb.sig_bps = int(sig_bps)

    # -- quote flush ----------------------------------------------------------------------------
    def _flush_quotes(self, now_ts: float) -> None:
        for mb in self.by_token.values():
            if not mb.dirty or not mb.m.keeper_managed:
                mb.pending = {"BUY": None, "SELL": None}
                mb.dirty = False
                continue
            snap = self._try_snapshot(mb.m.address)
            if snap is None or not snap.get("deployed") or snap.get("resolved"):
                mb.pending = {"BUY": None, "SELL": None}
                mb.dirty = False
                continue
            tick = TESTNET_MICRO["tick_size"]
            buy, sell = mb.pending["BUY"], mb.pending["SELL"]
            bid = buy[0] if buy else tick                      # missing side -> economically dead
            ask = sell[0] if sell else 1.0 - tick
            sizes = [x[1] for x in (buy, sell) if x is not None]
            escrow_cap = max(0.0, snap["escrow_usdc"] - snap["total_yes"])  # solvency bound
            max_trade = min(min(sizes) if sizes else 0.0, self.max_trade_cap, escrow_cap)
            mb.pending = {"BUY": None, "SELL": None}
            mb.dirty = False
            if max_trade <= 0:
                continue
            lb, la, lmt, lts = mb.last_posted
            moved = (abs(bid - lb) >= self.min_requote_delta
                     or abs(ask - la) >= self.min_requote_delta
                     or abs(max_trade - lmt) > 1e-9)
            stale = (now_ts - lts) > self.max_quote_age_s
            if not moved and not stale:
                continue                                       # debounce: no tx this tick
            bid6, ask6, mt6 = int(round(bid * _U)), int(round(ask * _U)), int(round(max_trade * _U))
            if not (0 <= bid6 < ask6 <= _U):
                continue                                       # contract would revert
            c = self.reader.contract(mb.m.address)
            out = self._send("postQuote", mb,
                             c.functions.postQuote(bid6, ask6, mt6, mb.m.category,
                                                   mb.lam_bps, mb.sig_bps))
            if out is not None:
                mb.last_posted = (bid6 / _U, ask6 / _U, mt6 / _U, now_ts)
                self._snap_cache.pop(mb.m.address, None)       # snapshot is now stale

    # -- fills ----------------------------------------------------------------------------------
    def _poll_fills(self, now_ts: float) -> list[dict]:
        try:
            head = self.reader.head_block()
        except Exception as e:  # noqa: BLE001
            self.risk.record_error(f"head_block: {e}")
            return []
        safe_head = head - self.confirmations
        fills: list[dict] = []
        for mb in self.by_token.values():
            if mb.last_scanned == 0:
                mb.last_scanned = max(mb.m.deployed_block, safe_head - self.fill_lookback_blocks)
            if safe_head <= mb.last_scanned:
                continue
            try:
                events = self.reader.traded_logs(mb.m.address, mb.last_scanned + 1, safe_head)
            except Exception as e:  # noqa: BLE001
                self.risk.record_error(f"traded_logs {mb.m.address[:10]}: {e}")
                continue
            mb.last_scanned = safe_head
            for ev in events:
                if ev["size"] <= 0:
                    continue
                price = ev["usdc"] / ev["size"]                # exact traded price from the event
                side = "SELL" if ev["buy"] else "BUY"          # user buyYes = engine SELL, and v.v.
                self._n += 1
                fill = {"token_id": mb.m.token_id,
                        "order_id": mb.oid[side] or f"tn-onchain-{self._n}",
                        "side": side, "price": price, "size": ev["size"],
                        "timestamp": ev["timestamp"], "queue_model": "onchain",
                        "tx": ev["tx"], "block": ev["block"]}
                fills.append(fill)
                mb.tape_buf.append({"price": price, "size": ev["size"],
                                    "side": "BUY" if ev["buy"] else "SELL",  # taker's side
                                    "timestamp": ev["timestamp"]})
                self.risk.record_fill(mb.m.address, side, price, ev["size"])
        return fills

    def step(self, now_ts: float) -> list[dict]:
        self._flush_quotes(now_ts)
        return self._poll_fills(now_ts)

    # -- dispute defense --------------------------------------------------------------------------
    def flag_dispute_for(self, cid: str) -> dict | None:
        """Sign flagDispute() on the market tracking `cid` (idempotent; risk-gated)."""
        mb = self.by_cid.get(cid)
        if mb is None or not mb.m.keeper_managed:
            return None
        snap = self._try_snapshot(mb.m.address, ttl=0.0)
        if snap is None or snap.get("disputed") or snap.get("resolved"):
            return None
        c = self.reader.contract(mb.m.address)
        out = self._send("flagDispute", mb, c.functions.flagDispute())
        if out is not None:
            self._snap_cache.pop(mb.m.address, None)
        return out

    # -- status -----------------------------------------------------------------------------------
    def status(self) -> dict:
        return {"markets": {t: {"address": mb.m.address, "keeper_managed": mb.m.keeper_managed,
                                "last_posted": mb.last_posted, "last_scanned": mb.last_scanned}
                            for t, mb in self.by_token.items()},
                "tx_count": len(self.tx_log),
                "last_tx": self.tx_log[-1] if self.tx_log else None,
                "last_denied": self.last_denied}
