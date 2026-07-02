# PolyLambda — build ledger

One block per working day. Keep it honest (Done-Checks are binary, not "mostly").

---

## Day 01 — 2026-07-01
Phase: 1 (learn) + scaffold
Learn: resolution lifecycle → `notes/day01-lifecycle.md`
Build: repo scaffold — runnable Envio indexer (ConditionalTokens + CTF Exchange) + Python
       skeleton; `pricing/quote.py` implemented + tested.
Done-Checks:
- [x] indexer scaffold created (config.yaml, schema.graphql, EventHandlers.ts, package.json)
- [x] `pricing/quote.py` implemented; `tests/test_quote.py` passing
- [ ] `pnpm dev` runs; `Fill`/`Market` rows visible in Hasura   ← needs recent start_block + Docker
- [ ] fixtures (1 resolved + 1 disputed) saved
Gate status: on-track
Next first action: set a recent `start_block`, run the indexer, confirm data lands.

---

## Day 02 — 2026-07-02
Phase: 2 (data backbone) — integrate the public HF dataset `moose-code/polymarket-onchain-v1`
Learn: the 1.17B-fill CLOB tape spans **2022–2026** (not 2020 — that's the FPMM era); HF has
       resolution outcomes but **no OOv2 dispute events** → the scoped local indexer is mandatory
       for λ labels. Full analysis in `DATASET.md`.
Build: `data/` DuckDB layer over `hf://` (hf, fills, conditions, metadata, base_rates, cache,
       prior_corpus, dossier); rewired `sigma.fetch_fills` (DATA_SOURCE switch), `recon.run_recon`
       (HF `condition.payoutNumerators` ground truth), `lambda_engine` (HF denominators + injected
       local dispute numerators + Wilson CI), `replay_ablation.run_replay` (two-source join);
       scoped `indexer/` down to the OOv2 dispute lifecycle only.
Done-Checks:
- [x] DuckDB installed; live schema verified (all VARCHAR/camelCase; layouts; epoch-s ts)
- [x] fill↔market join validated 30/30; deriveFill SQL↔TS parity-tested
- [x] real dossier numbers computed (1,172,658,611 fills; 992,485 resolved; category base rates)
- [x] full pytest green (33 passing; pure cores untouched)
- [x] indexer config + handlers scoped to OOv2 (no dangling CTFExchange refs)
- [ ] run the scoped local OOv2 indexer to produce the ~184 dispute labels (needs Docker/pnpm)
- [ ] materialize the replay slice, then run `replay_ablation.run_replay` end-to-end
Gate status: on-track — the historical backbone is live; λ + replay await the dispute-label backfill.
Next first action: `cd indexer && pnpm dev` (scoped OOv2) → confirm `Dispute` rows → materialize slice.

---

## Day 03 — 2026-07-02
Phase: 2 (data backbone) — dispute labels WITHOUT Docker + deepen the analysis
Learn: HyperSync/most public RPCs now need API keys; `polygon.gateway.tenderly.co` is keyless and
       allows 500k-block `eth_getLogs`. **NegRisk conditionIds are NOT derivable from OO ancillary**
       (0/56) — NegRiskIdLib assigns sequential questionIds; only V2/Legacy derive via
       `keccak(adapter,keccak(ancillary),2)` (validated 723/723 vs HF).
Build: `data/disputes.py` — pulls OOv2 DisputePrice via keyless RPC, derives conditionId, joins HF,
       caches to `.data_cache/disputes.json`. Wired into `lambda_engine.category_base_rate`
       (numerator) + `replay_ablation.load_disputes`. Added `dossier.dispute_base_rates` +
       `volume_by_year`; updated DATASET.md with the λ signal.
Done-Checks:
- [x] `data/disputes.py` backfill → **723 V2/Legacy disputes, 100% HF-joined**; 963 NegRisk counted (gap)
- [x] deriveConditionId parity test + 36 pytest green
- [x] real λ base rates: **politics 0.92% vs crypto 0.042% (~22×)** — the market-selection edge, in data
- [x] C3 end-to-end on real disputes: replay ran (diffusion +340.6 vs **λ_jump +350.1** sharpe 0.38, small slice)
- [x] fills_by_year: 74% of the 1.17B tape is 2026; derivable disputes are the thin 2022-24 era (key caveat)
- [ ] NegRisk disputes (963) — need the local indexer's NegRiskAdapter events (documented gap)
Gate status: on-track — λ + replay now run on REAL disputes (no Docker). NegRisk coverage deferred.
Next first action: finish C3 proof; optionally run the full ~700-market replay via a materialized slice.

---

## Day 04 — 2026-07-02
Phase: 2 (data backbone) — close the NegRisk gap (attempt) + full replay + robustness
Learn: NegRisk conditionId is **conclusively not derivable** from external UMA/OO events — tested 4
       formulas across 2 contracts + 2 event types (ancillary keccak 0/56; QuestionResolved qid ×
       {NEG_UMA, NEG_ADAPTER, identity} 0/60). It needs the NegRiskAdapter's own prepare events (the
       local indexer). Also: HF `order_filled` parquet **schema drifts across year partitions**
       (`SELECT *` → "don't know what type" — fixed with explicit casted columns), and one 2024
       monthly file has a persistent ZSTD read fault in this env.
Build: extended `data/disputes.py` probes (NegRisk, decisive negative); `run_replay` now takes
       explicit `disputed`/`controls` + reports PROCESSED counts; `materialize_slice` selects explicit
       columns + clears stale cache; `dossier.notional_by_year` per-year isolated; `data/hf.py`
       `with_retry`/`reset_connection` for flaky reads.
Done-Checks:
- [x] NegRisk recovery proven infeasible from external events (documented in DATASET.md §5)
- [x] **control-matched replay** (56 disputed + 248 controls): diffusion 1765.9 (sh 0.164),
      **λ_jump 1950.4 (sh 0.182, +10%)**, λ_select 784.6 (WORST — blanket avoidance forfeits reward
      income). Ablation says the edge is the **surgical exit**, not market-selection avoidance.
- [x] wrote **METHODOLOGY.md** (model + data backbone + λ signal + replay result + honest limits)
- [x] 36 pytest green; materialize/replay pipeline robust to schema drift + transient reads
- [ ] 2024+ (NegRisk) replay — needs local indexer + stable connection/HF_TOKEN (the powered, liquid era)
Gate status: on-track — primary edge proof runs on real data with a positive λ_jump signal (small N).
Next first action: run the scoped local indexer (Docker) for NegRisk 2024+ disputes → control-matched, powered replay.

---

## Day 05 — 2026-07-02
Phase: 2 — adversarial self-review + fixes (a multi-agent review audited the session's work)
Learn: the first replay result was **NOT trustworthy** — a hardcoded `proposal_detected=True`
       short-circuited the λ* threshold (so the "sensitivity curve" was flat/meaningless), and arm C
       filtered on **volatility** not the **category dispute rate**, so "λ_select is worst" was partly
       an artifact. Also: `copysign(x,0.0)` leaks +drift at neutral price; category counts dropped
       ~FPMM-era disputes. The review found **12 confirmed issues** (1 refuted).
Build: fixed replay-ablation (category-rate λ signal, `proposal_detected=False`, correct control
       handling, honest forgone accounting, base-rate-scaled λ*-grid); jump_drift neutral guard;
       dispute counts COALESCE→'other'; doc-honesty corrections. Re-ran the replay.
Done-Checks:
- [x] adversarial review workflow (5 dims → verify) → 12 confirmed fixes applied
- [x] **corrected** replay: arms now converge to diffusion at λ*=0.01 (sanity ✓); λ_jump 894.5
      (sh 0.283) > diffusion 766.3 (0.239) > λ_select 289.4 at λ*=0.0005; **λ*-sensitivity is real**
- [x] 38 pytest green (+2: jump_drift-neutral, replay arm logic); DATASET.md/METHODOLOGY.md corrected
- [x] **control-matched** re-run on a clean 2022-2023 slice (56 disputed + 223 controls): λ_jump
      1536.8 (sh 0.183) > diffusion 1408.6 (0.167) > λ_select 620.6 at λ*=0.0005; converge at λ*=0.01 ✓
Gate status: on-track — the edge proof is correct, control-matched, and honestly caveated; conclusion (surgical exit > avoidance) holds on fixed math.
Next first action: run the scoped local indexer (Docker) for the 2024+ NegRisk era → powered, liquid-era replay.

---

## Day 06 — 2026-07-03
Phase: 3 — run the local Envio indexer (Docker up) for the 2024+ NegRisk dispute era
Learn: Envio v3-alpha **auto-loads handlers from `src/handlers/` by default** — my `src/EventHandlers.ts`
       was silently NOT loaded (all events "skipped — no handler") until I added a per-contract
       `handler:` field. Also: HyperSync now needs a free `ENVIO_API_TOKEN`; a keyless RPC works but is
       slow/flaky for Envio's block fetches. NegRisk conditionIds still aren't keccak-derivable, so the
       indexer join is **lookup-based** (read conditionId from ConditionPreparation, keyed by questionId).
Build: node22 via nvm; Docker Postgres+Hasura come up; fixed handler loading; **adapter-agnostic
       lookup redesign** — added `QuestionIndex`/`RequestIndex` entities + reworked handlers so OO
       disputes resolve conditionId via RequestIndex (keccak fallback for V2); added NegRisk (0x2f5e) +
       Legacy (0x71392E) adapters. Config set for the full 28M→head HyperSync backfill.
Done-Checks:
- [x] `envio dev` stack runs (Docker+Hasura); handlers load (no more "skipped"); RPC smoke indexed blocks
- [x] lookup handlers + NegRisk/legacy: `envio codegen` clean, `lib.test.ts` 7/7 green
- [ ] full HyperSync backfill (needs `ENVIO_API_TOKEN` — user is fetching) → V2≈723 + NegRisk→HF >90%
- [ ] wire indexer disputes into `data/disputes.py` + powered 2024+ replay
Gate status: on-track — indexer stack + lookup redesign done; blocked only on the free HyperSync token.
Next first action: set `ENVIO_API_TOKEN` in indexer/.env → `envio dev` full backfill → verify counts.

---

## Day NN — YYYY-MM-DD
Phase:
Learn:
Build:
Done-Checks:
Gate status:
Next first action:
