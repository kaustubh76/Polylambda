"""
negrisk_map — recover the UMA↔tradeable conditionId map for NegRisk markets (the piece that
unblocks the powered 2024+ replay and that no public dataset ships).

BACKGROUND (this corrects the Day 07 "NegRisk 0% joinable / data-layer-blocked" finding):
Polymarket's multi-outcome (NegRisk) markets resolve through the UMA OOv2 at oracle
`0x2f5e…` under a UMA `questionId`, but they TRADE under a *different* conditionId whose oracle is
the NegRiskAdapter `0xd91E80cF…`. Our scoped indexer never sees that tradeable conditionId — its
`QuestionInitialized` fallback keccak-derives a PHANTOM conditionId from the 0x2f5e adapter that
exists nowhere on-chain (hence the phantom's 0% HF join). The real tradeable conditionId IS fully
present in the HF dataset; it just has to be recovered from chain.

THE LINK (validated end-to-end on 6 independent disputes — crypto/F1/politics/weather/NFL — each
joining HF `market_data` AND agreeing with the on-chain `ConditionPreparation`):
  NegRiskOperator `0x71523d0f…` emits QuestionPrepared (topic0 `0xcdc45423…`) once per NegRisk
  question, carrying:
    topic3 = requestId  == the UMA questionId our dispute rows already hold
    topic2 = questionId_d91e (the NegRiskAdapter-side question id)
  tradeableConditionId = keccak256( bytes20(0xd91E80cF…) ++ questionId_d91e ++ uint256(2) )
  which is exactly Gnosis CTF `getConditionId(d91eAdapter, questionId_d91e, 2)` — the SAME shape as
  `disputes.derive_condition_id`, but keyed on the recovered d91e question id, not the OO ancillary.

So: one forward scan of the Operator's QuestionPrepared logs builds { umaQuestionId ->
tradeableConditionId } for every NegRisk question, and NegRisk disputes join HF through it.
"""
from __future__ import annotations

import json
import os
import time

from eth_utils import keccak

from .disputes import _rpc
from .hf import query, table_path

# NegRiskOperator — emits QuestionPrepared linking the UMA requestId to the d91e question id.
OPERATOR = "0x71523d0f655B41E805Cec45b17163f528B59B820"
# keccak("QuestionPrepared(...)") as observed on-chain (topic0). Kept as the literal we validated;
# the event's full ABI is not needed — the three indexed topics carry everything.
QPREP_TOPIC0 = "0xcdc45423ec79c60a3fe3de57272e598d71a4ec88822e822ac8e134184a8435aa"
# NegRiskAdapter — the oracle under which NegRisk conditions are actually prepared/traded.
NEGRISK_ADAPTER = bytes.fromhex("d91E80cF2E7be2e162c6513ceD06f1dD0dA35296".lower())

# Operator is live from ~late-2023; scan a margin below the first NegRisk dispute through the HF cutoff.
MAP_START_BLOCK = int(os.environ.get("NEGRISK_MAP_START_BLOCK", "45000000"))
MAP_END_BLOCK = int(os.environ.get("HF_CUTOFF_BLOCK", "85948287"))
# QuestionPrepared is sparse (one per question), so a wide chunk still returns a small response —
# unlike OrderFilled, it does not make tenderly hang. Shrink-on-failure handles transient wedges.
MAP_CHUNK = int(os.environ.get("NEGRISK_MAP_CHUNK", "400000"))
CACHE = os.path.join(os.environ.get("DATA_CACHE_DIR", ".data_cache"), "negrisk_map.json")

# One validated (umaQuestionId -> tradeableConditionId) pair, used as a build canary + unit-test anchor.
CANARY_UMA_QID = "0x7ccc42e2a48278d6e3c1f0532644891004ad9b40e782a6b6c906cdfe80ebc475"
CANARY_TRADEABLE_CID = "0xca92ec28e43948c3b41a87ea94c74aea851924e085ff624df9fb03d83e668109"


def derive_negrisk_cid(question_id_d91e: str) -> str:
    """tradeableConditionId = keccak(d91eAdapter ++ questionId_d91e ++ uint256(2)).

    Gnosis CTF getConditionId with the NegRiskAdapter as oracle. `question_id_d91e` is the 0x-hex
    32-byte value carried in topic2 of the Operator's QuestionPrepared event.
    """
    qid = bytes.fromhex(question_id_d91e[2:] if question_id_d91e.startswith("0x") else question_id_d91e)
    return "0x" + keccak(NEGRISK_ADAPTER + qid + (2).to_bytes(32, "big")).hex()


def _get_logs_resilient(frm: int, to: int, *, timeout: int = 60, log=None) -> list:
    """eth_getLogs for QuestionPrepared on the Operator over [frm,to], halving the range on a
    transient RPC error (tenderly intermittently -32603s). Returns the decoded log list."""
    try:
        return _rpc("eth_getLogs", [{"address": OPERATOR, "topics": [QPREP_TOPIC0],
                                     "fromBlock": hex(frm), "toBlock": hex(to)}], timeout=timeout)
    except Exception as e:
        if to - frm <= 25_000:
            raise
        mid = (frm + to) // 2
        if log:
            log(f"    split [{frm}..{to}] after {str(e)[:60]}")
        time.sleep(1)
        return _get_logs_resilient(frm, mid, timeout=timeout, log=log) + \
            _get_logs_resilient(mid + 1, to, timeout=timeout, log=log)


def build_negrisk_map(start_block: int = MAP_START_BLOCK, end_block: int = MAP_END_BLOCK,
                      *, refetch: bool = False, log=print) -> dict:
    """Scan Operator QuestionPrepared logs → { umaQuestionId(topic3) : {questionId_d91e(topic2),
    tradeableConditionId, prepBlock} }. Cached to negrisk_map.json.

    NB duplicate topic3 (a question re-prepared after a two-strikes reset) keeps the FIRST occurrence
    — the tradeable conditionId is stable across resets (same d91e question id).
    """
    if os.path.exists(CACHE) and not refetch:
        with open(CACHE) as f:
            return json.load(f)

    mapping: dict[str, dict] = {}
    frm = start_block
    while frm <= end_block:
        to = min(frm + MAP_CHUNK, end_block)
        for lg in _get_logs_resilient(frm, to, log=log):
            uma_qid = lg["topics"][3]
            if uma_qid in mapping:
                continue
            qid_d91e = lg["topics"][2]
            mapping[uma_qid] = {
                "questionId_d91e": qid_d91e,
                "tradeableConditionId": derive_negrisk_cid(qid_d91e),
                "prepBlock": int(lg["blockNumber"], 16),
            }
        if log:
            log(f"  [{frm}..{to}] questions mapped={len(mapping)}")
        frm = to + 1

    # canary: the module's own validated pair must be present + derive correctly
    c = mapping.get(CANARY_UMA_QID)
    if not c or c["tradeableConditionId"] != CANARY_TRADEABLE_CID:
        raise RuntimeError(f"negrisk_map canary failed: {CANARY_UMA_QID} -> {c}")

    out = {"map": mapping, "n": len(mapping), "start_block": start_block, "end_block": end_block}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(out, f)
    return out


# ---------------------------------------------------------------------------------------------
# LIVE labeling — resolve a NegRisk OO dispute to its tradeable conditionId from pure RPC, with no
# 36MB negrisk_map.json (which is NOT shipped to the deploy image). Powers the live dispute stream.
# ---------------------------------------------------------------------------------------------
LIVE_LABELS_CACHE = os.path.join(os.environ.get("DATA_CACHE_DIR", ".data_cache"),
                                 "negrisk_live_labels.json")


def uma_question_id(ancillary: bytes) -> str:
    """umaQuestionId = keccak256(the OO request's ancillaryData).

    VERIFIED on-chain for NegRisk (not just V2): the adapter's QuestionInitialized topic1 (questionID)
    equals keccak(its own ancillaryData) — so a DisputePrice log alone yields the questionId for free.
    NB this is the QUESTION id; the NegRisk *conditionId* is still NOT derivable from it (sequential
    NegRiskIdLib ids) — that bridge is the Operator lookup in resolve_negrisk_cids().
    """
    return "0x" + keccak(ancillary).hex()


def resolve_negrisk_cids(uma_question_ids, *, log=None) -> dict[str, str]:
    """{umaQuestionId: tradeableConditionId} via ONE batched NegRiskOperator QuestionPrepared lookup.

    This is what makes NegRisk disputes label-joinable on the pure-RPC path (they used to be "counted
    but unlabeled" — ~96% of the live feed showing no market). The chain, verified end-to-end:

        umaQuestionId = keccak(ancillary)                     (free, from the DisputePrice log)
        -> QuestionPrepared(topic3 = umaQuestionId)           (indexed → one batched getLogs)
        -> questionId_d91e (topic2) -> derive_negrisk_cid()   -> tradeable conditionId

    Validated: 963/963 released NegRisk disputes reproduce their exact release conditionId, and on live
    disputes 14/14 resolved ids are present in the HF `condition` table. topic3 is indexed, so a single
    wide-range query over the Operator's whole life answers the whole batch (~1s), and results are
    immutable → cached. Needs NO negrisk_map.json (36MB), so it works on the slim deploy image.
    """
    wanted = sorted({q for q in uma_question_ids if q})
    if not wanted:
        return {}
    cached: dict = {}
    if os.path.exists(LIVE_LABELS_CACHE):
        try:
            with open(LIVE_LABELS_CACHE) as f:
                cached = json.load(f)
        except Exception:  # noqa: BLE001 — a corrupt cache must not break the live feed
            cached = {}
    todo = [q for q in wanted if q not in cached]
    if todo:
        try:
            from .disputes import chain_head_block
            head = chain_head_block()
            logs = _batched_qprep(todo, MAP_START_BLOCK, head, log=log)
            found = {lg["topics"][3]: derive_negrisk_cid(lg["topics"][2]) for lg in logs}
            for q in todo:
                cached[q] = found.get(q)       # None → remembered as unresolvable, not retried forever
            if log:
                log(f"  negrisk labels: resolved {len(found)}/{len(todo)}")
            try:
                os.makedirs(os.path.dirname(LIVE_LABELS_CACHE), exist_ok=True)
                with open(LIVE_LABELS_CACHE, "w") as f:
                    json.dump(cached, f)
            except Exception:  # noqa: BLE001 — caching is an optimization, not a requirement
                pass
        except Exception as e:  # noqa: BLE001 — labeling is enrichment; never break the feed
            if log:
                log(f"  negrisk label batch failed: {str(e)[:80]}")
    return {q: cached[q] for q in wanted if cached.get(q)}


def _batched_qprep(qids: list[str], frm: int, to: int, *, timeout: int = 60, log=None) -> list:
    """QuestionPrepared logs for a batch of umaQuestionIds (topic3 OR-filter), halving the block range
    on a transient/oversized-range RPC error. Mirrors _get_logs_resilient."""
    try:
        return _rpc("eth_getLogs", [{"address": OPERATOR, "topics": [QPREP_TOPIC0, None, None, qids],
                                     "fromBlock": hex(frm), "toBlock": hex(to)}], timeout=timeout)
    except Exception:
        if to - frm <= 500_000:
            raise
        mid = (frm + to) // 2
        if log:
            log(f"    split qprep [{frm}..{to}]")
        return _batched_qprep(qids, frm, mid, timeout=timeout, log=log) + \
            _batched_qprep(qids, mid + 1, to, timeout=timeout, log=log)


def load_negrisk_map() -> dict[str, dict]:
    """Cached { umaQuestionId : {questionId_d91e, tradeableConditionId, prepBlock} }.

    Cache-only: returns {} if the map has not been built (does NOT trigger a network scan — run
    `python -m data.negrisk_map` or call build_negrisk_map() to build it).
    """
    if not os.path.exists(CACHE):
        return {}
    with open(CACHE) as f:
        return json.load(f)["map"]


def hf_has_conditions(cids: list[str]) -> set[str]:
    """Subset of `cids` present in HF `condition` (the join-key membership test)."""
    joined: set[str] = set()
    cpath = table_path("condition")
    uniq = list({c for c in cids if c})
    for i in range(0, len(uniq), 5000):
        inl = ",".join(f"'{c}'" for c in uniq[i:i + 5000])
        joined |= {r[0] for r in query(f"SELECT id FROM '{cpath}' WHERE id IN ({inl})")}
    return joined


if __name__ == "__main__":
    m = build_negrisk_map()
    cids = [v["tradeableConditionId"] for v in m["map"].values()]
    present = hf_has_conditions(cids)
    n_join = sum(1 for c in cids if c in present)
    print(f"NegRisk questions mapped: {m['n']}")
    print(f"tradeable conditionIds in HF: {n_join}/{len(cids)} ({n_join/max(1,len(cids)):.1%})")
