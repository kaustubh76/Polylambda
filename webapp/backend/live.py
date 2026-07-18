"""Live OOv2 dispute feed — source-agnostic (Envio HyperIndex GraphQL → keyless Polygon RPC → offline).

Two sources, tried in order:
  1. Envio GraphQL — ONLY if INDEXER_GRAPHQL_URL/HOSTED_GRAPHQL_URL is explicitly configured AND the
     endpoint is reachable AND fresh. (The old free-tier dev deploy has ended, so there is no baked-in
     default endpoint anymore — an unset env means "go straight to RPC".)
  2. Keyless Polygon RPC — `data.disputes.recent_disputes_rpc` scans OOv2 DisputePrice logs straight
     from the chain head. No indexer, no paid service. This is the "previous method" and the default.

Liveness for the RPC source is gated on the CHAIN HEAD (eth_blockNumber's block time ≈ now), not on the
latest dispute — disputes are sparse/bursty, so "at chain tip with no dispute for N days" is LIVE-but-
quiet, honestly reported (the latest-dispute age is shown separately). The heavy RPC tail scan runs in a
background thread behind a long TTL cache so it never blocks a request; the status probe stays cheap.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

# Envio is opt-in now — NO dead default endpoint. Unset → RPC path.
_ENVIO_URL = os.environ.get("INDEXER_GRAPHQL_URL") or os.environ.get("HOSTED_GRAPHQL_URL")
TIMEOUT = float(os.environ.get("INDEXER_TIMEOUT", "8"))
_TTL = 3.0                                   # status/GraphQL micro-cache (UI polls every ~5s)
ENVIO_FRESH_MAX_S = int(os.environ.get("ENVIO_FRESH_MAX_S", str(2 * 86400)))  # envio usable if head < this
_TAIL_TTL = float(os.environ.get("RPC_TAIL_TTL", "600"))                       # RPC tail refresh cadence
_TAIL_LOOKBACK = int(os.environ.get("RPC_TAIL_LOOKBACK_BLOCKS", "4500000"))    # ~HF cutoff → head
_TAIL_TARGET = int(os.environ.get("RPC_TAIL_TARGET", "60"))

_cache: dict[str, tuple[float, dict]] = {}


# ---------------------------------------------------------------------------------------------
# Envio GraphQL (optional)
# ---------------------------------------------------------------------------------------------
def _gql(query: str, *, timeout: float = TIMEOUT) -> tuple[dict, float]:
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(_ENVIO_URL, data=body, headers={"content-type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
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


def _envio_status() -> dict | None:
    """Envio head/latency IF configured, reachable, AND fresh; else None (→ caller falls back to RPC)."""
    if not _ENVIO_URL:
        return None

    def fetch():
        try:
            data, ms = _gql("{ Dispute(limit: 1, order_by: {disputeTs: desc}) { id disputeTs } }")
            head = data.get("Dispute") or []
            head_ts = int(head[0]["disputeTs"]) if head else None
            age = (int(time.time()) - head_ts) if head_ts else None
            fresh = age is not None and age <= ENVIO_FRESH_MAX_S
            return {"reachable": True, "source": "envio", "endpoint": _ENVIO_URL,
                    "latency_ms": round(ms, 1), "head_ts": head_ts,
                    "head_id": head[0]["id"] if head else None, "head_age_seconds": age,
                    "chain_head_ts": head_ts, "_fresh": fresh}
        except Exception as e:  # noqa: BLE001
            return {"reachable": False, "source": "envio", "endpoint": _ENVIO_URL, "error": str(e)[:200]}
    st = _cached("envio_status", fetch)
    return st if (st.get("reachable") and st.get("_fresh")) else None


def _envio_disputes(limit: int, since_ts: int | None) -> list[dict] | None:
    if not _ENVIO_URL:
        return None
    limit = max(1, min(int(limit), 100))
    where = f', where: {{disputeTs: {{_gt: "{int(since_ts)}"}}}}' if since_ts else ""
    q = ("{ Dispute(limit: %d%s, order_by: {disputeTs: desc}) { id round disputeTs disputer "
         "request { requestTimestamp proposedOutcome proposer bond "
         "market { id status finalOutcome outcomeSlotCount } } } }" % (limit, where))

    def fetch():
        try:
            data, _ = _gql(q)
            rows = []
            for x in data.get("Dispute", []):
                req = x.get("request") or {}
                mkt = req.get("market") or {}
                rows.append({
                    "id": x["id"], "round": x.get("round"), "disputeTs": int(x["disputeTs"]),
                    "disputer": x.get("disputer"), "proposedOutcome": req.get("proposedOutcome"),
                    "proposer": req.get("proposer"), "conditionId": mkt.get("id"),
                    "adapter": None, "marketStatus": mkt.get("status"),
                    "finalOutcome": mkt.get("finalOutcome"), "outcomeSlotCount": mkt.get("outcomeSlotCount")})
            return rows
        except Exception:  # noqa: BLE001
            return None
    return _cached(f"envio_disputes:{limit}:{since_ts}", fetch)


# ---------------------------------------------------------------------------------------------
# keyless Polygon RPC (default) — background-refreshed tail cache, never blocks a request
# ---------------------------------------------------------------------------------------------
_tail: dict = {"until": 0.0, "rows": [], "refreshing": False, "built_at": None, "error": None}
_tail_lock = threading.Lock()
# Lifecycle control for the background scan. Without this the daemon thread is fire-and-forget: it
# outlives the process/test that spawned it and, because it calls data.disputes._rpc, races any test
# that monkeypatches _rpc afterwards (the root cause of a flaky checkpoint test). stop_tail() sets the
# event (skip future spawns) and joins the in-flight thread; the app lifespan calls it on shutdown, so
# a TestClient context no longer leaves a live RPC thread behind.
_tail_stop = threading.Event()
_tail_threads: set[threading.Thread] = set()


def _enrich_live_names(rows: list[dict]) -> None:
    """Attach marketName/category to live dispute rows via a targeted HF market_data lookup, in place.

    Live disputes are brand-new markets, so they are NOT in the shipped dispute_market_context (built
    from the April release) — without this they'd show a bare conditionId forever. The lookup is keyed
    by conditionId (an `IN` over market_data: ~0.4s against the local parquet, ~13s against the Hub),
    runs only in the background tail thread, and is cached because the mapping is immutable. Degrades
    silently to no names when neither a local parquet nor an HF token is available.
    """
    from pathlib import Path
    cids = sorted({r["conditionId"] for r in rows if r.get("conditionId")})
    if not cids:
        return
    from .cache import DATA_CACHE, WEBAPP_CACHE
    path = WEBAPP_CACHE / "live_market_names.json"
    cached: dict = {}
    try:
        if path.exists():
            cached = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        cached = {}
    todo = [c for c in cids if c not in cached]
    if todo:
        try:
            from data.hf import has_hf_token, query, table_path
            from data.metadata import category_case_sql
            if not (has_hf_token() or (DATA_CACHE / "market_data").is_dir()):
                return                                   # no source for names on this host
            inl = ",".join(f"'{c}'" for c in todo)
            got = query(f"""SELECT condition, any_value(marketName), any_value({category_case_sql()})
                            FROM '{table_path('market_data')}'
                            WHERE condition IN ({inl}) GROUP BY condition""")
            for cid, name, cat in got:
                cached[cid] = {"marketName": name or None, "category": cat}
            for c in todo:                               # remember misses so we don't re-query forever
                cached.setdefault(c, {"marketName": None, "category": None})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cached))
        except Exception:  # noqa: BLE001 — enrichment is optional; the feed must still work
            return
    for r in rows:
        hit = cached.get(r.get("conditionId") or "")
        if hit:
            r["marketName"] = hit.get("marketName")
            r["category"] = hit.get("category")


def _refresh_tail_async() -> None:
    """Kick a background scan of the OOv2 dispute tail if the cache is stale and no scan is running."""
    now = time.monotonic()
    with _tail_lock:
        if _tail_stop.is_set() or _tail["refreshing"] or _tail["until"] > now:
            return                            # shutting down, a scan is running, or the cache is fresh
        _tail["refreshing"] = True

    def work():
        try:
            from data.disputes import recent_disputes_rpc
            rows = recent_disputes_rpc(lookback_blocks=_TAIL_LOOKBACK, target=_TAIL_TARGET)
            _enrich_live_names(rows)          # market names for the freshly-labeled NegRisk cids
            with _tail_lock:
                _tail.update(rows=rows, until=time.monotonic() + _TAIL_TTL,
                             built_at=int(time.time()), error=None)
        except Exception as e:  # noqa: BLE001 — keep last-good rows on failure
            with _tail_lock:
                _tail.update(until=time.monotonic() + 60, error=str(e)[:200])
        finally:
            with _tail_lock:
                _tail["refreshing"] = False
            _tail_threads.discard(threading.current_thread())

    t = threading.Thread(target=work, name="rpc-tail-scan", daemon=True)
    _tail_threads.add(t)
    t.start()


def stop_tail(timeout: float = 5.0) -> None:
    """Stop spawning tail scans and join any in-flight one. Call on app shutdown (and in test teardown)
    so the background RPC thread never outlives its owner and races a later _rpc monkeypatch. Idempotent."""
    _tail_stop.set()
    for t in list(_tail_threads):
        t.join(timeout=timeout)
    _tail_threads.clear()


def resume_tail() -> None:
    """Re-arm scans after a stop_tail() (e.g. a fresh TestClient in the same process). Idempotent."""
    _tail_stop.clear()


def warm_tail() -> None:
    """Trigger the first RPC tail scan (call from the app warm-up thread so users hit a warm cache)."""
    _refresh_tail_async()


def _rpc_tail_rows() -> list[dict]:
    """Current OOv2 dispute tail (newest-first). Non-blocking: triggers a background refresh and returns
    whatever is cached (possibly empty on a cold start; the explorer/stream self-heal once it lands)."""
    _refresh_tail_async()
    with _tail_lock:
        return list(_tail["rows"])


def _rpc_status() -> dict:
    """Cheap liveness: chain head (eth_blockNumber block time ≈ now) proves we're at tip; latest dispute
    ts from the tail cache is informational. Never runs the heavy scan on the request path."""
    def fetch():
        try:
            from data.disputes import chain_head_ts, RPC_URL
            t0 = time.monotonic()
            head_ts = chain_head_ts()
            ms = (time.monotonic() - t0) * 1000.0
            with _tail_lock:
                rows = _tail["rows"]
            latest = rows[0]["disputeTs"] if rows else None
            return {"reachable": True, "source": "rpc", "endpoint": RPC_URL, "latency_ms": round(ms, 1),
                    "chain_head_ts": head_ts, "head_ts": latest,
                    "head_id": (rows[0]["id"] if rows else None),
                    "head_age_seconds": max(0, int(time.time()) - head_ts)}
        except Exception as e:  # noqa: BLE001
            from data.disputes import RPC_URL
            return {"reachable": False, "source": "rpc", "endpoint": RPC_URL, "error": str(e)[:200]}
    return _cached("rpc_status", fetch)


# ---------------------------------------------------------------------------------------------
# public API (source-agnostic) — routes.py + services.py consume these
# ---------------------------------------------------------------------------------------------
def indexer_status() -> dict:
    """Reachability + head + latency, tagged with `source` ("envio"|"rpc"). `head_age_seconds` measures
    chain-head age for RPC (proves we're at tip) so a quiet-but-live feed reads LIVE, not stale."""
    st = _envio_status()
    if st is not None:
        st.pop("_fresh", None)
        _refresh_tail_async()  # keep the RPC tail warm as a hot standby even while Envio is healthy
        return st
    return _rpc_status()


def live_disputes(*, limit: int = 25, since_ts: int | None = None) -> dict:
    """Latest disputes from the active source (Envio if fresh, else the RPC tail)."""
    limit = max(1, min(int(limit), 100))
    rows = _envio_disputes(limit, since_ts) if _envio_status() is not None else None
    source = "envio"
    if rows is None:
        rows = _rpc_tail_rows()
        source = "rpc"
    if since_ts:
        rows = [r for r in rows if (r.get("disputeTs") or 0) > int(since_ts)]
    rows = rows[:limit]
    from data.disputes import RPC_URL
    return {"reachable": True, "disputes": rows, "source": source,
            "endpoint": (_ENVIO_URL if source == "envio" else RPC_URL)}


def recent_disputes(*, limit: int = 200, since_ts: int | None = None) -> list[dict]:
    """Latest disputes normalized to the disputes-explorer column shape, for the request-time union with
    the frozen parquet (services.disputes / disputes_analytics). Fields the live feed can't supply
    (marketName, category for Envio, pre/postDisputePrice, realizedJumpLogit) are None. Best-effort:
    returns [] when no source is available so the explorer never breaks."""
    from datetime import datetime, timezone
    feed = live_disputes(limit=min(int(limit), 100), since_ts=since_ts)
    rows: list[dict] = []
    for d in feed.get("disputes", []):
        ts = d.get("disputeTs")
        date = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d") if ts else None
        rows.append({
            "conditionId": d.get("conditionId"), "marketName": d.get("marketName"),
            "category": d.get("category"), "adapter": d.get("adapter"), "disputeDate": date, "disputeTs": ts,
            "proposedOutcome": d.get("proposedOutcome"),
            "preDisputePrice": None, "postDisputePrice": None, "realizedJumpLogit": None,
            "disputer": d.get("disputer"), "proposer": d.get("proposer"),
            "round": d.get("round"), "source": "live",
        })
    return rows
