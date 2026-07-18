"""
disputes — OOv2 dispute labels WITHOUT Docker (the piece HF lacks; DECISIONS.md #13).

HF indexes resolution outcomes but no OptimisticOracleV2 dispute events, so λ + the replay-ablation
need dispute labels from elsewhere. This pulls `DisputePrice` logs straight from Polygon via a keyless
public RPC (`eth_getLogs`, no Docker, no indexer), decodes the ancillaryData, derives the CTF
`conditionId`, and joins to the HF dataset.

VERIFIED derivation (validated against HF `condition.id` only — NOT market_data, which is missing
~132k FPMM-era/unlabeled conditions; 29/29 in an early spot-check, then 723/723 on the full backfill):
  questionId  = keccak256(ancillaryData)                         # as emitted by the OO event
  conditionId = keccak256(adapter ++ questionId ++ uint256(2))   # Gnosis CTF getConditionId
This is the Python twin of indexer/src/lib.ts:deriveConditionId — the SAME formula.

HARD LIMITATION (measured, not assumed): the derivation holds for the standard **UMA CTF Adapter V2**
and **Legacy** adapters, but NOT for **NegRisk** (0/56 join across every keccak variant). NegRisk
assigns sequential questionIds via NegRiskIdLib at market-prep time, so conditionId is NOT a function
of the OO ancillaryData — it can only be recovered from the NegRiskAdapter's own on-chain events
(what the scoped local indexer reads). NegRisk disputes are therefore COUNTED but not label-joined
BY THIS RPC KECCAK FALLBACK; the default load_disputes() path (released parquet, or the indexer +
data/negrisk_map.py) joins them 100% — see the CORRECTION below. The raw RPC source skews to the
2023–2024 V2 era (which is exactly where HF's market_data overlaps anyway).

CORRECTION (2026-07-05) to the earlier "NegRisk structurally absent from HF" claim: that finding was
an artifact of PHANTOM conditionIds. Our indexer's QuestionInitialized handler keccak-falls-back to
`deriveConditionId(0x2f5e…, umaQuestionID)` for NegRisk — an id that never exists on-chain (no
ConditionPreparation anywhere), which is what joined 0%. The TRADEABLE NegRisk conditions (oracle =
NegRiskAdapter 0xd91E…) ARE fully present in HF; the UMA→tradeable bridge is recovered on-chain from
NegRiskOperator QuestionPrepared logs by `data/negrisk_map.py` (132,004 questions, 100% in HF).
`load_disputes_from_indexer` applies that map: each row carries `tradeableConditionId`, and
`hf_joinable` is computed on the EFFECTIVE key (tradeable-or-raw) — final release joins 100% across
all adapters (V2 723/723, NegRisk 963/963, other 108/108).

Timestamps: the indexer's `Dispute.disputeTs` is the OO REQUEST timestamp (event.params.timestamp),
which can precede the dispute tx by hours. `dispute_block_timestamps` recovers the TRUE block time per
dispute tx (Dispute.id = txHash-logIndex → receipt → block), cached; `load_disputes_from_indexer`
transparently overrides `disputeTs` with it and keeps the original as `requestTimestamp`.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from eth_abi import decode as abi_decode
from eth_utils import keccak

from .hf import query, table_path

OOV2 = "0xeE3Afe347D5C74317041E2618C49534dAf887c24"
DISPUTE_TOPIC0 = "0x" + keccak(text="DisputePrice(address,address,address,bytes32,uint256,bytes,int256)").hex()

# Adapters whose conditionId derives as keccak(adapter ++ keccak(ancillary) ++ 2) — VALIDATED against
# HF. The VALUE is the label written to the released `adapter` column, so it must match what the
# indexer path produces (`ADAPTER_OF.get(oracle) or oracle`) or a re-export silently rewrites history.
DERIVABLE = {
    "0x6a9d222616c90fca5754cd1333cfd9b7fb6a4f74": "v2",
    "0x71392e133063cc0d16f40e1f9b60227404bc03f7": "legacy",   # 0 rows in the current release
    # A third UMA CTF adapter, live 2025-07-05 → 2026-01-29, carrying 108 released disputes across
    # politics/crypto/sports/… It is NOT in indexer/config.yaml, so the indexer never mapped it to a
    # friendly name and the release stores the RAW ADDRESS in `adapter` — hence the odd-looking label
    # here: renaming it would silently rewrite those 108 rows on the next export. Naming it is a
    # separate, deliberate change. Validated against the shipped release: 108/108 hf_joinable and
    # 108/108 satisfy conditionId == keccak(adapter ++ questionId ++ 2), i.e. it derives exactly like
    # V2 (its OO `requester` IS its Market.oracle). Omitting it made the RPC export drop all 108.
    "0x157ce2d672854c848c9b79c49a8cc6cc89176a49": "0x157ce2d672854c848c9b79c49a8cc6cc89176a49",
}
NEGRISK = "0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d"  # counted-only in the RPC keccak fallback; label-joined via negrisk_map in the indexer/release paths

RPC_URL = os.environ.get("POLYGON_RPC_URL") or "https://polygon.gateway.tenderly.co"
# public fallbacks (keyless) tried in order after the primary — one bad free endpoint shouldn't kill
# the live feed. Matches the indexer/config.yaml rpc: block.
RPC_URLS = [RPC_URL] + [u for u in (
    "https://polygon-bor-rpc.publicnode.com", "https://polygon-rpc.com") if u != RPC_URL]
START_BLOCK = int(os.environ.get("DISPUTE_START_BLOCK", "33000000"))   # ~early 2022
HF_CUTOFF_BLOCK = int(os.environ.get("HF_CUTOFF_BLOCK", "85948287"))   # dataset head; markets past it aren't in HF
# The HF head as a TIMESTAMP: the block time of HF_CUTOFF_BLOCK, read from chain (2026-04-24 07:43:38Z).
# This is the authoritative cutoff date — DATASET.md's "2026-04-09" is wrong; dataset_release/README's
# 2026-04-24 is right. Hardcoded (not fetched) so this module stays import-pure and offline.
HF_CUTOFF_TS = int(os.environ.get("HF_CUTOFF_TS", "1777016618"))
CHUNK = 500_000
CACHE = os.path.join(os.environ.get("DATA_CACHE_DIR", ".data_cache"), "disputes.json")

# The git-tracked released dispute layer: the COMPLETE, 100%-HF-joinable set across ALL adapters,
# keyed by the HF-EFFECTIVE conditionId (NegRisk already mapped to its tradeable cid). This is the
# offline default numerator source for load_disputes().
#   1,848 rows total, running to chain head — of which 1,794 are INSIDE the HF window
#   (V2 723 + NegRisk 963 + other 108) and 54 are past it, marked post_hf_cutoff.
# load_disputes() serves only the 1,794: the λ denominator is an HF snapshot frozen at HF_CUTOFF_TS,
# so the numerator must be bounded to match. The extra 54 exist for the explorer's recency.
RELEASE_PARQUET = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "dataset_release", "polymarket-oov2-disputes-v1", "disputes.parquet")

# --- local Envio indexer (Hasura) source: covers V2 + NegRisk + Legacy via the ConditionPreparation
# lookup (authoritative conditionId), NOT keccak derivation. See load_disputes_from_indexer. ---
GRAPHQL_URL = os.environ.get("GRAPHQL_URL", "http://localhost:8080/v1/graphql")
HASURA_SECRET = os.environ.get("HASURA_ADMIN_SECRET", "testing")
# Envio's hosted HyperIndex deploy — the fallback when the local Docker indexer is down. It rejects
# the admin-secret header (send none) and is row-capped at 1000 with aggregates off, so callers that
# need the full universe must treat a hosted hit as coverage-capped, not authoritative.
# Opt-in ONLY — no baked default. The old hosted dev deploy (indexer.dev.hyperindex.xyz/0638687) is
# GONE (free tier ended); keeping it as a default just made resolve_indexer() burn a 15s timeout
# probing a corpse on every call. Set this to your own indexer if you run one.
HOSTED_GRAPHQL_URL = os.environ.get("HOSTED_GRAPHQL_URL", "")
# Market.oracle (lowercased) -> adapter label. Reuses the RPC-path adapter set (V2/Legacy) + NegRisk.
ADAPTER_OF = {**DERIVABLE, NEGRISK: "negrisk"}


def _pad(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().replace("0x", "")


# Transient signatures worth pausing for. Keyless public RPCs throttle PROGRESSIVELY under a batch job
# (a full export is ~60 log queries + ~1.8k block lookups), so all three endpoints can be rate-limited
# at the same moment — failover alone then raises and kills a 20-minute job. Backoff turns that into a
# pause. A genuine JSON-RPC error (e.g. "range too large") is NOT transient: it must surface fast so
# the caller's range-bisection can react.
_RPC_TRANSIENT = ("401", "429", "403", "500", "502", "503", "504", "Unauthorized", "Too Many",
                  "timed out", "Connection", "reset", "Remote end closed")
_RPC_ATTEMPTS = int(os.environ.get("RPC_ATTEMPTS", "4"))


def _rpc_once(url: str, body: bytes, timeout: int):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (operator-supplied endpoints)
        out = json.loads(r.read())
    if "error" in out:
        raise RuntimeError(out["error"])                    # JSON-RPC error (e.g. range too large)
    return out["result"]


def _rpc(method: str, params: list, timeout: int = 60):
    """JSON-RPC with endpoint failover AND bounded retry/backoff on transient throttling.

    Order: try every endpoint once; if they all failed transiently, sleep (exponential + jitter) and go
    round again. Non-transient errors (JSON-RPC application errors) raise immediately on the last
    endpoint so `_get_oov2_logs_resilient` can bisect the block range instead of sleeping pointlessly.
    """
    import random
    import time as _t

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last_err: Exception | None = None
    for attempt in range(_RPC_ATTEMPTS):
        transient_only = True
        for url in RPC_URLS:                               # endpoint failover (keyless publics)
            try:
                return _rpc_once(url, body, timeout)
            except Exception as e:  # noqa: BLE001 — try the next endpoint, remember the last failure
                last_err = e
                if not any(t in str(e) for t in _RPC_TRANSIENT):
                    transient_only = False
        if not transient_only or attempt == _RPC_ATTEMPTS - 1:
            break                                          # a real error, or out of attempts
        _t.sleep(min(2 ** attempt, 8) + random.random())   # every endpoint throttled → pause, retry
    raise last_err if last_err else RuntimeError("no RPC endpoint available")


def derive_condition_id(adapter: str, ancillary: bytes) -> str:
    """conditionId = keccak256(adapter ++ keccak256(ancillary) ++ uint256(2)). VALIDATED vs HF."""
    addr = bytes.fromhex(adapter.lower().replace("0x", "").rjust(40, "0"))
    qid = keccak(ancillary)
    return "0x" + keccak(addr + qid + (2).to_bytes(32, "big")).hex()


def _block_timestamps(blocks: list[int]) -> dict[int, int]:
    """Fetch block timestamps (epoch seconds) for the given blocks, one call each (disputes are rare)."""
    ts: dict[int, int] = {}
    for b in blocks:
        blk = _rpc("eth_getBlockByNumber", [hex(b), False])
        ts[b] = int(blk["timestamp"], 16)
    return ts


def fetch_oov2_disputes(start_block: int = START_BLOCK, end_block: int = HF_CUTOFF_BLOCK,
                        *, log=print) -> dict:
    """Scan DisputePrice logs on OOv2 (all Polymarket adapters) in [start, end]. Returns
    {derivable: [{conditionId, adapter, block, disputer, proposedPrice}], negrisk_count: int}."""
    derivable: list[dict] = []
    negrisk_count = 0
    frm = start_block
    adapters_topic = [_pad(a) for a in list(DERIVABLE) + [NEGRISK]]
    while frm <= end_block:
        to = min(frm + CHUNK, end_block)
        logs = _rpc("eth_getLogs", [{"address": OOV2, "topics": [DISPUTE_TOPIC0, adapters_topic],
                                     "fromBlock": hex(frm), "toBlock": hex(to)}])
        for lg in logs:
            adapter = "0x" + lg["topics"][1][-40:]
            if adapter == NEGRISK:
                negrisk_count += 1
                continue
            _, _, ancillary, price = abi_decode(["bytes32", "uint256", "bytes", "int256"],
                                                bytes.fromhex(lg["data"][2:]))
            derivable.append({
                "conditionId": derive_condition_id(adapter, ancillary),
                "adapter": DERIVABLE[adapter],
                "block": int(lg["blockNumber"], 16),
                "disputer": "0x" + lg["topics"][3][-40:],
                "proposedPrice": str(price),
            })
        if log:
            log(f"  [{frm}..{to}] derivable={len(derivable)} negrisk={negrisk_count}")
        frm = to + 1
    return {"derivable": derivable, "negrisk_count": negrisk_count}


def chain_head_block() -> int:
    """Current Polygon head block number (the RPC liveness proof)."""
    return int(_rpc("eth_blockNumber", []), 16)


def chain_head_ts() -> int:
    """Timestamp (epoch seconds) of the current Polygon head block."""
    blk = _rpc("eth_getBlockByNumber", ["latest", False])
    return int(blk["timestamp"], 16)


def _get_oov2_logs_resilient(frm: int, to: int, adapters_topic, *, timeout: int = 40, log=None) -> list:
    """eth_getLogs for OOv2 DisputePrice over [frm,to], halving the range on a transient RPC error
    (public RPCs intermittently -32005 'range too large' / -32603). Mirrors negrisk_map._get_logs_resilient."""
    try:
        return _rpc("eth_getLogs", [{"address": OOV2, "topics": [DISPUTE_TOPIC0, adapters_topic],
                                     "fromBlock": hex(frm), "toBlock": hex(to)}], timeout=timeout)
    except Exception as e:
        if to - frm <= 2_000:
            raise
        mid = (frm + to) // 2
        if log:
            log(f"    split [{frm}..{to}] after {str(e)[:60]}")
        return _get_oov2_logs_resilient(frm, mid, adapters_topic, timeout=timeout, log=log) + \
            _get_oov2_logs_resilient(mid + 1, to, adapters_topic, timeout=timeout, log=log)


def _decode_dispute_log(lg: dict) -> dict | None:
    """One OOv2 DisputePrice log → the fields an OO log can actually yield. None for non-Polymarket
    requesters. Shared by the live tail (recent_disputes_rpc) and the export (load_disputes_rpc).

    topics = [topic0, requester(adapter), proposer, disputer]; data = (identifier, timestamp,
    ancillaryData, proposedPrice). `questionId` is keccak(ancillaryData) — verified on-chain against
    the adapter's own QuestionInitialized.topic1, for NegRisk as well as V2/Legacy.
    """
    adapter = "0x" + lg["topics"][1][-40:]
    _identifier, oo_ts, ancillary, price = abi_decode(
        ["bytes32", "uint256", "bytes", "int256"], bytes.fromhex(lg["data"][2:]))
    if adapter in DERIVABLE:
        cid, label = derive_condition_id(adapter, ancillary), DERIVABLE[adapter]
    elif adapter == NEGRISK:
        cid, label = None, "negrisk"          # resolved via the Operator lookup by the caller
    else:
        return None                            # a non-Polymarket OO requester
    return {
        "conditionId": cid, "adapter": label,
        "questionId": "0x" + keccak(ancillary).hex(),
        "requestTimestamp": int(oo_ts),
        "proposedOutcome": _outcome_from_price(int(price)),
        "proposer": "0x" + lg["topics"][2][-40:],
        "disputer": "0x" + lg["topics"][3][-40:],
        "block": int(lg["blockNumber"], 16),
        "disputeId": f'{lg.get("transactionHash", "")}-{int(lg.get("logIndex", "0x0"), 16)}',
    }


def _outcome_from_price(price: int) -> str | None:
    """OO proposedPrice (int256, 1e18-scaled) → YES / NO / UNRESOLVABLE. None if off-grid."""
    if price >= 10**18:
        return "YES"
    if price == 0:
        return "NO"
    if price == 5 * 10**17:
        return "UNRESOLVABLE"
    return None


def recent_disputes_rpc(*, lookback_blocks: int = 4_500_000, target: int = 50,
                        window: int = 900_000, log=None) -> list[dict]:
    """The latest OOv2 disputes straight from Polygon via keyless RPC — the no-indexer live feed.

    Scans DisputePrice logs BACKWARD from the chain head in `window`-block steps (resilient bisection
    on RPC range errors) until `target` disputes are collected or `lookback_blocks` is exhausted.
    Returns rows in webapp `live.live_disputes` shape (newest first), with proposer (topic2) and the
    proposed outcome decoded. V2/Legacy conditionIds derive locally from the ancillaryData. NegRisk
    conditionIds are NOT a function of the OO ancillary (sequential NegRiskIdLib ids), so they are
    recovered on-chain via `negrisk_map.resolve_negrisk_cids` (requestTimestamp → QuestionInitialized →
    QuestionPrepared → derive) — pure RPC, no 36MB map, results cached. A NegRisk dispute that can't be
    resolved simply stays conditionId=None rather than getting a phantom id.

    Heavy (a multi-window scan); callers MUST cache it off the request path (see webapp/backend/live.py)."""
    head = chain_head_block()
    adapters_topic = [_pad(a) for a in list(DERIVABLE) + [NEGRISK]]
    floor = max(START_BLOCK, head - int(lookback_blocks))
    rows: list[dict] = []
    hi = head
    while hi >= floor and len(rows) < target:
        lo = max(floor, hi - int(window) + 1)
        logs = _get_oov2_logs_resilient(lo, hi, adapters_topic, log=log)
        for lg in logs:
            d = _decode_dispute_log(lg)
            if d is None:
                continue
            rows.append({
                "id": d["disputeId"], "round": None,
                "disputeTs": d["block"], "_block": d["block"],
                # NegRisk conditionIds aren't derivable from the ancillary, but the QUESTION id is —
                # keep it for the batched Operator lookup below (negrisk_map.resolve_negrisk_cids).
                "_qid": d["questionId"] if d["adapter"] == "negrisk" else None,
                "disputer": d["disputer"], "proposer": d["proposer"],
                "proposedOutcome": d["proposedOutcome"],
                "conditionId": d["conditionId"], "adapter": d["adapter"],
                "marketStatus": None, "finalOutcome": None, "outcomeSlotCount": None,
            })
        hi = lo - 1
    # attach TRUE block timestamps (disputeTs currently holds the block number as a placeholder)
    ts = _block_timestamps(sorted({r["_block"] for r in rows}))
    for r in rows:
        r["disputeTs"] = ts.get(r["_block"], 0)
        r.pop("_block", None)
    rows.sort(key=lambda r: r["disputeTs"], reverse=True)
    rows = rows[:target]
    # label the NegRisk rows (the dominant adapter in recent disputes) via ONE batched Operator lookup.
    # Best-effort: a failure leaves conditionId=None rather than blocking the feed.
    try:
        from .negrisk_map import resolve_negrisk_cids
        need = [r["_qid"] for r in rows if r["_qid"] and not r["conditionId"]]
        if need:
            labels = resolve_negrisk_cids(need, log=log)
            for r in rows:
                if r["_qid"] and not r["conditionId"]:
                    r["conditionId"] = labels.get(r["_qid"])
    except Exception:  # noqa: BLE001 — labeling is enrichment; the feed must still return
        pass
    for r in rows:
        r.pop("_qid", None)
    return rows


# {blockNumber: timestamp} for the RPC export. DISTINCT from BLOCK_TS_CACHE (defined further down),
# which is {txHash: timestamp} for the indexer path — this constant was originally *also* named
# BLOCK_TS_CACHE, so the later definition silently shadowed it and both this reader and its writer
# resolved to dispute_block_ts.json: the export would have overwritten a {txHash: ts} cache with
# {block: ts} entries under the same key space, corrupting it with no error. Different data, different
# key, different file — keep the names distinct. See test_block_ts_cache_paths_are_distinct.
RPC_BLOCK_TS_CACHE = os.path.join(os.environ.get("DATA_CACHE_DIR", ".data_cache"), "rpc_block_ts.json")


_BLOCK_TS_CHECKPOINT = 100
# Self-imposed pacing for the block-timestamp batch (~5 req/s by default). The keyless pool is
# effectively ONE endpoint — publicnode 403s and polygon-rpc 401s outright, so RPC_URLS' "failover" is
# decorative and tenderly alone must absorb ~1.8k calls. Unthrottled, it starts 401-ing partway
# through; backoff can't fix a sustained limit, only a burst. Applied HERE (the batch hot path) and not
# in _rpc, so the live feed's status probe stays instant. Set RPC_BATCH_DELAY_S=0 to disable.
_RPC_BATCH_DELAY_S = float(os.environ.get("RPC_BATCH_DELAY_S", "0.2"))


def _block_timestamps_cached(blocks: list[int], *, log=None) -> dict[int, int]:
    """{block: timestamp} with a persistent, CHECKPOINTED cache.

    A full export needs ~1.8k lookups (one `eth_getBlockByNumber` each — disputes are sparse, so there
    is no batch endpoint). Block times are immutable, so the cache is append-only and a re-export pays
    only for genuinely new blocks. It is flushed every _BLOCK_TS_CHECKPOINT entries AND on failure:
    writing only at the end (as this did) meant one throttled call near the finish discarded ~1,700
    successful lookups and forced a full restart — which is exactly how the first real export died.
    """
    cache: dict = {}
    try:
        if os.path.exists(RPC_BLOCK_TS_CACHE):
            with open(RPC_BLOCK_TS_CACHE) as f:
                cache = json.load(f)
    except Exception:  # noqa: BLE001 — a corrupt cache costs time, not correctness
        cache = {}

    def _flush() -> None:
        try:
            os.makedirs(os.path.dirname(RPC_BLOCK_TS_CACHE) or ".", exist_ok=True)
            with open(RPC_BLOCK_TS_CACHE, "w") as f:
                json.dump(cache, f)
        except Exception as e:  # noqa: BLE001 — caching is an optimization, never a requirement...
            # ...but it must not fail SILENTLY: a swallowed write error here is what let this function
            # spend ~1.7k RPC calls, log "checkpointed", and persist nothing at all.
            print(f"[disputes] WARNING: block-ts checkpoint write failed "
                  f"({type(e).__name__}: {e}) -> {RPC_BLOCK_TS_CACHE}", file=sys.stderr)

    todo = [b for b in blocks if str(b) not in cache]
    if log and todo:
        log(f"  block timestamps: {len(todo)} to fetch ({len(blocks) - len(todo)} cached)")
    import time as _t
    try:
        for i, b in enumerate(todo, 1):
            t0 = _t.monotonic()
            cache[str(b)] = int(_rpc("eth_getBlockByNumber", [hex(b), False])["timestamp"], 16)
            if i % _BLOCK_TS_CHECKPOINT == 0:
                _flush()
                if log:
                    log(f"  block timestamps {i}/{len(todo)} (checkpointed)")
            # pace ourselves: sleep only for the time the call did NOT already take
            if _RPC_BATCH_DELAY_S:
                _t.sleep(max(0.0, _RPC_BATCH_DELAY_S - (_t.monotonic() - t0)))
    finally:
        if todo:
            _flush()                                    # keep partial progress even if we blew up
    return {b: cache[str(b)] for b in blocks if str(b) in cache}


def load_disputes_rpc(start_block: int = START_BLOCK, end_block: int | None = None, *,
                      window: int = 900_000, log=print) -> list[dict]:
    """The full dispute layer straight from Polygon — the indexer-free source for the release export.

    Returns rows in the SAME shape `load_disputes_from_indexer` yields (conditionId, disputeId,
    disputeTs, requestTimestamp, adapter, disputer, proposer, proposedOutcome, round, questionId,
    tradeableConditionId, hf_joinable), so `export_disputes.build_rows` is source-agnostic.

    Three things the OO log can't give directly, and how they're recovered:
      * NegRisk conditionId — not derivable from the ancillary (sequential NegRiskIdLib ids); recovered
        on-chain via the Operator's QuestionPrepared (negrisk_map.resolve_negrisk_cids), validated
        963/963 against the released layer.
      * disputeTs — the OO `timestamp` field is the REQUEST time, which can precede the dispute tx by
        hours and would split pre/post fills wrongly. We use the true block time of the dispute log
        (cached), which is exactly what the dataset card promises — and what the indexer path only
        managed via an optional cache that nothing built in CI.
      * round — the two-strikes counter. A dispute resets the question and re-requests, so the n-th
        dispute on a questionId (ordered by time) IS round n. Derived here, ZERO-based to match the
        released schema (0 = first request).
    """
    end_block = end_block if end_block is not None else chain_head_block()
    adapters_topic = [_pad(a) for a in list(DERIVABLE) + [NEGRISK]]
    raw: list[dict] = []
    frm = start_block
    while frm <= end_block:
        to = min(frm + window, end_block)
        for lg in _get_oov2_logs_resilient(frm, to, adapters_topic, log=log):
            d = _decode_dispute_log(lg)
            if d is not None:
                raw.append(d)
        if log:
            log(f"  [{frm}..{to}] disputes={len(raw)}")
        frm = to + 1

    # NegRisk: recover the tradeable conditionId (one batched Operator lookup)
    try:
        from .negrisk_map import resolve_negrisk_cids
        need = [r["questionId"] for r in raw if r["adapter"] == "negrisk"]
        labels = resolve_negrisk_cids(need, log=log) if need else {}
    except Exception as e:  # noqa: BLE001
        labels = {}
        if log:
            log(f"  negrisk label lookup failed ({str(e)[:60]}); those rows stay unjoinable")
    for r in raw:
        if r["adapter"] == "negrisk":
            r["conditionId"] = labels.get(r["questionId"])
        r["tradeableConditionId"] = r["conditionId"]   # already the effective HF key on this path

    # TRUE dispute block time (not the OO request time)
    ts = _block_timestamps_cached(sorted({r["block"] for r in raw}), log=log)
    for r in raw:
        r["disputeTs"] = ts.get(r["block"], r["requestTimestamp"])

    # round = the n-th dispute on the same question, oldest first (the two-strikes semantic).
    # ZERO-BASED, matching the released schema ("0 = first request; bumps on each two-strikes reset").
    # A 1-based counter here silently makes EVERY row a reset round — caught by diffing against the
    # release, which has 245 in-window rows with round>0, not 1,794.
    seen: dict[str, int] = {}
    for r in sorted(raw, key=lambda x: (x["questionId"], x["disputeTs"], x["disputeId"])):
        r["round"] = seen.get(r["questionId"], 0)
        seen[r["questionId"]] = r["round"] + 1

    rows = [r for r in raw if r["conditionId"]]        # unresolvable NegRisk → dropped, never phantom
    if log and len(rows) != len(raw):
        log(f"  dropped {len(raw) - len(rows)} NegRisk disputes whose conditionId could not be recovered")
    _mark_hf_joinable(rows, log=log)
    for r in rows:
        r.pop("block", None)
    rows.sort(key=lambda r: r["disputeTs"])
    return rows


def _mark_hf_joinable(rows: list[dict], *, log=None) -> None:
    """Set `hf_joinable` = the effective conditionId exists in HF's `condition` table (in place)."""
    cids = list({(r.get("tradeableConditionId") or r["conditionId"]) for r in rows})
    joined: set[str] = set()
    cpath = table_path("condition")
    for i in range(0, len(cids), 5000):
        inl = ",".join(f"'{c}'" for c in cids[i:i + 5000])
        joined |= {x[0] for x in query(f"SELECT id FROM '{cpath}' WHERE id IN ({inl})")}
    for r in rows:
        r["hf_joinable"] = (r.get("tradeableConditionId") or r["conditionId"]) in joined
    if log:
        log(f"  hf_joinable: {sum(1 for r in rows if r['hf_joinable'])}/{len(rows)}")


def build_dispute_cache(*, refetch: bool = False, log=print) -> dict:
    """Fetch disputes (V2+Legacy), keep those that join to HF, attach timestamps, cache to disputes.json."""
    if os.path.exists(CACHE) and not refetch:
        with open(CACHE) as f:
            return json.load(f)
    raw = fetch_oov2_disputes(log=log)
    cids = list({d["conditionId"] for d in raw["derivable"]})
    # keep only disputes whose derived conditionId is actually in HF (drops mis-derived / non-Polymarket)
    joined = set()
    for i in range(0, len(cids), 5000):
        chunk = cids[i:i + 5000]
        inl = ",".join(f"'{c}'" for c in chunk)
        joined |= {r[0] for r in query(f"SELECT id FROM '{table_path('condition')}' WHERE id IN ({inl})")}
    kept = [d for d in raw["derivable"] if d["conditionId"] in joined]
    ts = _block_timestamps(sorted({d["block"] for d in kept}))
    for d in kept:
        d["disputeTs"] = ts.get(d["block"], 0)
    out = {"disputes": kept, "n_derivable_raw": len(raw["derivable"]), "n_joined": len(kept),
           "negrisk_count_unjoinable": raw["negrisk_count"]}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(out, f)
    return out


def _gql(query_str: str, *, url: str | None = None, secret: str | None = None, timeout: int = 60):
    """POST a GraphQL query to the local Hasura (admin secret auto-attached); return the `data` object."""
    url = url or GRAPHQL_URL
    secret = HASURA_SECRET if secret is None else secret
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["x-hasura-admin-secret"] = secret
    req = urllib.request.Request(url, data=json.dumps({"query": query_str}).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    if out.get("errors"):
        raise RuntimeError(out["errors"])
    return out["data"]


def resolve_indexer(graphql_url: str | None = None):
    """Pick a reachable indexer endpoint and return (url, secret), or (None, None) if none answers.

    Probe order: an explicit `graphql_url` (admin secret) → the local Hasura `GRAPHQL_URL` (admin
    secret) → the hosted HyperIndex deploy (which rejects the admin-secret header, so send none).
    The shared resolver so recon/export/hazard all degrade gracefully when local Docker is down —
    previously only hazard had this fallback. Probes each with a trivial ResolutionRequest query.

    NB: a hosted hit is COVERAGE-CAPPED (1000 rows, aggregates off) — callers needing the full
    universe (e.g. full recon) should say so in their logs rather than over-claim completeness."""
    candidates = []
    if graphql_url:
        candidates.append((graphql_url, None))
    candidates.append((GRAPHQL_URL, None))
    if HOSTED_GRAPHQL_URL:          # opt-in only — no dead default to probe
        candidates.append((HOSTED_GRAPHQL_URL, ""))
    for url, secret in candidates:
        try:
            _gql("{ ResolutionRequest(limit: 1) { id } }", url=url, secret=secret, timeout=15)
            return url, secret
        except Exception:
            continue
    return None, None


BLOCK_TS_CACHE = os.path.join(os.environ.get("DATA_CACHE_DIR", ".data_cache"), "dispute_block_ts.json")


def _load_block_ts_cache() -> dict[str, int]:
    """{txHash: blockTs} from the local cache; {} when not built (never scans the network)."""
    try:
        with open(BLOCK_TS_CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def dispute_block_timestamps(graphql_url: str | None = None, *, refetch: bool = False,
                             log=print) -> dict[str, int]:
    """Build {disputeTxHash: true block timestamp} for every indexer Dispute; cache to disk.

    The Dispute entity stores no block field, but its id is `txHash-logIndex` — so the tx hash is
    recoverable, and eth_getTransactionReceipt → eth_getBlockByNumber yields the true block time.
    ~1 RPC receipt per unique tx + 1 block fetch per unique block (blocks are deduped); incremental —
    already-cached txs are skipped unless refetch=True.
    """
    cache = {} if refetch else _load_block_ts_cache()
    ids = _gql("query { Dispute { id } }", url=graphql_url).get("Dispute", [])
    txs = sorted({(d.get("id") or "").rsplit("-", 1)[0] for d in ids} - {""} - set(cache))
    if log and txs:
        log(f"  resolving block timestamps for {len(txs)} dispute txs "
            f"({len(cache)} already cached)")
    block_of: dict[str, int] = {}
    for i, tx in enumerate(txs):
        try:
            rcpt = _rpc("eth_getTransactionReceipt", [tx])
            block_of[tx] = int(rcpt["blockNumber"], 16)
        except Exception as e:
            if log:
                log(f"  receipt failed for {tx[:14]}…: {str(e)[:80]}")
        if log and i and i % 200 == 0:
            log(f"  receipts {i}/{len(txs)}")
    ts_of_block: dict[int, int] = {}
    for b in sorted(set(block_of.values())):
        try:
            ts_of_block[b] = int(_rpc("eth_getBlockByNumber", [hex(b), False])["timestamp"], 16)
        except Exception as e:
            if log:
                log(f"  block {b} failed: {str(e)[:80]}")
    for tx, b in block_of.items():
        if b in ts_of_block:
            cache[tx] = ts_of_block[b]
    os.makedirs(os.path.dirname(BLOCK_TS_CACHE), exist_ok=True)
    with open(BLOCK_TS_CACHE, "w") as f:
        json.dump(cache, f)
    if log:
        log(f"  block-ts cache: {len(cache)} txs -> {BLOCK_TS_CACHE}")
    return cache


def load_disputes_from_indexer(graphql_url: str | None = None, *, secret: str | None = None,
                               joinable_only: bool = False, page: int = 1000, log=None) -> list[dict]:
    """Dispute labels from the scoped local Envio indexer (Hasura) — V2 + NegRisk + Legacy.

    `secret` follows the _gql convention: None → the local HASURA_SECRET, "" → no auth header
    (what the hosted deploy requires). Callers that resolved their endpoint via `resolve_indexer`
    should pass both the returned url and secret.

    Unlike the RPC path (V2/Legacy only, via keccak derivation), the indexer captures NegRisk disputes
    too. For NegRisk the indexer's conditionId is a PHANTOM (see module docstring); the on-chain
    tradeable conditionId is recovered via `data/negrisk_map.py` and carried as `tradeableConditionId`,
    and `hf_joinable` is computed on the EFFECTIVE key (tradeable-or-raw) — 100% joinable across all
    adapters once the map cache is built.

    `disputeTs` is the TRUE dispute block time when the block-ts cache exists (see
    `dispute_block_timestamps`); the indexer's raw value (the OO REQUEST timestamp, which can precede
    the dispute tx by hours) is preserved as `requestTimestamp`.

    Returns [{conditionId, tradeableConditionId, disputeId, disputeTs, requestTimestamp, adapter,
              disputer, proposer, proposedOutcome, round, questionId, hf_joinable}].
    `joinable_only=True` keeps only rows whose effective cid joins HF (fills + category available).
    """
    url = graphql_url or GRAPHQL_URL
    rows: list[dict] = []
    offset = 0
    while True:
        q = ("query { Dispute(limit: %d, offset: %d, order_by: {disputeTs: asc}) "
             "{ id disputer disputeTs round request { proposer proposedOutcome requestTimestamp "
             "market { id oracle questionId } } } }" % (page, offset))
        batch = _gql(q, url=url, secret=secret).get("Dispute", [])
        if not batch:
            break
        for d in batch:
            req = d.get("request") or {}
            mkt = req.get("market") or {}
            cid = mkt.get("id")
            if not cid:
                continue
            orc = (mkt.get("oracle") or "").lower()
            rows.append({
                "conditionId": cid,
                "disputeId": d.get("id"),                       # txHash-logIndex (block-ts join key)
                "disputeTs": int(d.get("disputeTs") or 0),
                "requestTimestamp": int(d.get("disputeTs") or 0),  # OO request ts (raw indexer value)
                # named adapter where known; else the raw oracle address (honest + traceable)
                "adapter": ADAPTER_OF.get(orc) or orc or "unknown",
                "disputer": d.get("disputer"),
                "proposer": req.get("proposer"),
                "proposedOutcome": req.get("proposedOutcome"),
                "round": d.get("round"),
                "questionId": mkt.get("questionId"),
            })
        offset += len(batch)
        if log:
            log(f"  fetched {offset} disputes from indexer")
        if len(batch) < page:
            break
    # NegRisk: the indexer's conditionId is a PHANTOM (keccak from the 0x2f5e OO adapter) that exists
    # nowhere on-chain — NegRisk markets TRADE under a conditionId whose oracle is the NegRiskAdapter
    # 0xd91E80cF…. Recover it from the NegRiskOperator map (keyed by the UMA questionId our rows carry)
    # so NegRisk disputes join HF exactly like V2/Legacy. Cache-only: if the map isn't built yet, NegRisk
    # rows keep tradeableConditionId=None and stay unjoinable (no regression, no implicit network scan).
    nmap: dict[str, dict] = {}
    try:
        from .negrisk_map import load_negrisk_map
        nmap = load_negrisk_map()
    except Exception as e:  # pragma: no cover - defensive
        if log:
            log(f"  negrisk map unavailable ({str(e)[:60]}); NegRisk falls back to phantom cid")
    for r in rows:
        if r["adapter"] == "negrisk":
            m = nmap.get(r.get("questionId") or "")
            r["tradeableConditionId"] = m["tradeableConditionId"] if m else None
        else:
            r["tradeableConditionId"] = r["conditionId"]  # V2/Legacy trade under the same conditionId

    # TRUE block time: the indexer's disputeTs is the OO REQUEST timestamp (event.params.timestamp),
    # not the dispute tx's block time — it can be hours earlier and would split pre/post fills wrongly.
    # Override from the cached txHash→blockTs map when present (built by dispute_block_timestamps);
    # cache-only here so a missing cache degrades to the request ts, never a network scan.
    bts = _load_block_ts_cache()
    if bts:
        for r in rows:
            tx = (r.get("disputeId") or "").rsplit("-", 1)[0]
            if tx in bts:
                r["disputeTs"] = int(bts[tx])

    # hf_joinable: membership of the EFFECTIVE join key (tradeable cid where recovered, else the
    # indexer conditionId) in the FULL HF `condition` table. Prefer the local cache when present
    # (a complete 1.117M-row copy, verified == remote), else the remote single file.
    join_cids = list({(r.get("tradeableConditionId") or r["conditionId"]) for r in rows})
    joined: set[str] = set()
    cpath = table_path("condition")
    for i in range(0, len(join_cids), 5000):
        inl = ",".join(f"'{c}'" for c in join_cids[i:i + 5000])
        joined |= {x[0] for x in query(f"SELECT id FROM '{cpath}' WHERE id IN ({inl})")}
    for r in rows:
        r["hf_joinable"] = (r.get("tradeableConditionId") or r["conditionId"]) in joined
    if joinable_only:
        rows = [r for r in rows if r["hf_joinable"]]
    return rows


def load_disputes() -> list[dict]:
    """[{conditionId, disputeTs, adapter, disputer}] for HF-joinable disputes WITHIN the HF window.

    WINDOW INVARIANT (load-bearing — this is the λ NUMERATOR's only choke point).
    The base rate is a two-source join: this numerator ÷ an HF-derived denominator
    (data.base_rates.category_counts_hf) whose resolution status was frozen when the HF snapshot was
    taken at HF_CUTOFF_BLOCK. `hf_joinable` alone does NOT keep the two aligned: it is a SPATIAL
    predicate ("does this conditionId exist in HF"), never temporal — a market prepared before the
    cutoff but disputed after it is hf_joinable=True. Such a market was probably still unresolved when
    HF froze, so it lands in n_markets but NOT n_resolved: appending it is numerator +1 / denominator
    +0, a selection bias that silently inflates the rate (measured: 12 boundary markets → +12 numerator
    / +7 denominator, 7 of them politics — the category carrying the headline claim).
    So we bound `disputeTs` by HF_CUTOFF_TS too, keeping both sides on the same window BY CONSTRUCTION
    however far the released layer is later extended. Today this filters nothing (the shipped layer's
    max disputeTs is ~6 days inside the cutoff) — it is a guard, not a behavior change.
    Post-cutoff disputes are still served to the EXPLORER via the live merge
    (webapp/backend/services._merged_disputes_df), which deliberately never feeds this path.

    Default (DATA_SOURCE=hf): the git-tracked released dispute layer (RELEASE_PARQUET) — the
    COMPLETE, 100%-joinable set across all adapters (V2 723 + NegRisk 963 + other 108 = 1,794),
    already keyed by the HF-effective conditionId, fully offline (no indexer, no NegRisk-map
    indirection at runtime). When DATA_SOURCE=graphql, source live from the local Envio indexer
    instead (V2+NegRisk+Legacy, joinable subset). Fallbacks on any failure: released parquet →
    the RPC-scanned V2/Legacy cache (723) so the numerator is never empty.
    """
    if os.environ.get("DATA_SOURCE") == "graphql":
        try:
            rows = load_disputes_from_indexer(joinable_only=True)
            if rows:
                # Use the EFFECTIVE HF join key so downstream fill fetches resolve for NegRisk too:
                # tradeableConditionId (recovered from the NegRisk map) for NegRisk, native cid otherwise.
                # Returning the phantom cid here would pass the joinable filter yet fetch zero fills.
                return [{"conditionId": r.get("tradeableConditionId") or r["conditionId"],
                         "disputeTs": r["disputeTs"],
                         "adapter": r["adapter"], "disputer": r["disputer"]} for r in rows]
        except Exception:
            pass  # indexer down → offline release/RPC cache below
    # offline default: the complete released dispute layer (effective cid, 100% HF-joinable, 1,794)
    if os.path.exists(RELEASE_PARQUET):
        try:
            rows = query(f"SELECT conditionId, disputeTs, adapter, disputer "
                         f"FROM '{RELEASE_PARQUET}' "
                         f"WHERE hf_joinable AND disputeTs <= {HF_CUTOFF_TS}")
            if rows:
                return [{"conditionId": c, "disputeTs": int(ts or 0), "adapter": a, "disputer": d}
                        for c, ts, a, d in rows]
        except Exception:
            pass  # parquet unreadable → RPC cache below
    # last resort: the RPC-scanned V2/Legacy cache (723) if the release parquet is absent
    data = build_dispute_cache()
    return [{"conditionId": d["conditionId"], "disputeTs": d.get("disputeTs", 0),
             "adapter": d["adapter"], "disputer": d["disputer"]} for d in data["disputes"]]


def dispute_counts_by_category() -> dict[str, int]:
    """{category: n_disputed_markets} — the λ NUMERATOR, joined to HF-derived category on conditionId."""
    from .metadata import category_case_sql

    disputes = load_disputes()
    cids = list({d["conditionId"] for d in disputes})
    if not cids:
        return {}
    inl = ",".join(f"'{c}'" for c in cids)
    # category per disputed condition FROM market_data — but market_data is missing ~132k FPMM-era /
    # unlabeled conditions, so some disputed conditionIds have no row here. We must NOT drop those:
    # look up the category where available, then default the rest to 'other' so ALL disputed markets
    # are counted (the earlier version silently dropped the unlabeled ones from the numerator).
    rows = query(f"""
        SELECT condition, any_value({category_case_sql()}) AS category
        FROM '{table_path('market_data')}'
        WHERE condition IN ({inl})
        GROUP BY condition
    """)
    cat_of = {cond: cat for cond, cat in rows}
    counts: dict[str, int] = {}
    for c in cids:
        cat = cat_of.get(c, "other")  # FPMM-era / unlabeled → 'other', never dropped
        counts[cat] = counts.get(cat, 0) + 1
    return counts


if __name__ == "__main__":
    disputes = load_disputes()
    src = "released parquet (all adapters)" if os.path.exists(RELEASE_PARQUET) else "RPC cache (V2/Legacy)"
    print(f"\ndefault dispute numerator source: {src}")
    print(f"HF-joinable disputes loaded: {len(disputes)}")
    by_adapter: dict[str, int] = {}
    for d in disputes:
        by_adapter[d["adapter"]] = by_adapter.get(d["adapter"], 0) + 1
    print(f"by adapter: {by_adapter}")
    print("by category:", dispute_counts_by_category())
