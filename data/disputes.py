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
import urllib.request

from eth_abi import decode as abi_decode
from eth_utils import keccak

from .hf import query, table_path

OOV2 = "0xeE3Afe347D5C74317041E2618C49534dAf887c24"
DISPUTE_TOPIC0 = "0x" + keccak(text="DisputePrice(address,address,address,bytes32,uint256,bytes,int256)").hex()

# adapters whose conditionId derives from keccak(ancillary) — VALIDATED against HF
DERIVABLE = {
    "0x6a9d222616c90fca5754cd1333cfd9b7fb6a4f74": "v2",
    "0x71392e133063cc0d16f40e1f9b60227404bc03f7": "legacy",
}
NEGRISK = "0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d"  # counted-only in the RPC keccak fallback; label-joined via negrisk_map in the indexer/release paths

RPC_URL = os.environ.get("POLYGON_RPC_URL") or "https://polygon.gateway.tenderly.co"
START_BLOCK = int(os.environ.get("DISPUTE_START_BLOCK", "33000000"))   # ~early 2022
HF_CUTOFF_BLOCK = int(os.environ.get("HF_CUTOFF_BLOCK", "85948287"))   # dataset head; markets past it aren't in HF
CHUNK = 500_000
CACHE = os.path.join(os.environ.get("DATA_CACHE_DIR", ".data_cache"), "disputes.json")

# The git-tracked released dispute layer: the COMPLETE, 100%-HF-joinable set across ALL adapters
# (V2 723 + NegRisk 963 + other 108 = 1,794), keyed by the HF-EFFECTIVE conditionId (NegRisk already
# mapped to its tradeable cid). This is the offline default numerator source for load_disputes().
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
HOSTED_GRAPHQL_URL = os.environ.get(
    "HOSTED_GRAPHQL_URL", "https://indexer.dev.hyperindex.xyz/0638687/v1/graphql")
# Market.oracle (lowercased) -> adapter label. Reuses the RPC-path adapter set (V2/Legacy) + NegRisk.
ADAPTER_OF = {**DERIVABLE, NEGRISK: "negrisk"}


def _pad(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().replace("0x", "")


def _rpc(method: str, params: list, timeout: int = 60):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    req = urllib.request.Request(RPC_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    if "error" in out:
        raise RuntimeError(out["error"])
    return out["result"]


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
    """[{conditionId, disputeTs, adapter, disputer}] for HF-joinable disputes.

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
                         f"FROM '{RELEASE_PARQUET}' WHERE hf_joinable")
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
