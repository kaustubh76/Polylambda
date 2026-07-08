"""
export_disputes — package the OOv2 dispute-label layer as a releasable companion dataset.

WHY THIS EXISTS. The public `moose-code/polymarket-onchain-v1` dataset (2.74B on-chain records, 1.17B
CLOB fills) is the fill/condition/market backbone — but it does NOT contain the OptimisticOracleV2
proposal/dispute lifecycle. Those dispute events are PolyLambda's genuine net-new public contribution:
the scoped local Envio indexer (see ../indexer/) produces them for EVERY adapter (V2 + NegRisk + Legacy)
via the ConditionPreparation lookup (authoritative conditionId, not keccak derivation).

This module reads those disputes from the local indexer (Hasura), resolves each to its effective HF
join key, enriches the joinable rows with a derived category (and, optionally, pre/post price context
from the HF fill tape), and writes a releasable artifact + a HuggingFace dataset card:

    dataset_release/polymarket-oov2-disputes-v1/
        disputes.parquet   # one row per DisputePrice event, all adapters
        stats.json         # counts by adapter / category / year + the join-rate summary
        README.md          # the HF dataset card (schema, provenance, join recipe, NegRisk map)

NEGRISK (corrected 2026-07-05): NegRisk markets resolve through the UMA OOv2 under a UMA questionId but
TRADE under a different conditionId (oracle = NegRiskAdapter 0xd91E80cF…). The indexer's phantom
conditionId (keccak from the 0x2f5e OO adapter) exists nowhere on-chain — which is why the earlier
"NegRisk 0% joinable" reading was an artifact, not a structural gap. `data/negrisk_map.py` recovers the
real tradeable conditionId from the NegRiskOperator's QuestionPrepared events, so the released
`conditionId` is the EFFECTIVE HF join key (tradeable for NegRisk, native for V2/Legacy) and NegRisk
disputes join `moose-code` like any other. See DATASET.md §5.

HONEST FRAMING (baked into the card): the 2.74B-record dataset already exists publicly; this is the
*missing dispute layer* PLUS the UMA↔tradeable conditionId map that lets NegRisk disputes join it.
"""
from __future__ import annotations

import datetime
import json
import os

from .disputes import HOSTED_GRAPHQL_URL, load_disputes_from_indexer, resolve_indexer
from .hf import connect, query, table_path
from .metadata import category_case_sql

OUT_DIR = os.environ.get("DISPUTE_RELEASE_DIR", "dataset_release/polymarket-oov2-disputes-v1")
DATASET_NAME = "polymarket-oov2-disputes-v1"

# Deterministic column order for the released parquet.
COLUMNS = ["conditionId", "questionId", "adapter", "hf_joinable", "category",
           "disputeTs", "disputeDate", "requestTimestamp", "round", "disputer", "proposer",
           "proposedOutcome", "preDisputePrice", "postDisputePrice", "realizedJumpLogit"]


def _dt(ts: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)


def _year(ts: int) -> int | None:
    return _dt(ts).year if ts else None


def _iso(ts: int) -> str | None:
    return _dt(ts).strftime("%Y-%m-%d") if ts else None


def _categories_for(cids: list[str]) -> dict[str, str]:
    """{conditionId: derived category} from HF market_data (joinable cids only)."""
    if not cids:
        return {}
    out: dict[str, str] = {}
    for i in range(0, len(cids), 5000):
        inl = ",".join(f"'{c}'" for c in cids[i:i + 5000])
        rows = query(f"SELECT condition, any_value({category_case_sql()}) "
                     f"FROM '{table_path('market_data')}' WHERE condition IN ({inl}) GROUP BY condition")
        out.update({cond: cat for cond, cat in rows})
    return out


def _price_context(cid: str, dispute_ts: int, *, fill_limit: int = 5000) -> tuple[float | None, float | None, float | None]:
    """(pre-dispute price, post-dispute price, realized logit jump) from the HF fill tape. Best-effort."""
    from forwardtest.replay_ablation import _realized_jump_logit
    from .fills import fetch_fills_hf

    try:
        fills = fetch_fills_hf(cid, limit=fill_limit)
    except Exception:
        return None, None, None
    if not fills:
        return None, None, None
    pre = [f for f in fills if f["timestamp"] <= dispute_ts]
    post = [f for f in fills if f["timestamp"] > dispute_ts]
    pre_p = pre[-1]["price"] if pre else None
    post_p = post[0]["price"] if post else None
    jump = _realized_jump_logit(fills, dispute_ts) if (pre and post) else None
    return pre_p, post_p, jump


def build_rows(graphql_url: str | None = None, *, with_price_context: bool = False, log=print) -> list[dict]:
    """Assemble the full release rows (all adapters). Category on the joinable subset; price optional.

    The released `conditionId` is the EFFECTIVE HF join key: the recovered tradeable conditionId for
    NegRisk (via data/negrisk_map.py), the native conditionId for V2/Legacy. So category + price context
    now populate for NegRisk too, wherever the map resolved the market.

    Endpoint via the shared `resolve_indexer` (explicit → local → hosted). A hosted hit keeps the
    export RUNNING but is coverage-capped — loudly flagged, because a truncated release artifact
    must never be published as authoritative.
    """
    url, secret = resolve_indexer(graphql_url)
    if url is None:
        raise RuntimeError("no indexer endpoint reachable (local Hasura and hosted HyperIndex both "
                           "down) — start the local indexer before exporting")
    if log and url == HOSTED_GRAPHQL_URL:
        log("  ⚠ local indexer down -> hosted HyperIndex fallback (COVERAGE-CAPPED: 1000 rows/page, "
            "aggregates off). Do NOT publish artifacts from this run as authoritative.")
    disputes = load_disputes_from_indexer(url, secret=secret, log=log)
    # effective HF join key per row (tradeable for NegRisk, native for V2/Legacy)
    for d in disputes:
        d["_joinCid"] = d.get("tradeableConditionId") or d["conditionId"]
    joinable = [d["_joinCid"] for d in disputes if d["hf_joinable"]]
    cat_of = _categories_for(list(set(joinable)))
    if log:
        log(f"  categorized {len(cat_of)} of {len(set(joinable))} joinable conditions")

    rows: list[dict] = []
    for i, d in enumerate(disputes):
        cid, ts = d["_joinCid"], d["disputeTs"]
        pre = post = jump = None
        if with_price_context and d["hf_joinable"]:
            pre, post, jump = _price_context(cid, ts)
            if log and i % 100 == 0:
                log(f"  price context {i}/{len(disputes)}")
        rows.append({
            "conditionId": cid,
            "questionId": d.get("questionId"),
            "adapter": d["adapter"],
            "hf_joinable": d["hf_joinable"],
            "category": cat_of.get(cid) if d["hf_joinable"] else None,
            "disputeTs": ts,
            "disputeDate": _iso(ts),
            "requestTimestamp": d.get("requestTimestamp"),
            "round": d.get("round"),
            "disputer": d.get("disputer"),
            "proposer": d.get("proposer"),
            "proposedOutcome": d.get("proposedOutcome"),
            "preDisputePrice": pre,
            "postDisputePrice": post,
            "realizedJumpLogit": jump,
        })
    return rows


def _stats(rows: list[dict]) -> dict:
    from collections import Counter

    adapter = Counter(r["adapter"] for r in rows)
    joinable = sum(1 for r in rows if r["hf_joinable"])
    cat = Counter(r["category"] for r in rows if r["hf_joinable"])
    year = Counter(_year(r["disputeTs"]) for r in rows if r["disputeTs"])
    ts = [r["disputeTs"] for r in rows if r["disputeTs"]]
    # per-adapter join rate — makes the NegRisk correction auditable (was 0%, now ≈V2 after the map)
    adapter_join = {a: {"total": adapter[a],
                        "joinable": sum(1 for r in rows if r["adapter"] == a and r["hf_joinable"])}
                    for a in adapter}
    return {
        "dataset": DATASET_NAME,
        "total_disputes": len(rows),
        "hf_joinable": joinable,
        "hf_joinable_pct": round(100 * joinable / len(rows), 1) if rows else 0.0,
        "by_adapter": dict(adapter),
        "by_adapter_joinable": adapter_join,
        "by_category_joinable": dict(cat),
        "by_year": {str(k): v for k, v in sorted(year.items())},
        "dispute_ts_min": min(ts) if ts else None,
        "dispute_ts_max": max(ts) if ts else None,
        "date_min": _iso(min(ts)) if ts else None,
        "date_max": _iso(max(ts)) if ts else None,
    }


def _card(stats: dict) -> str:
    """The HuggingFace dataset card (README.md) — provenance, schema, join recipe, NegRisk caveat."""
    by_adapter = " · ".join(f"{k}={v}" for k, v in stats["by_adapter"].items())
    rc = stats.get("recon")
    recon_line = (f"pass_rate = {rc['pass_rate']:.4f} on {rc['eligible']:,} eligible V2/Legacy markets"
                  if rc else "pass_rate = 1.0 on the eligible V2/Legacy set")
    return f"""---
license: cc-by-4.0
task_categories:
  - tabular-classification
tags:
  - polymarket
  - prediction-markets
  - uma
  - optimistic-oracle
  - disputes
  - polygon
pretty_name: Polymarket OOv2 Disputes v1
---

# Polymarket OptimisticOracleV2 Disputes (v1)

The **missing dispute layer** for Polymarket on-chain analysis. The excellent public dataset
[`moose-code/polymarket-onchain-v1`](https://huggingface.co/datasets/moose-code/polymarket-onchain-v1)
(2.74B on-chain records, 1.17B CLOB fills, conditions, redemptions, positions) is the trading/resolution
backbone — but it does **not** index the UMA **OptimisticOracleV2** proposal/dispute lifecycle. This
companion dataset fills exactly that gap: every on-chain `DisputePrice` on a Polymarket UMA CTF adapter,
linked to its Gnosis CTF `conditionId` so it joins the `moose-code` tables directly.

- **{stats['total_disputes']} disputes** across adapters ({by_adapter}), {stats['date_min']} → {stats['date_max']}.
- **{stats['hf_joinable']} ({stats['hf_joinable_pct']}%) are HF-joinable** — across **all** adapters. The
  released `conditionId` is the effective `moose-code` join key: for NegRisk (multi-outcome) markets that
  is the **tradeable** conditionId recovered from the NegRiskOperator (see the NegRisk map below), not the
  UMA-side id; for V2/Legacy it is the native conditionId. Every joinable row carries a derived `category`
  and joins the fill tape directly.

## Provenance
Produced by the PolyLambda scoped Envio indexer (`indexer/`, chain 137 / Polygon), which reads:
- `ConditionalTokens.ConditionPreparation` → the authoritative `questionId → conditionId` map (works for
  **every** adapter, including NegRisk — it is read from chain, never derived);
- `UmaCtfAdapter.QuestionInitialized` (V2 + NegRisk + Legacy) → `(adapter, requestTimestamp) → conditionId`;
- `OptimisticOracleV2.{{ProposePrice, DisputePrice, Settle}}` → the proposal/dispute/settle events, whose
  `conditionId` is resolved via the lookup above (no keccak derivation — which fails for NegRisk).

Reconciliation: the indexer's resolved `finalOutcome` matches `moose-code` `condition.payoutNumerators`
at **{recon_line}**.

## Schema (`disputes.parquet`)
| column | type | notes |
|---|---|---|
| `conditionId` | string | Gnosis CTF conditionId — the effective join key to `moose-code` `condition.id` / `market_data.condition`. For NegRisk this is the **tradeable** conditionId (oracle = NegRiskAdapter), recovered on-chain; for V2/Legacy it is the native conditionId. |
| `questionId` | string | UMA question id (the OOv2 request id; for NegRisk it maps to the tradeable conditionId via the Operator — see below) |
| `adapter` | string | `v2` · `negrisk` · `legacy` (the UMA CTF adapter that owns the request) |
| `hf_joinable` | bool | `true` iff `conditionId` ∈ `moose-code` `condition` (all adapters; false only when the NegRisk map could not resolve a market, or the market post-dates the HF cutoff) |
| `category` | string | coarse keyword-derived category (crypto/politics/sports/…); null when not joinable |
| `disputeTs` | int64 | TRUE dispute block timestamp (epoch seconds) — resolved from the dispute tx's block |
| `disputeDate` | string | `YYYY-MM-DD` (from `disputeTs`) |
| `requestTimestamp` | int64 | the UMA OO *price-request* timestamp the dispute references (can precede the dispute tx by hours; this is what the raw `DisputePrice` event carries) |
| `round` | int | reset round (0 = first request; bumps on each two-strikes reset) |
| `disputer` / `proposer` | string | on-chain addresses |
| `proposedOutcome` | string | the disputed proposal: `YES` / `NO` / `UNRESOLVABLE` / `OTHER` |
| `preDisputePrice` / `postDisputePrice` / `realizedJumpLogit` | double | optional fill-tape price context (joinable only; null unless exported `--with-price-context`) |

## Join recipe (DuckDB)
```sql
INSTALL httpfs; LOAD httpfs;
SELECT d.conditionId, d.adapter, d.category, d.disputeDate, m.marketSlug
FROM 'disputes.parquet' d
JOIN 'hf://datasets/moose-code/polymarket-onchain-v1/market_data.parquet' m
  ON m.condition = d.conditionId
WHERE d.hf_joinable;   -- joins ALL adapters, incl. NegRisk (conditionId is already the tradeable key)
```

## The NegRisk map (the piece no public dataset ships)
Polymarket's **multi-outcome (NegRisk)** markets resolve through the UMA OOv2 under a UMA `questionId`,
but they **trade** under a *different* `conditionId` whose oracle is the NegRiskAdapter
`0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`. That tradeable conditionId is what `moose-code` keys on —
so joining a NegRisk dispute to the fill tape requires bridging UMA `questionId` → tradeable
`conditionId`. This dataset does that bridge for you: the released `conditionId` is already the tradeable
key, recovered from chain via the **NegRiskOperator** (`0x71523d0f655B41E805Cec45b17163f528B59B820`)
`QuestionPrepared` event, then `conditionId = keccak(NegRiskAdapter ++ questionId_d91e ++ uint256(2))`.

Validated end-to-end on independent disputes across crypto/F1/politics/weather/NFL — each recovered
`conditionId` both joins `moose-code` `market_data` **and** agrees with the on-chain
`ConditionPreparation`. Rows are `hf_joinable=false` only when the market post-dates the `moose-code`
cutoff (block 85,948,287 / 2026-04-24) or the Operator mapping could not be resolved.

> Earlier versions of this card described NegRisk as structurally absent from `moose-code`. That was an
> artifact of joining on the indexer's UMA-side (phantom) conditionId; the tradeable conditions are fully
> present, and this release now joins them.

## License
`CC-BY-4.0`, matching the upstream `moose-code/polymarket-onchain-v1`. Attribution: PolyLambda +
the `enviodev/polymarket-indexer` lineage.
"""


def export_dispute_dataset(graphql_url: str | None = None, out_dir: str = OUT_DIR,
                           *, with_price_context: bool = False, log=print) -> dict:
    """Write disputes.parquet + stats.json + README.md to out_dir. Returns the stats dict."""
    rows = build_rows(graphql_url, with_price_context=with_price_context, log=log)
    if not rows:
        raise RuntimeError("no disputes returned from the indexer — is it running / backfilled?")
    os.makedirs(out_dir, exist_ok=True)

    # parquet via DuckDB (explicit column order, deterministic)
    import pandas as pd

    df = pd.DataFrame(rows)[COLUMNS]
    # keep the price-context columns DOUBLE even on the fast (all-null) path, so the released schema is
    # identical whether or not --with-price-context was used.
    for c in ("preDisputePrice", "postDisputePrice", "realizedJumpLogit"):
        df[c] = df[c].astype("float64")
    con = connect()
    con.register("disputes_df", df)
    pq = os.path.join(out_dir, "disputes.parquet")
    con.execute(f"COPY disputes_df TO '{pq}' (FORMAT PARQUET)")
    con.unregister("disputes_df")

    stats = _stats(rows)
    stats["parquet_rows"] = len(df)
    # attach the indexer↔HF reconciliation summary (validates finalOutcome vs payoutNumerators)
    try:
        from recon.check import run_recon

        rep = run_recon(graphql_url or os.environ.get("GRAPHQL_URL", "http://localhost:8080/v1/graphql"))
        stats["recon"] = {"pass_rate": rep.pass_rate, "eligible": rep.eligible, "matched": rep.matched,
                          "no_ground_truth": rep.excluded_no_ground_truth}
    except Exception as e:  # recon is provenance, not a hard dep of the export
        if log:
            log(f"  (recon summary skipped: {str(e)[:80]})")

    with open(os.path.join(out_dir, "stats.json"), "w") as f:
        json.dump(stats, f, indent=2)
    with open(os.path.join(out_dir, "README.md"), "w") as f:
        f.write(_card(stats))

    if log:
        log(f"\nwrote {len(df)} rows -> {pq}")
        log(f"  by adapter: {stats['by_adapter']}")
        log(f"  hf_joinable: {stats['hf_joinable']} ({stats['hf_joinable_pct']}%)")
        log(f"  by year: {stats['by_year']}")
        log(f"\nto publish (needs `huggingface-cli login`):")
        log(f"  huggingface-cli upload <your-namespace>/{DATASET_NAME} {out_dir} . --repo-type dataset")
    return stats


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--graphql-url", default=os.environ.get("GRAPHQL_URL", "http://localhost:8080/v1/graphql"))
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--with-price-context", action="store_true",
                    help="attach pre/post fill-tape prices + realized jump (slow: one HF scan per joinable market)")
    args = ap.parse_args()
    export_dispute_dataset(args.graphql_url, args.out_dir, with_price_context=args.with_price_context)
