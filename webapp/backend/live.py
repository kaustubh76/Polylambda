"""Live Envio HyperIndex GraphQL client — the real-time OOv2 dispute lifecycle.

Queries the hosted Envio HyperIndex (sub-second) for disputes as they are indexed on-chain, so the
dashboard is not limited to the released parquet snapshot. Pure stdlib `urllib` (no new dep), short
timeout, tiny TTL cache so a busy UI never hammers the endpoint. Every call degrades gracefully:
if the indexer is unreachable, the live panels show "offline" and the rest of the dashboard (which
runs off the shipped artifacts) is unaffected.

Endpoint is env-overridable so you can point at your own production indexer:
  INDEXER_GRAPHQL_URL  (preferred)  >  HOSTED_GRAPHQL_URL  >  the public dev deploy.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

ENDPOINT = (os.environ.get("INDEXER_GRAPHQL_URL")
            or os.environ.get("HOSTED_GRAPHQL_URL")
            or "https://indexer.dev.hyperindex.xyz/0638687/v1/graphql")
TIMEOUT = float(os.environ.get("INDEXER_TIMEOUT", "8"))
_TTL = 3.0  # seconds — a live feed polling every ~5s shares this cache

_cache: dict[str, tuple[float, dict]] = {}


def _gql(query: str, *, timeout: float = TIMEOUT) -> tuple[dict, float]:
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(ENDPOINT, data=body,
                                 headers={"content-type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https endpoint)
        payload = json.loads(resp.read())
    latency_ms = (time.monotonic() - t0) * 1000.0
    if payload.get("errors"):
        raise RuntimeError(str(payload["errors"][0].get("message", "graphql error")))
    return payload.get("data") or {}, latency_ms


def _cached(key: str, fn):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = fn()
    _cache[key] = (now + _TTL, val)
    return val


def indexer_status() -> dict:
    """Reachability + head timestamp + measured round-trip latency (the 'sub-second' proof)."""
    def fetch():
        try:
            data, ms = _gql("{ Dispute(limit: 1, order_by: {disputeTs: desc}) { id disputeTs } }")
            head = data.get("Dispute") or []
            return {"reachable": True, "endpoint": ENDPOINT, "latency_ms": round(ms, 1),
                    "head_ts": int(head[0]["disputeTs"]) if head else None,
                    "head_id": head[0]["id"] if head else None}
        except Exception as e:  # noqa: BLE001 — any failure → an honest "offline" card
            return {"reachable": False, "endpoint": ENDPOINT, "error": str(e)[:200]}
    return _cached("status", fetch)


def live_disputes(*, limit: int = 25, since_ts: int | None = None) -> dict:
    """The latest disputes straight from the indexer, joined to their request + market."""
    limit = max(1, min(int(limit), 100))
    where = f', where: {{disputeTs: {{_gt: "{int(since_ts)}"}}}}' if since_ts else ""
    q = ("{ Dispute(limit: %d%s, order_by: {disputeTs: desc}) { id round disputeTs disputer "
         "request { requestTimestamp proposedOutcome proposer bond "
         "market { id status finalOutcome outcomeSlotCount } } } }" % (limit, where))

    def fetch():
        try:
            data, ms = _gql(q)
            rows = []
            for x in data.get("Dispute", []):
                req = x.get("request") or {}
                mkt = req.get("market") or {}
                rows.append({
                    "id": x["id"], "round": x.get("round"), "disputeTs": int(x["disputeTs"]),
                    "disputer": x.get("disputer"),
                    "proposedOutcome": req.get("proposedOutcome"), "proposer": req.get("proposer"),
                    "conditionId": mkt.get("id"), "marketStatus": mkt.get("status"),
                    "finalOutcome": mkt.get("finalOutcome"),
                    "outcomeSlotCount": mkt.get("outcomeSlotCount"),
                })
            return {"reachable": True, "disputes": rows, "latency_ms": round(ms, 1),
                    "endpoint": ENDPOINT}
        except Exception as e:  # noqa: BLE001
            return {"reachable": False, "disputes": [], "error": str(e)[:200], "endpoint": ENDPOINT}
    return _cached(f"disputes:{limit}:{since_ts}", fetch)
