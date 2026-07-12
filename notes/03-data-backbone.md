# 03 · Data backbone

> **Source of truth.** `data/` modules; `dataset_release/polymarket-oov2-disputes-v1/stats.json` for the
> released counts; `../DATASET.md` / `../METHODOLOGY.md` for narrative. Mirrors
> `quant-implementation-full.excalidraw` Panels ①② (Panel L) + M glossary.

## 1. Why a two-source backbone

The HF dataset has everything **except dispute labels**. So the backbone joins two sources on
`conditionId`:

1. **HF dataset** `moose-code/polymarket-onchain-v1` — fills, outcomes, market metadata. Queried in
   place via DuckDB over `hf://` (no download). ~2.74B on-chain records · ~1.17B CLOB fills ·
   992,485 resolved conditions.
2. **OOv2 dispute labels** — derived without Docker by `data/disputes.py` from `OptimisticOracleV2`
   `DisputePrice` logs via a **keyless public RPC**, then joined to HF by deriving the `conditionId`.

## 2. The conditionId derivation (the join key)

`data/disputes.py:derive_condition_id(adapter, ancillary)` (:101):

```
conditionId = keccak256( adapter_address ‖ keccak256(ancillary) ‖ uint256(2) )
```

This is the Python twin of `indexer/src/lib.ts:deriveConditionId`. NegRisk markets **trade under a
different conditionId** (their oracle is the NegRiskAdapter), so they are joined via the recovered map.

## 3. NegRisk recovery (`data/negrisk_map.py`)

NegRisk was the "0% joinable" phantom-conditionId bug. Fix: scan `NegRiskOperator` `QuestionPrepared`
logs to build `{umaQuestionId → tradeableConditionId}` (`derive_negrisk_cid`, :58; `build_negrisk_map`,
:85). Result: 132,004 NegRisk questions mapped, 100% present in HF. `data/disputes.py` loads this map and
assigns `tradeableConditionId` so NegRisk disputes join like everything else.

## 4. The released dispute artifact (the numbers that matter)

`dataset_release/polymarket-oov2-disputes-v1/stats.json` is the committed source of truth:

| Field | Value |
|-------|-------|
| `total_disputes` | **1,794** |
| `hf_joinable` / `hf_joinable_pct` | 1,794 / **100.0%** |
| `by_adapter.v2` | 723 |
| `by_adapter.negrisk` | 963 |
| `by_adapter` (Legacy / `0x157ce2d6…`) | 108 |
| span | 2022-12 → 2026-04 |

> The 6 prior diagram fixes aligned `quant-implementation-full.excalidraw` to these exact counts.

## 5. Base rates → λ (`data/base_rates.py`)

- **Denominator** = markets resolved per category (`category_counts_hf()`, from HF).
- **Numerator** = disputes per category (`dispute_counts_by_category()` in `data/disputes.py`).
- `category_base_rate()` returns a **Wilson CI** (`_wilson(k, n, z=1.96)`), which becomes
  `LambdaOutput.ci_low/ci_high`.
- Real signal: politics is ~22× more dispute-prone than crypto. (The diagram's `0.92%` politics /
  `0.042%` crypto are the earlier V2/Legacy-only rates; the ~22× ratio holds against the corrected
  all-adapter numbers ~1.83% / ~0.085% — see `../METHODOLOGY.md`.)

## 6. Recon gate (`recon/check.py`)

Before estimators trust any indexed data, recon compares each **eligible** indexed `Market.finalOutcome`
to the HF payout vector (`data.conditions.hf_payout_map()`), bucketing exclusions explicitly.

- Committed result (`stats.json`): `pass_rate = 1.0`, `eligible = 28,482`, `matched = 28,482`,
  `no_ground_truth = 125,270`.
- **NegRisk phantom conditionIds** land in `excluded_no_ground_truth` — counted as data coverage, never a
  mismatch (that was the fix for the phantom bug).

## 7. Fills tape (`data/fills.py`)

`fetch_fills_hf(condition_id, *, limit=5000, years=None, canonical_token=None)` reads `order_filled` and
normalizes to a single canonical axis (`min(tokenId)`; the other side is `1 − price`), yielding
`{price, size, side, maker, taker, timestamp}`. It is a SQL port of `indexer/src/lib.ts:deriveFill` and is
**parity-tested** against that TS oracle (`tests/test_data_fills.py`). This tape feeds σ estimation and
the replay-ablation.

## 8. Label resolution order (how `load_disputes()` decides its source)

`data/disputes.py:load_disputes()` (default `DATA_SOURCE=hf`):
1. **Released parquet** `dataset_release/.../disputes.parquet` (default, all adapters, NegRisk joined).
2. If `DATA_SOURCE=graphql` → the Envio **indexer** (local or hosted, via `resolve_indexer`).
3. Fallback → live **keyless-RPC** derivation with an on-disk cache.

## 9. Reproduce

- Regenerate the reported dataset numbers: `python -m data.dossier --full`.
- Rebuild dispute labels from RPC: `python -m data.disputes`.
- Rebuild the NegRisk map: `python -m data.negrisk_map`.
- Re-export the released dataset: `python -m data.export_disputes`.
- Recon gate: `python -m recon.check`.
