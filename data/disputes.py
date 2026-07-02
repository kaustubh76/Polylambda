"""
disputes — OOv2 dispute labels WITHOUT Docker (the piece HF lacks; DECISIONS.md #13).

HF indexes resolution outcomes but no OptimisticOracleV2 dispute events, so λ + the replay-ablation
need dispute labels from elsewhere. This pulls `DisputePrice` logs straight from Polygon via a keyless
public RPC (`eth_getLogs`, no Docker, no indexer), decodes the ancillaryData, derives the CTF
`conditionId`, and joins to the HF dataset.

VERIFIED derivation (validated 29/29 against HF `condition.id` + `market_data`):
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


def load_disputes() -> list[dict]:
    """[{conditionId, disputeTs, adapter, disputer}] for HF-joinable V2/Legacy disputes (cached)."""
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
    rows = query(f"""
        SELECT {category_case_sql()} AS category, count(DISTINCT condition) AS n
        FROM '{table_path('market_data')}'
        WHERE condition IN ({inl})
        GROUP BY 1
    """)
    return {cat: n for cat, n in rows}


if __name__ == "__main__":
    data = build_dispute_cache()
    print(f"\nHF-joinable V2/Legacy disputes: {data['n_joined']} "
          f"(of {data['n_derivable_raw']} derivable scanned)")
    print(f"NegRisk disputes (counted, NOT joinable without the local indexer): "
          f"{data['negrisk_count_unjoinable']}")
    print("by category:", dispute_counts_by_category())
