# The Polymarket On-Chain v1 dataset — dossier & how PolyLambda uses it

**Full analysis of `moose-code/polymarket-onchain-v1`** (HuggingFace) — the largest public Polymarket
dataset: **2.74B on-chain records, 1.17B CLOB trades, 127 GB** Zstd parquet, Hive-partitioned by
year, indexed with the open-source **Envio** `enviodev/polymarket-indexer` (CC-BY-4.0). Queryable in
place with DuckDB — **no download**.

> Every number below was produced by a live DuckDB query and is reproducible with
> `python -m data.dossier` (add `--full` for the full-scan queries). Verified 2026-07-01/02 against
> the live dataset. Where a number here disagrees with the announcement, this file is what the data
> actually says.

Why this matters for PolyLambda: every function that needs *history* (σ priors, reconciliation
ground-truth, λ base-rates, and the primary **replay-ablation edge proof**) was a stub pointing at a
local Envio indexer that would take days to backfill 1.17B fills. This dataset is that history,
already indexed — so the `data/` layer queries it in place and the indexer is scoped down to the one
thing HF lacks (below).

---

## 1. Scale (verified counts)

| Table | Rows (verified) | Layout | What it is / PolyLambda use |
|---|---:|---|---|
| `order_filled` | **1,172,658,611** | partitioned `year=2022..2026` | CLOB fills (CTF Exchange). **The σ / fair-value tape.** |
| `orders_matched` | **445,179,390** | partitioned | CLOB match events |
| `redemption` | **153,914,418** | partitioned | resolution payouts claimed |
| `condition` | **1,117,152** | single file | market resolutions → **recon ground truth** |
| `market_data` | **1,854,758** (927,377 conditions) | single file | question/slug + tokenId↔conditionId → **join + category** |
| `user_position` | 303,955,230* | single file | per-user avgPrice / realizedPnl |

\* `user_position`/`fee_refunded` per the dataset card; not re-counted here.

**CLOB tape spans 2022–2026, not 2020.** `order_filled` min/max `year` = **2022–2026** — the CTF
Exchange launched late 2022. The "since 2020" back-history lives in the FPMM-era tables
(`fpmm_transaction` etc.), *not* in the fill tape PolyLambda's σ consumes. (Correction vs the
headline.)

---

## 2. Verified schema facts (these drive every query in `data/`)

Confirmed by `DESCRIBE` + sampling, not assumed — see `data/hf.py` `COLUMNS`:

- **All scalar columns are `VARCHAR`** — uint256 amounts, `timestamp`, and asset ids are strings.
  Cast explicitly: amounts/price → `DOUBLE` (matches the TS `Number()`), `timestamp` → `BIGINT`
  (**epoch seconds**; e.g. `1674781150` = 2023-01-27).
- **camelCase** names (`makerAssetId`, `payoutNumerators`, …), not snake_case.
- **Layouts differ:** event tables are `year`-partitioned (`<table>/**/*.parquet`); state tables are
  a single file (`<table>.parquet`) — a `/**/` glob 404s on them. `data/hf.py:table_path` encodes this.
- **`order_filled` = raw OrderFilled:** `maker, taker, makerAssetId, takerAssetId, makerAmountFilled,
  takerAmountFilled, fee, timestamp`. Cash leg is **`assetId = '0'`**. deriveFill (BUY/SELL, price,
  size) is a straight port of `indexer/src/lib.ts` — **parity-tested** in `tests/test_data_fills.py`.
  Spot check (real row): `1263600 / 2430000 = 0.52` ✓ a valid probability.
- **The fill↔market join works and is exact:** `order_filled.{maker,taker}AssetId = market_data.id`
  (the outcome tokenId). Validated **30/30** on sampled fills. This removes the need to index
  `TokenRegistered` locally.
- **`market_data.outcomeIndex` is entirely NULL** → cannot label YES vs NO from it. `data/fills.py`
  therefore normalizes to a *deterministic* canonical leg (`min(tokenId)`, other leg → `1-price`);
  σ of log-odds is invariant to that choice, so this is correct and uses both legs' liquidity.
- **Payout vectors are heterogeneously scaled:** `payoutDenominator` is `'1'` for 977,663 conditions
  ([1,0]/[0,1]) but `'1000000000000000000'` (1e18) for others (numerators like `[0, 1e18]`), plus
  `'2'` (13,110, the 50/50 split) and `'1000000'` (1,524). Recon is unaffected — both HF and the
  local indexer store the **raw** `payoutNumerators` from the same on-chain event, so they match
  regardless of scaling — but any YES/NO *classification* must use argmax, not string match.

---

## 3. Resolutions (recon eligible set)

`condition`: **1,117,152 total**, **992,485 resolved** (88.84%), **124,667 unresolved**
(`payoutDenominator = '0'` ⇔ unresolved, an exact match to the count).

By argmax of the payout vector (scale-independent): **YES 398,356 · NO 580,992 · 50/50 split 13,137**
· non-binary 0. More markets resolve **NO** (58.5% of resolved) than YES (40.1%) — the base rate a
naive fair-value must not ignore.

This sizes the reconciliation eligible set: `recon.run_recon` compares the local indexer's
`Market.finalOutcome` to `condition.payoutNumerators` (bulk-loaded via
`data.conditions.hf_payout_map`) for the ~992k resolved, RPC only for the recent reorg tail.

---

## 4. Category base rates (the λ denominator + σ-prior strata)

Per **derived** category (`market_data ⋈ condition`, 927,377 conditions with metadata; 859,709
resolved):

| Category | Markets | Resolved |
|---|---:|---:|
| other | 625,720 | 563,325 |
| crypto | 170,677 | 170,446 |
| sports | 88,914 | 87,854 |
| politics | 19,061 | 15,953 |
| tech-ai | 10,624 | 10,298 |
| geopolitics | 8,313 | 8,026 |
| entertainment | 2,840 | 2,793 |
| economics | 1,228 | 1,014 |

Two honest caveats surfaced by the data:
- **`category` is derived, not a dataset column** (best-effort slug/name keywords in
  `data/metadata.py`). **67% land in `other`** — a coarse categorizer; a later pass can swap in
  Gamma-API tags by slug. σ-prior and λ base-rate both inherit this.
- **~132k resolved conditions have no `market_data` row** (992,485 vs 859,709) — FPMM-era / unlabeled
  markets. They count for recon but not for category strata.

These are the **denominators**. The dispute **numerator** for λ is *not in HF* (§5), so
`category_base_rate` reports a base rate with a wide **Wilson CI** until the local indexer supplies
disputes.

---

## 4b. Wash signal — a σ caveat surfaced by the data

Self-crosses (`maker = taker`) in a 200k `order_filled` sample: **0**. Literal same-wallet
self-crossing is essentially absent because the CLOB matches *distinct* maker/taker orders — real
wash/manipulation on Polymarket runs through *different* wallets one entity controls, which a
`maker == taker` test cannot see. So `sigma.wash_filter`'s self-cross drop is a near-no-op on this
dataset: **σ robustness on thin/wash markets rests on the winsorized/robust EWMA + category×price
shrinkage, not the self-cross filter** (a refinement of DECISIONS.md #9; genuine wash detection needs
entity/wallet clustering, which is out of scope). Note: this SAMPLE query cost ~24 min remotely —
compute wash metrics on a **materialized slice**, not against `hf://`.

## 5. The gap probe — what HF does NOT have (the load-bearing negative result)

HF indexes `UmaSportsOracle` + `ConditionalTokens` resolution, but **not generic
OptimisticOracleV2 `ProposePrice` / `DisputePrice` / `Settle`**. So the dataset gives you resolution
*outcomes* but **zero dispute events** — and the ~184 dispute **labels** that λ and the
replay-ablation depend on are simply not here. `data.dossier.gap_probe()` returns
`dispute_events_in_hf = 0` against 992,485 resolved markets you'd want labels for.

**Consequence:** the dispute labels must come from OUTSIDE the HF dataset. Two ways: the scoped local
Envio indexer (`indexer/`, needs Docker), or — implemented here — `data/disputes.py`, which pulls
OOv2 `DisputePrice` logs straight from Polygon via a keyless public RPC (`eth_getLogs`, no Docker),
derives `conditionId = keccak256(adapter ++ keccak256(ancillaryData) ++ 2)`, and joins to HF. The
derivation was validated **723/723** against HF for the **UMA CTF Adapter V2 + Legacy**.

**One hard limitation, measured not assumed:** the keccak derivation does NOT work for the **NegRisk**
adapter (0/56 across every variant) — NegRisk assigns sequential questionIds via NegRiskIdLib, so
conditionId is not a function of the OO ancillaryData. NegRisk disputes are **counted (963) but not
label-joined**; recovering them needs the NegRiskAdapter's own on-chain events (the local indexer).
Polymarket moved most recent markets to NegRisk, so `data/disputes.py` skews to the 2022–2024 V2 era —
which is where HF `market_data` overlaps anyway. This is exactly the `DECISIONS.md #3/#13` adapter
caveat, now quantified.

## 5b. Dispute base rates — the λ signal, from real data (723 disputes)

`data/disputes.py` recovered **723 HF-joined V2/Legacy disputes**. Joined to derived categories, the
per-category dispute base rate (disputes / resolved markets) is:

| Category | Disputes | Resolved | Rate |
|---|---:|---:|---:|
| **politics** | 146 | 15,953 | **0.92%** |
| **geopolitics** | 46 | 8,026 | **0.57%** |
| economics | 4 | 1,014 | 0.39% |
| tech-ai | 23 | 10,298 | 0.22% |
| entertainment | 3 | 2,793 | 0.11% |
| sports | 91 | 87,854 | 0.10% |
| **crypto** | 72 | 170,446 | **0.042%** |
| other | 162 | 563,325 | 0.029% |

**This is the market-selection edge the thesis needs, in real numbers: politics markets are ~22× more
dispute-prone than crypto** (0.92% vs 0.042%), and politics + geopolitics dominate disputes despite
being a small share of markets. This is precisely what `λ_select` is supposed to capture — avoid or
size down the dispute-prone categories. (Caveat: numerators are V2/Legacy-only while denominators
include NegRisk markets, so these are **lower bounds**; the cross-category *ordering* is the signal.)

---

## 6. What the dataset unblocks (table → consumer)

| PolyLambda consumer | HF table(s) | `data/` entry point |
|---|---|---|
| `sigma.fetch_fills` (σ tape) | `order_filled ⋈ market_data` | `data.fills.fetch_fills_hf` |
| `sigma.category_price_prior` corpus | sampled markets → σ | `data.prior_corpus.build_sigma_observation_corpus` |
| `recon.run_recon` ground truth | `condition.payoutNumerators` | `data.conditions.hf_payout_map` |
| `lambda_engine.category_base_rate` denominator | `market_data ⋈ condition` | `data.base_rates.category_counts_hf` |
| `replay_ablation.run_replay` controls + tapes | `market_data` + `order_filled` + `condition` | `data.cache.materialize_slice` |
| dispute **labels** (numerator) | — *(not in HF)* | **local OOv2 indexer** |

---

## 7. DuckDB recipes

```bash
# scale (metadata scan, ~20s)
duckdb -c "INSTALL httpfs;LOAD httpfs;
  SELECT count(*) FROM 'hf://datasets/moose-code/polymarket-onchain-v1/order_filled/**/*.parquet';"
# → 1172658611

# resolved-condition ground truth (single file, ~5s)
duckdb -c "INSTALL httpfs;LOAD httpfs;
  SELECT count(*) FILTER (WHERE len(payoutNumerators)>0)
  FROM 'hf://datasets/moose-code/polymarket-onchain-v1/condition.parquet';"
# → 992485
```

deriveFill in SQL (the exact port; `price = collateral/outcome`, `size = outcome/1e6`, cash leg `='0'`)
lives in `data/fills.py:DERIVE_FILL_SQL` and is parity-tested against `indexer/test/lib.test.ts`.

**Performance:** single-pass *aggregates* over `order_filled` are ~20–40s remotely; a *single
market's* tape is a multi-hundred-million-row scan (minutes) because there is no token index, only a
`year` partition. For the σ-prior corpus and the replay, **materialize the slice once**
(`data.cache.materialize_slice`) — after which `data.hf.table_path` transparently reads local parquet
in milliseconds.

---

## 8. Reproduce

```bash
pip install -r requirements.txt          # adds duckdb, huggingface_hub, pyarrow
python -m data.dossier                    # §3–5 (cheap: single-file + metadata)
python -m data.dossier --full             # + §1 counts and wash prevalence (full scans)
pytest tests/test_data_fills.py           # deriveFill SQL↔TS parity (offline)
```
