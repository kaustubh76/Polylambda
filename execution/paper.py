"""
paper — the simulated execution adapters for `paper` and `paper-live` modes.

Both present the SAME adapter interface the loop consumes (duck-typed, no base class needed):
    get_book(token_id) -> {"bids": [(p, s), ...], "asks": [...]}
    get_micro(token_id) -> microstructure dict (tick_size, min_order_size, reward params, ...)
    place(token_id, side, price, size) -> order_id           (LOCAL simulation, never the network)
    cancel(order_ids) -> None
    step(now_ts) -> list[fill]                                (advance sim / poll tape -> fills)
    tape(token_id) -> recent fills for the sigma estimator

PaperClob:      fully synthetic. A seeded latent logit random walk per market drives the book and
                the taker flow; same seed => byte-identical session. Zero network by construction.
PaperLiveClob:  the REAL public book/tape via execution.clob's read path (no auth), with fills
                simulated by ConservativeFillModel — a resting order sits BEHIND all displayed
                same-price depth at placement, and fills only from prints through its price or
                at-price printed volume exceeding that queue. NEVER optimistic about queue position
                (JURISDICTION.md/runner.py honesty constraint); every fill is tagged
                queue_model="conservative".

Neither adapter can reach the write path: they import only the read functions from execution.clob.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@dataclass
class SimOrder:
    order_id: str
    token_id: str
    side: str          # BUY | SELL
    price: float
    size: float
    queue_ahead: float = 0.0
    placed_ts: float = 0.0


# Fixture microstructure for synthetic markets (paper mode).
SIM_MICRO = {"tick_size": 0.01, "min_order_size": 5.0, "max_incentive_spread": 0.03,
             "reward_min_size": 5.0, "rewards_daily_rate_usd": 50.0, "neg_risk": False,
             "game_start_time": None}


class SyntheticBook:
    """A seeded latent logit random walk with a 5-level book and Poisson-ish taker arrivals."""

    def __init__(self, token_id: str, *, seed: int = 7, p0: float = 0.5, sigma_step: float = 0.02,
                 tick: float = 0.01, depth: float = 200.0):
        self.token_id = token_id
        # stable per-market rng: same (seed, token_id) => same path (hash() is salted per process,
        # so derive the offset from the token string content instead)
        salt = sum(ord(c) * (i + 1) for i, c in enumerate(token_id))
        self.rng = random.Random(seed * 1_000_003 + salt)
        self.x = math.log(p0 / (1 - p0))
        self.sigma_step = sigma_step
        self.tick = tick
        self.depth = depth

    @property
    def mid(self) -> float:
        return min(max(_sigmoid(self.x), 0.02), 0.98)

    def advance(self) -> None:
        self.x += self.sigma_step * self.rng.gauss(0.0, 1.0)

    def snapshot(self) -> dict:
        m, t = self.mid, self.tick
        bids = [(round(max(t, m - t * (i + 1)), 4), self.depth * (1 - 0.15 * i)) for i in range(5)]
        asks = [(round(min(1 - t, m + t * (i + 1)), 4), self.depth * (1 - 0.15 * i)) for i in range(5)]
        return {"bids": bids, "asks": asks}

    def taker_prints(self, now_ts: float) -> list[dict]:
        """0-2 synthetic taker prints around the touch per step (the tape sigma sees)."""
        out = []
        for _ in range(self.rng.randint(0, 2)):
            side = self.rng.choice(("BUY", "SELL"))
            px = self.mid + (self.tick if side == "BUY" else -self.tick) * self.rng.random()
            out.append({"price": round(min(max(px, 0.01), 0.99), 4),
                        "size": round(5 + 45 * self.rng.random(), 2), "side": side,
                        "timestamp": int(now_ts), "maker": "0xsim-m", "taker": "0xsim-t"})
        return out


class PaperClob:
    """Fully synthetic adapter (paper mode): zero network, deterministic under a fixed seed."""

    def __init__(self, token_ids: list[str], *, seed: int = 7):
        self.books = {t: SyntheticBook(t, seed=seed, p0=0.35 + 0.1 * (i % 4))
                      for i, t in enumerate(token_ids)}
        self.orders: dict[str, SimOrder] = {}
        self._tapes: dict[str, list[dict]] = {t: [] for t in token_ids}
        self._n = 0

    def get_book(self, token_id: str) -> dict:
        return self.books[token_id].snapshot()

    def get_micro(self, token_id: str) -> dict:
        return dict(SIM_MICRO)

    def place(self, token_id: str, side: str, price: float, size: float, *, now_ts: float = 0.0) -> str:
        self._n += 1
        oid = f"sim-{self._n}"
        self.orders[oid] = SimOrder(oid, token_id, side.upper(), price, size, placed_ts=now_ts)
        return oid

    def cancel(self, order_ids: list[str]) -> None:
        for oid in order_ids:
            self.orders.pop(oid, None)

    def tape(self, token_id: str) -> list[dict]:
        return self._tapes[token_id][-500:]

    def step(self, now_ts: float) -> list[dict]:
        """Advance every market one step; return fills against our resting orders."""
        fills: list[dict] = []
        for tid, book in self.books.items():
            book.advance()
            prints = book.taker_prints(now_ts)
            self._tapes[tid].extend(prints)
            for pr in prints:
                for oid, o in list(self.orders.items()):
                    if o.token_id != tid:
                        continue
                    hit = (o.side == "BUY" and pr["side"] == "SELL" and pr["price"] <= o.price) or \
                          (o.side == "SELL" and pr["side"] == "BUY" and pr["price"] >= o.price)
                    if not hit:
                        continue
                    qty = min(o.size, pr["size"])
                    fills.append({"token_id": tid, "order_id": oid, "side": o.side,
                                  "price": o.price, "size": qty, "timestamp": int(now_ts),
                                  "queue_model": "synthetic"})
                    o.size -= qty
                    if o.size <= 1e-9:
                        self.orders.pop(oid, None)
        return fills


class ConservativeFillModel:
    """Queue-honest fill simulation against the REAL tape (paper-live).

    On placement: queue_ahead = ALL displayed size at (or better than, on our side) our price —
    we assume we are last in line. A resting BUY at p accumulates printed SELL-side volume at
    price <= p; it fills only for volume beyond queue_ahead. Prints strictly through the price
    drain the queue too (the level was swept)."""

    def queue_at(self, book: dict, side: str, price: float) -> float:
        levels = book["bids"] if side == "BUY" else book["asks"]
        if side == "BUY":
            return sum(s for p, s in levels if p >= price)
        return sum(s for p, s in levels if p <= price)

    def fills_for(self, order: SimOrder, prints: list[dict]) -> float:
        """Return fill qty for this order given new prints; mutates order.queue_ahead/size."""
        vol = 0.0
        for pr in prints:
            if order.side == "BUY" and pr["price"] <= order.price + 1e-12:
                vol += pr["size"]
            elif order.side == "SELL" and pr["price"] >= order.price - 1e-12:
                vol += pr["size"]
        if vol <= 0:
            return 0.0
        drain = min(order.queue_ahead, vol)
        order.queue_ahead -= drain
        avail = vol - drain
        qty = min(order.size, avail)
        order.size -= qty
        return qty


class PaperLiveClob:
    """Real public book/tape (read-only), simulated conservative fills. No write path imported."""

    def __init__(self, token_ids: list[str]):
        # read-path only — the gated write functions are never touched from here
        from execution.clob import get_market_microstructure, read_book, read_trades

        self._read_book, self._read_trades = read_book, read_trades
        self._get_micro = get_market_microstructure
        self.token_ids = token_ids
        self.orders: dict[str, SimOrder] = {}
        self.model = ConservativeFillModel()
        self._last_ts: dict[str, int] = {t: 0 for t in token_ids}
        self._tapes: dict[str, list[dict]] = {t: [] for t in token_ids}
        self._books: dict[str, dict] = {}
        self._n = 0

    def get_book(self, token_id: str) -> dict:
        book = self._read_book(token_id)
        self._books[token_id] = book
        return book

    def get_micro(self, token_id: str) -> dict:
        return self._get_micro(token_id)

    def place(self, token_id: str, side: str, price: float, size: float, *, now_ts: float = 0.0) -> str:
        self._n += 1
        oid = f"pl-{self._n}"
        book = self._books.get(token_id) or self.get_book(token_id)
        qa = self.model.queue_at(book, side.upper(), price)
        self.orders[oid] = SimOrder(oid, token_id, side.upper(), price, size,
                                    queue_ahead=qa, placed_ts=now_ts)
        return oid

    def cancel(self, order_ids: list[str]) -> None:
        for oid in order_ids:
            self.orders.pop(oid, None)

    def tape(self, token_id: str) -> list[dict]:
        return self._tapes[token_id][-500:]

    def step(self, now_ts: float) -> list[dict]:
        fills: list[dict] = []
        for tid in self.token_ids:
            try:
                prints = self._read_trades(tid, self._last_ts[tid])
            except Exception:
                prints = []  # degraded: no tape this tick (book-cross handled by re-quote logic)
            if prints:
                self._last_ts[tid] = max(p["timestamp"] for p in prints)
                self._tapes[tid].extend(prints)
            for oid, o in list(self.orders.items()):
                if o.token_id != tid or not prints:
                    continue
                qty = self.model.fills_for(o, prints)
                if qty > 0:
                    fills.append({"token_id": tid, "order_id": oid, "side": o.side,
                                  "price": o.price, "size": qty, "timestamp": int(now_ts),
                                  "queue_model": "conservative"})
                if o.size <= 1e-9:
                    self.orders.pop(oid, None)
        return fills
