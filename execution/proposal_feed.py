"""proposal_feed — the confirmation-guarded live dispute detector for the testnet keeper.

Implements the `proposal_detector: cid -> bool` seam of execution/loop.py:run_loop, backed by the
keyless Polygon RPC dispute scan (data/disputes.py:recent_disputes_rpc) with the reorg-confirmation
guard the loop docstring demands: only disputes at least `confirmations` blocks deep count, so a
reorged/spurious DisputePrice log can never trigger the (irreversible) on-chain flagDispute.

Discipline mirrors webapp/backend/live.py: the heavy scan runs in a background thread on TTL
expiry; the hot path (inside tick) only ever reads the cached set — stale answers, never blocking.

The manual trigger file (one cid per line) is the demo/e2e lever: real Polymarket disputes are
rare, and the fleet's markets track specific conditionIds, so `echo <cid> >> DISPUTE_TRIGGERS`
is the only on-demand way to exercise the full defense chain end-to-end.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

MANUAL_PATH = ".data_cache/risk/DISPUTE_TRIGGERS"


class ConfirmedProposalDetector:
    def __init__(self, fleet: list, *, confirmations: int = 30, ttl_s: float = 600.0,
                 fetch=None, manual_path: str = MANUAL_PATH, clock=time.monotonic):
        self.confirmations = confirmations
        self.ttl_s = ttl_s
        self.manual_path = Path(manual_path)
        self.clock = clock
        # only cids the fleet actually tracks can ever fire — anything else is noise here
        self.watched = {m.tracks_cid for m in fleet if m.tracks_cid}
        self.watched |= {m.token_id for m in fleet}
        self._fetch = fetch or self._default_fetch
        self._cids: set[str] = set()
        self._until = 0.0
        self._error = ""
        self._lock = threading.Lock()
        self._refreshing = False

    def _default_fetch(self) -> list[dict]:
        from data.disputes import recent_disputes_rpc
        return recent_disputes_rpc(target=50, min_confirmations=self.confirmations)

    def _refresh(self) -> None:
        try:
            rows = self._fetch()
            cids = {r.get("conditionId") for r in rows if r.get("conditionId")}
            with self._lock:
                self._cids = cids
                self._error = ""
        except Exception as e:  # noqa: BLE001 — a failed scan serves the stale cache
            with self._lock:
                self._error = str(e)[:200]
        finally:
            with self._lock:
                self._until = self.clock() + self.ttl_s
                self._refreshing = False

    def _maybe_refresh_async(self) -> None:
        with self._lock:
            if self._refreshing or self.clock() < self._until:
                return
            self._refreshing = True
        threading.Thread(target=self._refresh, name="proposal-feed-scan", daemon=True).start()

    def _manual_cids(self) -> set[str]:
        try:
            return {ln.strip() for ln in self.manual_path.read_text().splitlines() if ln.strip()}
        except OSError:
            return set()

    def __call__(self, cid: str) -> bool:
        if cid in self._manual_cids():
            return True
        if cid not in self.watched:
            return False
        self._maybe_refresh_async()
        with self._lock:
            return cid in self._cids

    def status(self) -> dict:
        with self._lock:
            return {"confirmations": self.confirmations, "cached_disputes": len(self._cids),
                    "watched": len(self.watched), "ttl_until": self._until,
                    "error": self._error, "manual_path": str(self.manual_path)}
