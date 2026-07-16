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
- **Remaining (external ops, not code):** stand up a persistent at-head Envio indexer per `indexer/DEPLOY.md` and set `INDEXER_GRAPHQL_URL` to it — then the LIVE badge, disputes merge, and `?live=1` ablation all light up automatically. The dev deploy is still 14d stale (`advancing=false`).

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
