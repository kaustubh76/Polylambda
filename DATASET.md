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

**Volume is overwhelmingly recent** (`fills_by_year`; the exact per-year counts sum to 1,172,658,611 —
the table shows rounded M-notation for readability, so the displayed values do not sum exactly):

| Year | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|
| Fills | 3,161 | 328,176 | ~57.6M | ~241.2M | **~873.5M** |

**74% of the entire tape is 2026 alone.** The liquid **2025–2026 markets are NegRisk** (multi-outcome),
so reaching the powered high-liquidity replay requires joining NegRisk disputes to the HF fill tape.
That join is now **solved** (see §5): NegRisk markets trade under a conditionId whose oracle is the
NegRiskAdapter, recoverable on-chain from the NegRiskOperator, and those tradeable conditions are
**100% present in HF**. The earlier claim here — that NegRisk was "structurally absent from HF" — was an
artifact of joining on the indexer's phantom conditionId, and is **corrected in §5**. With the map
(`data/negrisk_map.py`) the liquid-era replay runs on real HF fills (§5b).

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

These are the **denominators**. The dispute **numerator** now ships in-repo: the released parquet
(`dataset_release/polymarket-oov2-disputes-v1/disputes.parquet`, §5c — 1,848 disputes, all adapters,
100% HF-joinable; the 1,794 inside the HF window are the numerator) is the **default** for
`data.disputes.load_disputes` / `dispute_counts_by_category`
— no indexer or Docker needed. §5b is the authoritative current base-rate table. **Do not mix
numerators:** rates computed from the old 723-row V2/Legacy RPC-cache numerator (the last-resort
fallback) understate per-category rates roughly **2–20×** (politics 0.92%→1.83%, entertainment
0.11%→2.11%, crypto 0.042%→0.085%) — downstream λ consumers must use the release/indexer numerator,
never the fallback's.

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
*outcomes* but **zero dispute events** — and the **1,794 dispute labels (1,527 unique disputed
markets, §5c)** that λ and the replay-ablation depend on are simply not here. `data.dossier.gap_probe()` returns
`dispute_events_in_hf = 0` against 992,485 resolved markets you'd want labels for.

**Consequence:** the dispute labels must come from OUTSIDE the HF dataset. Three ways now, and the
**default is the released layer**: the git-tracked
`dataset_release/polymarket-oov2-disputes-v1/disputes.parquet` (§5c) that `data.disputes.load_disputes`
reads out of the box — no Docker, no RPC. Alternatives: the scoped local Envio indexer (`indexer/`,
needs Docker; `DATA_SOURCE=graphql` sources labels live from it), or the keyless-RPC keccak scan in
`data/disputes.py` — `eth_getLogs` for OOv2 `DisputePrice`, deriving
`conditionId = keccak256(adapter ++ keccak256(ancillaryData) ++ 2)` — which covers **V2/Legacy only**
(validated **723/723** against HF) and is the **last resort**.

**The derivation from OO ancillary does NOT work for NegRisk** — that part still holds: `keccak(adapter,
keccak(ancillary), 2)` joins NegRisk at 0%, because NegRisk assigns questionIds via NegRiskIdLib, not
from the OO ancillaryData. But that is a statement about the *derivation path*, **not** about HF
coverage — and §5a below corrects the coverage claim. `data/disputes.py`'s keccak path covers the
V2/Legacy era (validated 723/723); NegRisk is recovered a different way (the NegRiskOperator lookup, §5a).

### 5a. NegRisk IS in HF — the "0% joinable" finding was a phantom-conditionId artifact (CORRECTED 2026-07-05)

The 2026-07-03 verdict below was **wrong**, and finding out why is the load-bearing result of this
project. The claim was: NegRisk disputes join HF at 0% *even with the authoritative on-chain
conditionId*, so NegRisk is structurally absent from HF and the powered replay is data-layer-blocked.

What actually happens: NegRisk markets resolve through the UMA OOv2 under a UMA `questionId`, but they
**trade** under a *different* conditionId whose oracle is the **NegRiskAdapter** `0xd91E80cF…`. Our
indexer's `QuestionInitialized` handler, lacking a `ConditionPreparation` for that tradeable condition,
falls back to `deriveConditionId(0x2f5e…, questionId)` — which fabricates a **phantom conditionId that
exists nowhere on-chain**. Joining *that* to HF is what returned 0%. The real tradeable conditions are
**100% present in HF**.

The bridge is recoverable from chain and was validated end-to-end:
- The **NegRiskOperator** `0x71523d0f655B41E805Cec45b17163f528B59B820` emits `QuestionPrepared`
  (topic0 `0xcdc45423…`) with `topic3 = requestId = the UMA questionId` and `topic2 = questionId_d91e`.
- `tradeableConditionId = keccak256(0xd91E80cF… ++ questionId_d91e ++ uint256(2))`.
- `data/negrisk_map.py` scans the Operator once and builds this map: **132,004 NegRisk questions,
  100.0% of their tradeable conditionIds present in HF `condition`**. Validated on independent disputes
  across crypto/F1/politics/weather/NFL — each recovered conditionId both joins HF `market_data` **and**
  agrees with the on-chain `ConditionPreparation`.

**Dispute-level result (via the map):** every adapter now joins HF **100%** — V2 723/723, **NegRisk
943/943 (was 0/350)**, other 108/108. What made the earlier probe wrong was a **measurement trap**:
tenderly `eth_getLogs` silently returns EMPTY (not an error) for block ranges ≳1M, so scans that looked
like "0 events found" were really "range too wide"; every scan here uses ≤400k chunks + a positive control.

Reconciliation (`recon.check`, indexed `finalOutcome` == HF `payoutNumerators`) stays **pass_rate =
1.0000 on the eligible V2/Legacy set** (exact count in `stats.json`, grows with the backfill). NegRisk stays in the `no_ground_truth` bucket here — but
for a *different* reason than the old "absent from HF" story: the indexer stores NegRisk `finalOutcome`
under the **phantom** conditionId, while HF keys the real tradeable one, so there is no phantom-keyed
payout to compare. NegRisk still JOINS HF via the tradeable cid for the dataset + replay (above);
recon's finalOutcome check simply can't validate a phantom-keyed outcome (reconciling it would need the
indexer to key the tradeable conditionId — an indexer change, out of recon's scope).

### 5b′. Powered NegRisk-era replay (the Day 04 goal, now unblocked)

With the map, the ablation runs on **real HF fills** for the liquid NegRisk era (2024 slice materialized
locally; 26 disputed + 132 control markets processed). The ordering **holds in the liquid era**, matching
the earlier V2-era result:

| arm | λ*=0.0005 pnl (Sharpe) | at λ*=0.01 |
|---|---|---|
| **λ_jump** (surgical exit) | **+1888.7 (0.375)** | converges to diffusion |
| diffusion (always hold) | +1882.2 (0.373) | +1882.2 |
| λ_select (blanket avoid) | +0.0 (0.000) — forgoes 1895 reward to avoid 13 loss | +1073.6 |

λ_jump's reward-aware surgical exit beats always-hold (avoids 8.0 jump-loss for 1.45 forgone reward) and
crushes blanket-avoidance; arms converge at λ*=0.01 (|jump−diffusion| = 1.2), so the λ*-sensitivity is
real. Small N (surgical, not a headline Sharpe), but the **conclusion — surgical exit > avoidance —
holds on real liquid-era NegRisk data**, no longer just the thin V2 era.

### 5b″. Broader multi-year powered replay (all adapters, 2022–2026)

Widening §5b′ from the thin 2024 slice to the **full release universe** — every joinable disputed market
with a usable fill tape plus matched controls, off the local 15.2M-fill slice, with **true block-time**
dispute timestamps (`DATA_SOURCE=graphql`): **1,409 disputed + 2,856 control markets**. The ordering
**λ_jump > diffusion > λ_select holds at every point on the λ\* grid**, net of forgone rewards:

| arm | λ*=0.0005 pnl (Sharpe) | λ*=0.002 (frozen) | λ*=0.01 |
|---|---|---|---|
| **λ_jump** (surgical exit) | **+46,975.1 (0.3335)** | **+41,975.5 (0.2891)** | **+41,544.7 (0.2856)** |
| diffusion (always hold) | +40,064.9 (0.2738) | +40,064.9 (0.2738) | +40,064.9 (0.2738) |
| λ_select (blanket avoid) | +0.0 (0.000) | +23,911.5 (0.1947) | +29,458.5 (0.226) |

At n=1,409 this is a **powered** result, not the surgical §5b′ check. λ_jump's edge over always-hold is
largest where exits fire most (λ*=0.0005: **+6,910 pnl / +0.060 Sharpe**, avoiding 7,550 jump-loss for 640
forgone reward) and **narrows monotonically** as the threshold rises (frozen λ*=0.002: +1,911 / +0.015;
λ*=0.01: +1,480 / +0.012) — publish the **whole sensitivity curve, not the single tuned point** (the
frozen `lambda_star=0.002` is one mid-grid operating point, DECISIONS.md #11). λ_select forfeits so much
reward income (48,554 forgone at λ*=0.0005) that blanket avoidance never beats diffusion anywhere on the
grid. Conclusion at scale: **reward-aware surgical exit is the edge; blanket avoidance destroys it.**

### 5c. Released artifact — `polymarket-oov2-disputes-v1` (the missing dispute layer)

`data/export_disputes.py` packages the indexer's disputes as a **releasable companion dataset** — the
OOv2 dispute events `moose-code` lacks — written to `dataset_release/polymarket-oov2-disputes-v1/`
(`disputes.parquet` + `stats.json` + a HuggingFace `README.md` card). One row per `DisputePrice`, all
adapters, keyed by `conditionId` so it joins `moose-code` directly. Columns: `conditionId`,
`questionId`, `adapter` (v2/negrisk/legacy/raw-address), `hf_joinable`, `category`, `disputeTs`/`Date`
(**true dispute block time**, resolved per tx via `dispute_block_timestamps`), `requestTimestamp` (the
raw UMA OO price-request ts the event carries — can precede the dispute tx by hours), `round`,
`disputer`, `proposer`, `proposedOutcome`, + fill-tape price context (pre/post price + realized logit
jump; populated with `--with-price-context`).

- **1,848 disputes to chain head** (V2 725 · NegRisk 1013 · other 110), 2022-12-30 → **2026-07-16**
  (verified against the parquet; an earlier "2022-12-28 → 2026-04-09" here was wrong on both ends). Of
  these, **1,794 fall inside the HF window** (V2 723 · NegRisk 963 · other 108) → **1,527 unique disputed
  markets**, which are what the λ base rates are computed on; the **54** rows past the head are flagged
  `post_hf_cutoff` and carry no fill-tape price context. The **HF head** — the window the λ denominator is
  frozen at — is `HF_CUTOFF_BLOCK` 85,948,287 = **2026-04-24T07:43:38Z** (that block's on-chain timestamp;
  `data.disputes.HF_CUTOFF_TS`). **100% `hf_joinable`** across all adapters — the released `conditionId` is the effective
  HF join key (tradeable cid for NegRisk, recovered via `data/negrisk_map.py`; native for V2/Legacy),
  so NegRisk rows carry a `category` and join the fill tape.
- The DuckDB join recipe + the NegRisk map explainer are baked into the card. License `CC-BY-4.0` (matches upstream).
- Publish (needs `hf auth login`): `hf upload <ns>/polymarket-oov2-disputes-v1 dataset_release/polymarket-oov2-disputes-v1 . --repo-type dataset`.
- The backfill runs to chain head (keyless Polygon RPC, no indexer) — this is the complete set;
  regenerate any time with `python -m data.export_disputes --with-price-context`.

## 5b. Dispute base rates — the λ signal, ALL adapters (1,527 disputed markets)

Regenerated 2026-07-05 over the FULL adapter set (V2 + NegRisk + Legacy + other; the 1,794 release
disputes collapse to **1,527 unique disputed markets** via the effective join cid). Per-category
dispute base rate (disputed / resolved markets), with Wilson 95% CIs:

| Category | Disputes | Resolved | Rate | Wilson 95% |
|---|---:|---:|---:|---|
| **entertainment** | 59 | 2,793 | **2.11%** | [1.64%, 2.72%] |
| **politics** | 292 | 15,953 | **1.83%** | [1.63%, 2.05%] |
| economics | 13 | 1,014 | 1.28% | [0.75%, 2.18%] |
| geopolitics | 73 | 8,026 | 0.91% | [0.72%, 1.14%] |
| tech-ai | 54 | 10,298 | 0.52% | [0.40%, 0.68%] |
| sports | 150 | 87,854 | 0.17% | [0.15%, 0.20%] |
| other | 742 | 563,325 | 0.13% | [0.12%, 0.14%] |
| **crypto** | 144 | 170,446 | **0.085%** | [0.072%, 0.099%] |

**The NegRisk numerators change the story materially.** On V2/Legacy alone (the old 723-only table),
entertainment looked near-safe (0.11%, n=3); with the NegRisk era included it is the **most
dispute-prone category (2.11%, n=59)** — culture/award markets with ambiguous resolution criteria.
Politics doubles to 1.83%. The headline selection edge survives and sharpens: **politics is still ~22×
more dispute-prone than crypto** (1.83% vs 0.085%), and the top of the table (entertainment, politics,
economics, geopolitics) is exactly what `λ_select` should avoid or size down. Reproduce with
`DATA_SOURCE=graphql python -c "from data.dossier import dispute_base_rates; print(dispute_base_rates())"`.
(Old V2/Legacy-only rates for comparison: politics 0.92%, geopolitics 0.57%, crypto 0.042% — those were
lower bounds, missing the 2024+ NegRisk numerators.)

---

## 6. What the dataset unblocks (table → consumer)

| PolyLambda consumer | HF table(s) | `data/` entry point |
|---|---|---|
| `sigma.fetch_fills` (σ tape) | `order_filled ⋈ market_data` | `data.fills.fetch_fills_hf` |
| `sigma.category_price_prior` corpus | sampled markets → σ | `data.prior_corpus.build_sigma_observation_corpus` |
| `recon.run_recon` ground truth | `condition.payoutNumerators` | `data.conditions.hf_payout_map` |
| `lambda_engine.category_base_rate` denominator | `market_data ⋈ condition` | `data.base_rates.category_counts_hf` |
| `replay_ablation.run_replay` controls + tapes | `market_data` + `order_filled` + `condition` | `data.cache.materialize_slice` |
| dispute **labels** (numerator) | — *(not in HF; released companion dataset §5c)* | `data.disputes.load_disputes` (release parquet default; indexer via `DATA_SOURCE=graphql`) |

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
pip install -r requirements.txt          # adds duckdb, huggingface_hub, pyarrow, eth-utils/eth-abi
python -m data.dossier                    # §3–5 (cheap: single-file + metadata + dispute base rates)
python -m data.dossier --full             # + §1 counts and wash prevalence (full scans)
pytest tests/test_data_fills.py tests/test_disputes.py   # deriveFill + deriveConditionId parity (offline)

# dispute labels (no Docker) → λ + the replay-ablation, end-to-end:
python -m data.disputes                   # load + summarize the released dispute layer (1,794, all adapters); RPC backfill only if the release parquet is absent
python -c "from data.cache import prefetch_state_tables, materialize_slice; \
           from data.disputes import load_disputes; prefetch_state_tables(); \
           materialize_slice([d['conditionId'] for d in load_disputes()][:12], years=(2022,2023))"
python -m forwardtest.replay_ablation     # arms A/B/C × λ*-grid on real disputes, net of forgone rewards

# WITH the local Envio indexer (Docker up) → V2+NegRisk labels + recon + the release artifact:
GRAPHQL_URL=http://localhost:8080/v1/graphql python -m recon.check   # pass_rate + no_ground_truth (NegRisk gap)
python -m data.export_disputes            # → dataset_release/polymarket-oov2-disputes-v1/{disputes.parquet,README.md,stats.json}
DATA_SOURCE=graphql python -m forwardtest.replay_ablation            # same replay, disputes sourced from the indexer
```

**Verified end-to-end, control-matched** (56 disputed + 223 control markets, 2022–2023). The λ signal
is the **category dispute base rate** (not per-market volatility), so `λ*` is scaled to that range
(~0.0003–0.009). Corrected arm P&L (pnl_net / sharpe), across the λ*-grid:

| arm | λ*=0.0005 | λ*=0.005 | λ*=0.01 |
|---|---:|---:|---:|
| diffusion_only (λ off) | 1408.6 / 0.167 | 1408.6 / 0.167 | 1408.6 / 0.167 |
| **lambda_jump** (surgical exit) | **1536.8 / 0.183** | 1502.9 / 0.179 | 1408.6 / 0.167 |
| lambda_select (blanket avoidance) | 620.6 / 0.112 | 1102.4 / 0.138 | 1408.6 / 0.167 |

Two things this (post-fix, control-matched) result gets right that the first pass did not: (1) **the
λ*-sensitivity is real** — all arms **converge to diffusion at λ*=0.01** (above every category base rate
→ the signal never fires → B=C=A, a clean sanity check the buggy version failed because a hardcoded
`proposal_detected=True` short-circuited the threshold); (2) **arm C actually tests category selection**
(the base rate), not volatility. The finding: **λ_jump beats diffusion by ~9% at low λ*** (1536.8 vs
1408.6) with the edge shrinking as fewer exits fire; **λ_select is worst** — at λ*=0.0005 it forfeits
**977 of reward** to avoid only **189 of loss** (blanket category avoidance forfeits reward income),
recovering toward diffusion as λ* rises and it avoids fewer markets. **The edge is the surgical exit,
not blanket avoidance** (DECISIONS.md §A) — now on *correct* math, with matched controls. Honest
caveats: **n=56 disputed** (underpowered — read via `power_calc`); fill-tape mid (no order book);
2022–2023 *thin* era only; simplified reward model. The primary edge proof — formerly a
`NotImplementedError` — now runs on real, control-matched data with an interpretable,
correctly-computed signal.

**Indexer-sourced confirmation (2026-07-03).** With `DATA_SOURCE=graphql`, `run_replay` now sources the
disputed labels from the **local Envio indexer** instead of the RPC path. Over the identical cached
slice, all 56 disputed conditionIds are confirmed present in the indexer's joinable set (an independent
cross-check of the RPC-derived labels), and the arm table reproduces. The 2024+ NegRisk-dominated liquid
era is **no longer out of reach**: the tradeable-cid map (§5a, `data/negrisk_map.py`) joins NegRisk to
the HF fill tape at 100%, and the powered liquid-era replays have since run — see §5b′ (2024 NegRisk
slice) and §5b″ (1,409 disputed + 2,856 controls, all adapters, 2022–2026).
