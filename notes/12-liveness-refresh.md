# 12 · Liveness & data-refresh (de-staling the dashboard)

> **Why this exists.** A UI walkthrough (2026-07-15) found six panels that present frozen data as
> "live". Nothing is hardcoded in React — every panel fetches the FastAPI backend — but the backend
> serves a **April-frozen parquet** and a **stopped Envio dev deploy** while labeling them current.
> This note is the source of truth for the fix; see also [06-onchain-webapp.md](06-onchain-webapp.md).

## 1. Root-cause map (confirmed)

| # | Symptom | Root cause | File |
|---|---------|-----------|------|
| 1 | Paper engine graph never moves on slider change | recompute only on the Run button; sliders mutate local `cfg` | `webapp/frontend/src/sections/PaperSession.tsx:52` |
| 2 | Edge-proof re-run "works" but graph frozen | `useApi(…,[live])` + `setLive(true)` idempotent → refetches once; live replay needs `INDEXER_GRAPHQL_URL` **+ sklearn/HF deps stripped from the deploy image** → identical `ABLATION_PUBLISHED`; `live_error` never shown | `sections/Ablation.tsx:20`, `webapp/backend/services.py:187` |
| 3 | Model cards stale | `/api/hazard` reads frozen `.data_cache/hazard_model*.json` (trained coefficients, lru-cached, no inputs, no provenance) | `services.py:306`, `cache.py:52` |
| 4 | Dispute explorer stuck in April | `disputes.parquet` frozen at **2026-04-18**; nothing regenerates it; `date_max` hardcoded | `cache.py:108`, `constants.py:79` |
| 5 | Jump / price-impact charts frozen | same April parquet via `disputes_analytics`; fixed `random_state=7` sample | `services.py:441` |
| 6 | Live indexer says LIVE but 14d behind | `up` = reachability only, never compares `head_ts` to now; hosted Envio **dev** deploy stopped ingesting ~2026-07-01 (`block_height == latest_processed_block` frozen) | `sections/LiveIndexer.tsx:56`, `components/LiveStatus.tsx:30` |

**Structural gap:** #4/#5 + the truly-live half of #2/#6 all bottom out in **no persistent at-head
indexer and no automated data-refresh loop**. `render.yaml`/`fly.toml` point `INDEXER_GRAPHQL_URL` at
`indexer.dev.hyperindex.xyz/0638687` — a stopped, coverage-capped dev deploy.

Live probe (2026-07-15): head dispute `disputeTs=1782882335` = 2026-07-01, **14.2d stale**, latency ~610ms.

## 2. Chosen approach (user-approved 2026-07-15)

1. **Live indexer** — honest + self-healing *in code* (freshness-gated badge, auto-merge that catches
   up the instant a fresh URL is pointed at it), **and** prepare indexer deploy assets for the user to launch.
2. **Data** — request-time self-merge of the live feed **and** a scheduled regenerate/retrain job.
3. **Extra scope** — wire the real replay artifact into the edge-proof; replace the `jump_drift/e_loss`
   placeholder with per-category realized-move calibration. **Weather-copytrade: out of scope.**

## 3. Workstreams

### A — Reactive & honest UI (frontend only)
- **A1** `PaperSession.tsx` — debounced auto-run on `cfg` change (reuse `lib/useDebounced.ts`); keep Run/Replay. Same for `LiveQuoting`.
- **A2** `Ablation.tsx` — re-run bumps a `nonce` so every click refetches; render `d.live_error` as a caveat; keep `SourceTag` honest.
- **A3** `HazardCard.tsx` — "trained on `<date>` · n=`<N>`" stamp (consumes `trained_at` from B4).
- **A6** `LiveIndexer.tsx` + `LiveStatus.tsx` — tri-state from `now - head_ts`: LIVE (<~15min) / "syncing · Nh behind" (<~2d) / "stale · Nd behind" / offline.

### B — Self-healing live data (backend)
- **B1** `services.disputes()` — union parquet with `live.recent_disputes()`; map live→explorer cols (nulls for unenriched: category/marketName/prices/jump), dedupe `(conditionId, disputeTs)` preferring parquet, then existing filter/sort/paginate. Best-effort + TTL cache.
- **B2** `services.disputes_analytics()` — reads the same union; drop fixed `random_state=7`; live rows still `dropna` out of jump/price-impact until parquet regenerates.
- **B3** `live.py` — add `recent_disputes(limit, since_ts)` + `head_age_seconds` in `indexer_status()`.
- **B4** compute `overview()` `date_max` from `disputes_df()` (drop `"2026-04-18"` literal); `hazard()` adds `trained_at`+`n`; add `cache.refresh()` to clear lru_caches after a regenerate.

### C — Automation & indexer assets
- **C1** new `.github/workflows/refresh-data.yml` — cron: `data.export_disputes` → retrain hazard → `precompute --force [--ablation]` → recompute `KAPPA_LOSS_CALIBRATED` → commit `dataset_release/**` + `webapp/deploy/cache/**`; degrade gracefully without secrets.
- **C2** `indexer/DEPLOY.md` + `scripts/indexer_health.py` (head-age warner). Once user sets a live `INDEXER_GRAPHQL_URL`, A6+B1/B2 light up with no code change.
- **C3** `services._ablation_full_rows()` — also read newest `forwardtest/results/replay_ablation_*.json` (`results` list) when `ablation_full.json` absent → real 4-arm data instead of 3 constants.

### D — Modeling
- **D1** `data/calibrate.py` `calibrate_kappa_by_category()` (+ signed drift, shrink for thin cats) → `kappa_by_category.json`; `estimators/lambda_engine.py` uses per-category κ for `e_loss` + signed `jump_drift`, scalar fallback, keep neutral-price (p=0.5) zero-drift guard.

## 4. Verification
Local: `python -m webapp.backend.main` + `cd webapp/frontend && npm run dev`. Check A1 (sliders recompute), A2 (re-run refetches + caveat), A6 (badge = "stale · 14d behind"), B1/B4 (`/api/disputes` rows > 2026-04-18 when feed has them; `/api/overview` date_max computed), C3 (`/api/ablation` serves replay rows), D1 (`python -m data.calibrate` per-category κ; neutral-price guard holds). Run `pytest`, `indexer && npm test`, `frontend && npm run test` + new unit tests (merge dedupe, freshness gate, per-category calibration).

## 5. Progress log
- 2026-07-15: root-cause investigation complete (3 explore agents + live probe), plan approved, notes written. Execution started.
- 2026-07-15: **all workstreams landed.** A1–A6 (reactive paper engine, working+honest edge-proof re-run, freshness-gated LIVE badge, model-card provenance), B1–B4 (live-merge in `services._merged_disputes_df`, computed `date_max`, `head_age_seconds`, `cache.refresh()` + `POST /api/admin/refresh`), C1–C3 (`refresh-data.yml`, `indexer/DEPLOY.md`, `scripts/indexer_health.py`, replay artifact wired via `_ablation_full_rows`), D1 (`kappa_by_category.json` + per-category `e_loss`/`jump_drift`). Verified end-to-end: `date_max`/explorer now show **2026-07-01** (was April, self-heals to head), ablation source=`replay` (4 real arms), LIVE badge reads "stale · 14d behind", `score` e_loss differs by category. Tests: **149 pytest / 26 frontend / 1 indexer green**; frontend typecheck + prod build clean.
- ~~**Remaining (external ops):** stand up a persistent at-head Envio indexer…~~ **SUPERSEDED by §6** —
  the Envio free tier ended, so the live plane was pivoted to a keyless Polygon RPC scan. No indexer is
  required any more; see §6–§8.

## 6. Part 2 (2026-07-16) — Envio free tier ended → keyless RPC live feed + HF in the UI

The hosted Envio dev indexer ended; `INDEXER_GRAPHQL_URL` is dead. Pivoted the live plane onto the
repo's pre-existing keyless-RPC method and surfaced the HF backbone in the UI.

### E — Live disputes via keyless Polygon RPC (no indexer, no paid service)
- `data/disputes.py`: `recent_disputes_rpc()` (backward OOv2 `DisputePrice` scan from chain head, resilient
  bisection, proposer from `topics[2]`, price→YES/NO/UNRESOLVABLE), `chain_head_block/ts()`, `_rpc`
  endpoint failover across the public list. **NegRisk is counted but conditionId=None** (repo-consistent —
  the OO ancillary isn't the NegRisk cid; needs the operator events). Empirically: last OOv2 dispute was
  genuinely **2026-07-01** (block 89455646); the scan surfaces April 29→July 1 disputes absent from the parquet.
- `webapp/backend/live.py`: rewritten source-agnostic (**Envio if configured+fresh → RPC → offline**). Heavy
  tail scan (~45s) runs in a **non-blocking background thread** behind a 600s TTL cache, warmed at startup
  (`main.py` `_warm` → `live.warm_tail()`); status probe is a cheap `chain_head_ts()`. Freshness gates on the
  **chain-head** age (proves tip) — sparse disputes read LIVE-but-quiet, not stale. `head_age_seconds` +
  `source` flow to the UI; `freshnessFromAge` (new) is used instead of client-side `headTs` recompute.
- Deploy: `render.yaml`/`fly.toml` now set `POLYGON_RPC_URL` and drop the dead `INDEXER_GRAPHQL_URL`.
  LiveIndexer copy is source-aware (RPC vs Envio). Verified: `/api/live/status` source=rpc, chain age 0s → LIVE;
  explorer + `overview.date_max` now show **2026-07-01**.

### F — HF backbone surfaced in the UI (all four)
- `webapp/backend/precompute.py`: `build_hf_overview` (resolution mix — matches DATASET.md exactly:
  YES 398356 / NO 580992 / tie 13137 — markets-by-year, category counts, coverage), `build_hf_markets`
  (top-800 recent), `build_dispute_market_context` (1,527 disputed markets). Small JSONs shipped in
  `webapp/deploy/cache/webapp/` (1.5KB / 282KB / 302KB).
- `cache.py` loaders (+ `_ARTIFACT_LOADERS`), `services.hf_overview(live)` / `hf_markets(...)`, routes
  `/api/hf/overview` (+ guarded `?live=1` via HF_TOKEN) + `/api/hf/markets`; disputes rows enriched with
  `hfResolvedOutcome/hfEndDate`.
- Frontend: new `sections/HfDataset.tsx` (resolution donut, markets-by-year bar, category bars, coverage)
  + `sections/HfMarkets.tsx` (browser), dispute-detail HF enrichment, HF provenance lines on BaseRates +
  ScoreMarket, App.tsx NAV/GOTO/lazy/DeferSection wiring, dead Envio footer link removed.

### Status
All green: **pytest 151 / frontend 26 / indexer 1**, frontend typecheck + prod build clean. HF is a frozen
~April snapshot (provenance-stamped); the RPC feed covers everything newer. Refresh job (`refresh-data.yml`)
rebuilds the HF caches too.

## 7. Part 3 (2026-07-17) — the HF token: made to load, then spent

An HF token was added to `.env` as **`HF_ACCESS_TOKEN`**. It was doing nothing, for two reasons:
1. **Name mismatch** — all code read `HF_TOKEN`; `HF_ACCESS_TOKEN` appeared in zero files.
2. **`.env` never reached the CLI** — the only `load_dotenv` was a side-effect import in
   `webapp/backend/chain.py`, so `python -m webapp.backend.precompute` (where the heavy HF scans live)
   never saw it.

### G — token plumbing (`data/hf.py`)
`hf_token()` / `has_hf_token()` accept **either** name (read at call time, blank-safe); `load_dotenv` moved
into `data/hf.py` — the one module every HF entrypoint (webapp *and* CLI) imports, so precompute/
export_disputes/calibrate now authenticate with no exporting. Real process env still wins. `CREATE SECRET`
now escapes quotes (was a raw f-string). `.env.example` documents both names; `HF_TOKEN` added as a
`sync: false` secret to `render.yaml` / `fly.toml`.

### H — what the token unlocked (all verified against the Hub)
- **Real fills-by-year** (`_hf_fills_by_year`, ~10-20s): 2022 3,161 · 2023 328,176 · 2024 57,578,938 ·
  2025 241,199,667 · 2026 873,548,669 → **sums to exactly 1,172,658,611**, the number that was previously a
  hardcoded constant (now the fallback). ⚠️ **Trap:** `order_filled` IS local but only as the *disputed
  slice* (2024: 2.5M vs 57.6M) — must force `prefer_cache=False` or you silently publish slice counts as
  full-tape counts.
- **Real per-market volume** (`_hf_volume_cte`): `orderbook.id` (tokenId) → `market_data.id` → `condition`,
  summing both legs (`market_data.outcomeIndex` is NULL → no YES/NO split). `hf_markets.json` is now
  **top-600-by-volume ∪ 400-recent** (1,000 rows, 392KB) — top market **"Will Donald Trump win the 2024 US
  Presidential Election" $1.64B / 5.1M trades**. `dispute_market_context.json`: **1,409/1,527 disputed
  markets with volume** (1,409 = the replay's `n_disputes_with_fills` — a clean cross-check), max $177M.
  Both degrade to volume-less output without a token.
- **`?live=1` re-gated** — it used to call a full multi-table rebuild; the Docker image ships **no parquet
  at all** (`Dockerfile:33` copies only JSON), so on a 512MB host that was a guaranteed timeout/OOM. Now
  requires token **and** local parquet, else returns the cache + an honest reason in **0.00s**.
- **UI**: volume/trades columns (sortable, volume-default) in `HfMarkets`, real fill-tape chart + `built_at`
  provenance in `HfDataset`, market volume in the dispute detail modal, `compact`/`usdCompact` helpers in
  `lib/format.ts`.

### Status
**pytest 156 / frontend 26 / indexer 1** green; typecheck + prod build clean; keyless-RPC live feed
re-verified unaffected (chain head age ~1s). Secrets never printed or committed.

## 8. Part 4 (2026-07-17) — NegRisk live disputes are now LABELED (the last big gap)

Parts 2–3 left the live feed showing a bare "—" for ~96% of disputes: NegRisk dominates recent activity
and the RPC path left `conditionId=None`, so those rows couldn't join to names, categories or HF volume.
Closed by recovering the label **on-chain**, with no 36MB `negrisk_map.json` (not shipped to the image).

### The correction that unlocked it
I had reasoned (and the repo's own docstring asserts) that NegRisk "isn't label-joinable from an OO log".
That is true of the **conditionId** (sequential NegRiskIdLib ids) — but **NOT of the questionId**.
Verified on-chain: `QuestionInitialized.topic1 == keccak(its own ancillaryData)` **for NegRisk too**. So:

```
umaQuestionId = keccak(DisputePrice.ancillaryData)        # FREE — no RPC
  -> NegRiskOperator QuestionPrepared(topic3 = umaQid)    # topic3 indexed -> ONE batched getLogs
  -> questionId_d91e (topic2) -> derive_negrisk_cid()     # -> tradeable conditionId
```

**Evidence:** 963/963 released NegRisk disputes reproduce their exact release conditionId; on live
disputes **14/14** distinct qids resolved and **14/14 are present in the HF `condition` table**.
A first (discarded) design searched for the block via `requestTimestamp` — that works (the adapter
requests the price in the same block, delta 0s) but cost ~6.3s/dispute and missed reset questions
(`QuestionReset` carries no timestamp). The keccak route is **~1.1s for the whole batch** and complete.

### Result
`recent_disputes_rpc` → **20/20 labeled** (was 0/19 NegRisk); cold scan 72.8s → **16.8s**. Labels are
immutable → cached (`negrisk_live_labels.json`). `live._enrich_live_names()` then attaches real
marketName/category via a targeted `market_data` lookup (0.4s local / 13.3s Hub, background + cached),
so the stream and explorer now read *"Will Waymo operate in 11 cities on June 30 2026?"*,
*"Will Roberto Sánchez Palomino win the 2026 Peruvian presidential election"* — with real categories, so
the explorer's category facet works on live rows. Markets created **after** the HF April snapshot still
show no name (HF simply has no record yet) — an honest degradation, not a bug.

Also observed: a **new dispute landed 2026-07-16**, i.e. the feed is genuinely live, not frozen at July 1.

### Status
**pytest 159 / frontend 26 / indexer 1** green; typecheck + prod build clean. Label + name caches seeded
into `webapp/deploy/cache/` so a cold container starts warm.

## 9. Part 5 (2026-07-17) — the λ window guard + finishing the batch pivot

### The correction that drove this
I twice told the user the λ numerator was already safe because `load_disputes()` does `WHERE
hf_joinable`. **Wrong.** `hf_joinable` is **spatial** ("does this conditionId exist in HF") and never
consults `disputeTs`. A market prepared before the HF head but disputed after it is `hf_joinable=True`.
Measured on the shipped layer's 12 boundary markets: **12/12 hf_joinable · 12/12 in `n_markets` · only
7/12 in `n_resolved`** → appending post-cutoff disputes is **numerator +12 / denominator +7**, a
selection bias (a market disputed after the snapshot was probably still unresolved when HF froze), not a
"more complete" measurement. 7 of the 12 are **politics** — the headline category.

**Guard (P):** `load_disputes()` — the numerator's only choke point — and the three hazard reads now
bound `disputeTs <= HF_CUTOFF_TS`. Proven a **no-op today**: base-rate rows, headline, κ and hazard
positives (1527) byte-identical before/after. It only bites once the layer extends past the head.

**`HF_CUTOFF_TS = 1777016618` = 2026-04-24T07:43:38Z** — the on-chain block time of `HF_CUTOFF_BLOCK`
85,948,287. This settles a three-way doc contradiction: `dataset_release/README` (04-24) was right,
`DATASET.md` (04-09) was wrong, and the parquet (max 04-18) **never leaked** — the doc did.

**A "fix" I declined:** an audit claimed `hazard.py`'s `preDisputePrice … else 0.5` fabricates data into
the `disputed=1` class. Verified false — `price` is not in `SAFE_FEATURES` and `build_training_rows`
never reads it; it is carried only for the LIVE builder, where 0.5 → logit 0 → `jump_drift` exactly 0
("no directional claim"). Dropping those 174 rows would have shrunk the training set for nothing.

### Q — the release export no longer needs Envio
`export_disputes` was indexer-only (`resolve_indexer` → raise), so the dead deploy made the dataset
unmaintainable. `data.disputes.load_disputes_rpc()` returns the indexer's row shape from a pure RPC scan;
`build_rows(source="auto"|"indexer"|"rpc")` falls back to it. **Validated 12/12 exact
(conditionId, disputeTs) matches** vs the shipped release, with adapter/questionId/proposer/
proposedOutcome/hf_joinable agreeing. Derived: `round` (n-th dispute per questionId = the two-strikes
semantic) and true `disputeTs` block times (fixing a latent bug where a re-enabled export would have
written `disputeTs == requestTimestamp`). New `post_hf_cutoff` column makes the window legible in-data.

### R — verified pivot breakage repaired
`.env.example` pointed at the dead deploy (a fresh clone paid an 8s timeout per poll **and lost the RPC
feed**); `HOSTED_GRAPHQL_URL` defaulted to it (`resolve_indexer` 15–45s → **0.44s**); refresh-data.yml
gated the hazard retrain on `INDEXER_GRAPHQL_URL` though training makes **zero** indexer calls (so the
deployed model never retrained while base rates refreshed nightly); and the sync step cp'd two artifacts
no step produces.

### Status
**pytest 165 / frontend 29 / indexer 1** green. Committed on `feat/hf-token-and-negrisk-labeling`.

## 10. Part 6 (2026-07-17) — the release extends to now, and the gates found the release was wrong

The plan was "extend to now, adopt only if λ is byte-identical". Three gates ran. **Gate 1 failed — and
the failure was the finding.** It said the RPC scan did not reproduce the release: 14 in-window keys
disjoint, `proposer` disagreeing on 85/1780, `proposedOutcome` on 41/1780. The tempting read was "my
scan is buggy". Chain says otherwise.

### The release was wrong; the RPC is right (verified on-chain, not argued)
| claim | evidence |
|---|---|
| **85 wrong `proposer`** | re-decoded the real `DisputePrice` log at each dispute block: chain agrees with **RPC 12/12**, with the **release 0/12**. `disputer` agrees on **85/85** — so not a topic-order slip: the indexer's *join* mis-keyed `proposer` while reading `disputer` correctly. The RPC reads it from indexed `topics[2]`, which cannot mis-key. |
| **14 misattributed markets** | the 14 release-only disputes exist on chain **0/14**; their 14 RPC counterparts **14/14**. Same blocks, same timestamps, *different questionId* → the same 14 disputes, attributed to the wrong question and therefore the wrong `conditionId` and the wrong **market**. |
| **41 wrong `proposedOutcome`** | sign flips (NO→YES ×20, YES→NO ×8); chain agrees with RPC 12/12 vs the release's 10/12. |

**Why the earlier "12/12 validated" (§Q) missed all of it:** that was a 12-row spot check on a lucky
subset. 85 errors sat in the corpus the whole time. A sample that small cannot find a 5% defect rate —
only the full diff did.

### My own bugs found in the same pass
- **`round` was 1-based** against a release schema that defines `0 = first request` → *every* row looked
  like a reset round (1,794 with round>0 vs the release's 245). Nothing crashes when `round` is wrong.
- **The test asserted `[1, 2]` — it PINNED the bug** instead of catching it, and passed for two commits.
  Now `[0, 1]` with the reasoning recorded.
- **The first adoption plan would have destroyed κ.** It said "copy the RPC parquet over the release";
  that parquet was exported `with_price_context=False`, so `realizedJumpLogit` is all-null — and 1,149
  non-null *is* the global-κ n. Confirmed empirically: κ calibration **throws** on it, it doesn't
  degrade quietly. Fixed by re-exporting **with** price context.

### Reproducibility bugs — two published numbers were single draws, not facts
- **`recon.eligible` was non-deterministic**: `Market(limit, offset)` paginated with **no `order_by`**,
  so pages silently overlap/omit. Four runs over the SAME stalled indexer: **23,259 / 27,311 / 30,632 /
  35,977**. `pass_rate` stayed 1.0 throughout, which is exactly why it hid — the headline looked stable
  while its denominator wandered 50%. Fixed: `order_by: {id: asc}`.
- **`holdout_auc` was non-deterministic**: `_holdout_eval` *was* seeded, but a seeded shuffle of a list
  whose incoming order is arbitrary is still arbitrary — the rows are built off Python set iteration.
  Three runs on identical data: **0.7153 / 0.7055 / 0.7105**. Fixed by sorting before the shuffle;
  now byte-stable at **0.70864**.
- **A flaky test, root-caused not retried**: `test_block_timestamps_checkpoint_survives_a_crash` keyed
  off a shared `_rpc` call counter, so `live.py`'s background tail-refresh thread (still alive after
  `tests/test_webapp.py`) burned one of its four allowed calls → "only 3 of 4 checkpointed", depending
  on file order. Now block-keyed and hermetic. **The stray thread outliving its test is still real.**

### What shipped
- **1,848 disputes**, `2022-12-30 → 2026-07-16` (was 1,794, stalling at 2026-04-18 — the *actual* fix
  for "the explorer shows April"). `stats.json` gains **`in_window_disputes: 1794`**.
- **The guard did its job**: `load_disputes()` still returns exactly **1,794** (v2 723 / negrisk 963 /
  other 108) from an 1,848-row parquet — `tests/test_disputes.py` passes **unchanged**. The 54
  post-cutoff rows (negrisk 50 / v2 2 / other 2) are all `hf_joinable=True`: precisely the rows that
  would have biased the numerator, exactly as §9 predicted.
- **λ moved only where the old data was proven wrong**, within the bounds quoted before the call:
  politics **0.00%**, headline unchanged ("entertainment ~25× more dispute-prone than crypto"), worst
  case tech-ai **−3.70%**. κ global **0.75873 → 0.76047** (n 1149 → 1146).
- **Hazard retrained**: AUC **0.704 → 0.7086**, positives 1527 → 1522. The 0.704 model predated the
  NegRisk labeling — it was fitted on the V2-only 723-row numerator, which is why `natural_rate`
  corrects **0.00272 → 0.00735**. `proposer_reliability` has coef **0.0** (a proven null), so the 85
  corrections do **not** move the model — an earlier claim of mine that they would was wrong.
- **Post-cutoff rows carry no price context, by construction**: the HF fill tape ends at the cutoff, so
  none is derivable. Asserted in the test, not just argued.

### Also discovered
A **local Envio indexer is running** (`envio-postgres` + `envio-hasura`, up 9d) — "Envio is dead" is
true only of the *hosted* deploy. It is **stalled at block 85,960,271** vs head 90,389,991 (~4.4M
blocks / ~3.5 months), having stopped just past the HF cutoff. `run_recon` silently found it, which is
how the unstable recon numbers got into a fresh export at all. **`recon` is therefore carried forward
from the last indexer-backed run rather than recomputed** — the card and `constants.py` say so.

### Status
**pytest 171 / frontend 29** green (typecheck + build clean).
