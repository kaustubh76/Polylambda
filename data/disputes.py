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
here; run the local indexer to cover them. Polymarket moved most recent markets to NegRisk, so this
source skews to the 2023–2024 V2 era (which is exactly where HF's market_data overlaps anyway).

DEEPER STRUCTURAL FINDING (2026-07-03, from the live local indexer — `load_disputes_from_indexer`):
even when the indexer supplies the AUTHORITATIVE NegRisk conditionId (read directly from
ConditionPreparation, not derived), NegRisk conditions are STILL absent from HF entirely — a same-era
spot-check joined V2 disputes 147/147 (100%) but NegRisk 0/104 (0%), and HF `market_data` is not
head-lagged (1.85M rows, endDate -> 2028). So HF does not carry NegRisk trading data under the
underlying conditionId; NegRisk disputes are valuable as LABELS but have no HF fill tape to join.
That is why `load_disputes_from_indexer` TAGS each row `hf_joinable` instead of assuming a join.
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
NEGRISK = "0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d"  # counted, not label-joined (see module docstring)

RPC_URL = os.environ.get("POLYGON_RPC_URL") or "https://polygon.gateway.tenderly.co"
START_BLOCK = int(os.environ.get("DISPUTE_START_BLOCK", "33000000"))   # ~early 2022
HF_CUTOFF_BLOCK = int(os.environ.get("HF_CUTOFF_BLOCK", "85948287"))   # dataset head; markets past it aren't in HF
CHUNK = 500_000
CACHE = os.path.join(os.environ.get("DATA_CACHE_DIR", ".data_cache"), "disputes.json")

# --- local Envio indexer (Hasura) source: covers V2 + NegRisk + Legacy via the ConditionPreparation
# lookup (authoritative conditionId), NOT keccak derivation. See load_disputes_from_indexer. ---
GRAPHQL_URL = os.environ.get("GRAPHQL_URL", "http://localhost:8080/v1/graphql")
HASURA_SECRET = os.environ.get("HASURA_ADMIN_SECRET", "testing")
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


def load_disputes_from_indexer(graphql_url: str | None = None, *, joinable_only: bool = False,
                               page: int = 1000, log=None) -> list[dict]:
    """Dispute labels from the scoped local Envio indexer (Hasura) — V2 + NegRisk + Legacy.

    Unlike the RPC path (V2/Legacy only, via keccak derivation), the indexer reads the AUTHORITATIVE
    conditionId from ConditionPreparation, so NegRisk disputes are captured too. But NegRisk conditions
    are structurally ABSENT from the HF dataset (verified: same-era V2 join 100% / NegRisk 0%; HF is not
    head-lagged — see the module docstring / DATASET.md §5), so each row carries `hf_joinable`: True iff
    its conditionId is in HF `condition` (i.e. it has HF fills + a derivable category).

    Returns [{conditionId, disputeTs, adapter, disputer, proposer, proposedOutcome, round, questionId,
              hf_joinable}]. `joinable_only=True` keeps only the HF-joinable (V2/Legacy) subset — the
    set the replay + category base-rate can actually join to HF fills.
    """
    url = graphql_url or GRAPHQL_URL
    rows: list[dict] = []
    offset = 0
    while True:
        q = ("query { Dispute(limit: %d, offset: %d, order_by: {disputeTs: asc}) "
             "{ disputer disputeTs round request { proposer proposedOutcome requestTimestamp "
             "market { id oracle questionId } } } }" % (page, offset))
        batch = _gql(q, url=url).get("Dispute", [])
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
                "disputeTs": int(d.get("disputeTs") or 0),
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

    Default (DATA_SOURCE=hf): the offline RPC cache (V2/Legacy, 723, keccak-derived). When
    DATA_SOURCE=graphql, source from the local Envio indexer instead (V2+NegRisk+Legacy, joinable
    subset), falling back to the RPC cache if the indexer is unreachable.
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
            pass  # indexer down → offline RPC cache below
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
    data = build_dispute_cache()
    print(f"\nHF-joinable V2/Legacy disputes: {data['n_joined']} "
          f"(of {data['n_derivable_raw']} derivable scanned)")
    print(f"NegRisk disputes (counted, NOT joinable without the local indexer): "
          f"{data['negrisk_count_unjoinable']}")
    print("by category:", dispute_counts_by_category())
