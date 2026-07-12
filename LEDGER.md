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

## Day 07 — 2026-07-03
Phase: 3 → Track A — release the dispute-label dataset + lock the V2/Legacy proof (indexer now live)
Learn: the running local indexer DECIDES the NegRisk question. It captures NegRisk disputes with the
       **authoritative** on-chain conditionId (from ConditionPreparation, not derived), yet those
       conditions are **0% joinable to HF** — V2 100% (147/147) vs NegRisk 0% (0/104) in the SAME
       2024–25 era, and HF `market_data` endDate→2028 (not head-lagged). So the powered NegRisk replay
       is blocked at the **data layer** (no HF fill tape under the underlying conditionId), NOT the
       indexer. The indexer's real payoff: it cross-validates the 723 (V2 100% HF-join), reconciles
       `finalOutcome` vs HF `payoutNumerators` at pass_rate 1.0, and yields the net-new public
       dispute-label dataset HF lacks.
Build: `data/disputes.py` `load_disputes_from_indexer` (V2+NegRisk+Legacy, `hf_joinable` flag; primary
       when `DATA_SOURCE=graphql`); `recon/check.py` `excluded_no_ground_truth` bucket + self-contained
       paginated/authed fetch; `data/export_disputes.py` → `dataset_release/polymarket-oov2-disputes-v1/`
       (parquet + HF card + stats, recon-provenance baked in); DATASET.md §5a/§5c + METHODOLOGY §5
       corrected to the data-layer finding.
Done-Checks:
- [x] `load_disputes_from_indexer`: 1000+ disputes; **V2 659/659 (100%) HF-joinable, NegRisk 0/350 (0%)**
- [x] recon at scale vs live indexer: **pass_rate=1.0000 on 25,873 eligible**; `no_ground_truth` bucket = NegRisk gap
- [x] export: `dataset_release/…/disputes.parquet` (~1,300 rows) + README card + stats.json; DOUBLE price cols
- [x] indexer-sourced replay (`DATA_SOURCE=graphql`): λ_jump > diffusion > λ_select at λ*=0.0005, converge at λ*=0.01 ✓
- [x] **40 pytest green** (+2: `load_disputes_from_indexer` adapter/join map, recon `no_ground_truth` bucket)
- [ ] regenerate FINAL export/recon/full 56+223 replay when the backfill reaches HF head (~block 85.9M; at ~76.9M now)
Gate status: on-track — the dispute-label release + the corrected proof stand; the NegRisk fill gap is
       documented as a data-layer limit, not an indexer failure.
Next first action: at backfill completion → `python -m data.export_disputes` (+ `--with-price-context`)
       + `python -m recon.check` + the full indexer-sourced replay; then `huggingface-cli upload`.

---

## Day 08 — 2026-07-05
Phase: 3 → Track A — OVERTURN the NegRisk finding: it IS in HF; unblock the powered replay
Learn: the Day 07 "NegRisk 0% joinable / data-layer-blocked" verdict was **WRONG** — an artifact of our
       indexer's PHANTOM conditionId (`QuestionInitialized` falls back to `deriveConditionId(0x2f5e…)`,
       fabricating an id that exists nowhere on-chain). NegRisk markets TRADE under a conditionId whose
       oracle is the NegRiskAdapter `0xd91E80cF…`, recoverable from the NegRiskOperator
       `0x71523d0f…` `QuestionPrepared` event (topic3=UMA qid, topic2=qid_d91e); tradeable cid =
       keccak(d91e ++ qid_d91e ++ 2). Root cause of the wrong probe: **tenderly `eth_getLogs` silently
       returns EMPTY for >1M-block ranges** (chunk ≤400k + positive control). Also: HF ships ~10 tables
       we never registered (`position`/`orderbook`/`neg_risk_event`/…); `SNAPSHOT.json` pins the cutoff
       (block 85,948,287 = 2026-04-24). And `Dispute.disputeTs` is the OO REQUEST ts, not block time.
Build: `data/negrisk_map.py` (Operator scan → {umaQid: tradeableCid}, canary + tests); registered 7 HF
       tables in `data/hf.py` (verified vs live schema); wired the tradeable cid through
       `load_disputes_from_indexer`, `load_disputes`, `replay_ablation.load_disputes` (the last was
       silently replaying every NegRisk dispute AS A CONTROL), and `export_disputes.py` (released
       `conditionId` = effective join key). Corrected DATASET §5/§5a/§5b′/§5c + METHODOLOGY §5. (Recon
       left V2/Legacy-only: a NegRisk finalOutcome bridge was tried and REVERTED — the indexer keys
       NegRisk finalOutcome by the phantom cid, so bridging the truth lookup mismatched; recon can't
       validate a phantom-keyed outcome without an indexer change.)
Done-Checks:
- [x] NegRisk map: **132,004 questions, 100.0% tradeable cids in HF**; derivation validated 6/6 (HF join + ConditionPreparation agreement)
- [x] dispute join rate: **NegRisk 0/350 → 943/943 (100%)**; every adapter 100% (V2 723/723, other 108/108)
- [x] recon stays **pass_rate 1.0000 on the eligible V2/Legacy set** (~21k at this checkpoint; NegRisk
      phantom-keyed finalOutcome isn't HF-comparable → stays in no_ground_truth; the DATASET join uses the tradeable cid, 100%)
- [x] **powered NegRisk-2024 replay** (26 disputed + 132 controls, materialized slice): λ_jump 1888.7
      (sh 0.375) > diffusion 1882.2 (0.373) > λ_select 0.0 at λ*=0.0005; converge at λ*=0.01 ✓
- [x] **44 pytest green** (+`test_negrisk_map`, recon bridge test rewritten); adversarial review workflow: 0 regressions
- [x] backfill reached the **HF cutoff** (block 85,960,271 > 85,948,287) after 22 self-healing runs
- [x] **FINAL export at cutoff**: `dataset_release/` = **1,794 disputes, 100% joinable** (V2 723/723,
      **NegRisk 963/963**, other 108/108), 2022-12-28 → 2026-04-09; recon **pass_rate 1.0 on 29,349 eligible** V2/Legacy
- [~] price-context (`--with-price-context`) + broader multi-year powered replay: materializing the full
      disputed+control fill slice (year-by-year; remote hf:// order_filled reads are slow/flaky)
- [ ] follow-ups: regenerate λ base-rate table over all adapters; enrich `disputeTs` with true block time; commit (awaiting go-ahead)
Gate status: DONE (core) — the project's biggest documented limitation is DISPROVEN; NegRisk is fully
       joinable (963/963), the powered liquid-era edge proof holds, the backfill reached the HF cutoff,
       and the final release (1,794 disputes, 100% joinable, recon 1.0) is regenerated. Uncommitted pending go-ahead.
Next first action: finish price-context + broader replay off the materialized slice; then commit the
       session's work + `huggingface-cli upload <ns>/polymarket-oov2-disputes-v1 …` (both await user go-ahead).

---

## Day 09 — 2026-07-06
Phase: 3 → Track A — price-context export FINAL + broader multi-year powered replay
Learn: the powered replay at full scale settles it. Widening from the Day-08 surgical slice (26+132) to
       the whole release universe — **1,409 disputed + 2,856 controls, all adapters, 2022–2026**, off the
       15.2M-fill local slice with TRUE block-time dispute ts — the ordering **λ_jump > diffusion >
       λ_select holds at EVERY λ\*** on the grid, net of forgone rewards. λ_jump's edge over always-hold is
       largest where exits fire most (λ*=0.0005: **+6,910 pnl / +0.060 Sharpe**) and NARROWS monotonically
       as λ* rises (frozen 0.002: +1,911 / +0.015; 0.01: +1,480 / +0.012) — so the honest deliverable is
       the whole **sensitivity curve, not the tuned point** (DECISIONS.md #11). λ_select forfeits so much
       reward (48,554 at λ*=0.0005) it never beats diffusion anywhere. Separately, `--with-price-context`
       populated the pre/post price + realized-logit-jump columns (1,620 / 1,200 / 1,149 of 1,794).
Build: ran `python -m data.export_disputes --with-price-context` → final `dataset_release/` (1,794 rows,
       15 cols incl. price context); broader replay over the release parquet's joinable cids + sampled
       controls across λ*∈[0.0005, 0.01]; DATASET.md **§5b″** (the powered multi-year table) + §5c/columns
       updated. (Runs off the already-materialized 15.2M-fill slice — no remote hf:// scan.)
Done-Checks:
- [x] price-context export: parquet 1,794 rows; preDisputePrice 1,620 / postDisputePrice 1,200 / realizedJumpLogit 1,149 populated
- [x] release final: stats.json 1,794 disputes, **100% HF-joinable** (V2 723 + NegRisk 963 + legacy 108); recon pass_rate 1.0
- [x] **broader powered replay** (1,409 disp + 2,856 ctrl, 2022–2026, all adapters): **λ_jump > diffusion > λ_select at every λ\***; edge monotone in λ* (+6,910/+0.060 @0.0005 → +1,480/+0.012 @0.01)
- [x] DATASET.md §5b″ records the powered table + the "publish the curve, not the point" caveat
- [ ] HuggingFace upload: DEFERRED — needs `hf auth login` + network; command handed off, not run
Gate status: DONE — the dispute-label release is final with price context, and the powered multi-year
       replay confirms the reward-aware surgical-exit edge at scale (n=1,409). Committed; upload deferred.
Next first action: `hf auth login`, then `hf upload <ns>/polymarket-oov2-disputes-v1
       dataset_release/polymarket-oov2-disputes-v1 . --repo-type dataset` to publish.

---

## Day 10 — 2026-07-06
Phase: 3 → Track B/execution — the paper forward-test engine (runner + live ablation) is complete
Learn: with the FROZEN A-S config (`config/model.yaml`), a paper quote sits ~5c off mid — wider than
       the synthetic book's 1-tick touch AND the 3c reward band — so paper mode posts two-sided quotes
       every tick but structurally never fills or earns reward credit. That is honest, not a bug: the
       queue/fill mechanics are validated directly (`test_paper_fill`), and each market is assigned to
       EXACTLY ONE arm (as a real live ablation must — one book can't be quoted two ways), so the runner
       is a plumbing/schema check while the powered edge proof stays the historical replay. Confirmed the
       loop is provably network-free: paper mode touches no socket (import-smoke + lazy imports hold).
Build: `forwardtest/runner.py` `run()` — builds a MarketState per arm (lambda resolved ONCE at
       session start), drives `execution.loop.run_loop`, writes the full session log
       (session_start → tick/quote/fill/exit → session_end with per-arm totals), P&L = cash+inv·mark
       ONLY; `forwardtest/ablation.py` `run_live_ablation()` — a pure, crash-tolerant JSONL reader that
       splits lambda_on/off, reports the ON−OFF delta + n_disputes, and ALWAYS emits the underpowered
       caveat; `tests/test_runner.py` (8) + `tests/test_ablation.py` (5); import-smoke extended to the
       execution engine in `tests/test_data_layer.py`.
Done-Checks:
- [x] C7 runner: paper harness drives `run_loop`; session log schema-complete (session_start…session_end); both arms logged
- [x] C8 ablation: pure reader; lambda_on/off split + ON−OFF delta + n_disputes; underpowered caveat always present
- [x] honesty invariant TESTED: P&L = cash + inv·mark only; `sim_reward_score` reported separately, never folded in
- [x] import-smoke extended → `config.loader`, `execution.{clob,paper,loop}`, `forwardtest.{session_log,runner,ablation}` (all network-free)
- [x] paper smoke round-trips: `python -m forwardtest.runner --mode paper` → session log → `python -m forwardtest.ablation`
- [x] **89 pytest green** (+13: 8 runner, 5 ablation)
- [ ] paper-live / live: BLOCKED — need the public CLOB WS/REST (polymarket.com, ISP-blocked here) + jurisdiction (JURISDICTION.md); v2 live-client wiring stays gated behind `LiveGateError`
Gate status: DONE — the v1 paper forward-test engine is complete and green; live paths remain intentionally gated (jurisdiction + network), not missing.
Next first action: once network/jurisdiction allow, run `MODE=paper-live python -m forwardtest.runner
       --mode paper-live` for 9-10 days of real-tape microstructure, then `run_live_ablation` (still
       underpowered — corroborate against the historical replay, DECISIONS.md #11).

---

## Day 11 — 2026-07-06
Phase: 3 → infra — HyperSync migration + Envio hosted deploy; backfill the indexer to chain head
Learn: HyperSync re-indexed the scoped dispute lifecycle **28M → chain head (61.7M blocks) in under a
       minute**, vs the keyless-RPC path that needed 22 self-healing restarts and stalled at 82.4M.
       The dispute-label layer now runs to the present, but everything past the HF cutoff (block
       85,948,287 = 2026-04-24) is **labels only — NOT HF-joinable** (no fill tape / price context /
       replay use; HF is upstream and ends there). The Envio hosted **dev** endpoint exposes a
       restricted public role (1000-row cap, aggregates off, rejects `x-hasura-admin-secret`), so the
       full 1.25M-market recon isn't runnable there — recon'd the **disputed subset** (the actual
       subject) instead.
Build: `indexer/config.yaml` → HyperSync (drop the `rpc:` block so Envio uses its default Polygon
       HyperSync endpoint). ⚠ This change lives on the **`envio` deploy branch only** (commit 68a2b95);
       **main stays keyless-RPC / separate** by request. Deployed via the Envio hosted service from the
       `envio` branch (Indexer Directory `./indexer`, Config File `config.yaml`); verified against the
       hosted GraphQL endpoint `indexer.dev.hyperindex.xyz/0638687`.
Done-Checks:
- [x] HyperSync migration: `rpc:` → default HyperSync; `pnpm codegen` clean; generated config `is_hyper_sync=true` (on `envio`, NOT main)
- [x] hosted deploy synced to head: `latest_processed_block = 89,756,131` = chain head (28M→head in <1 min)
- [x] disputes **1,794 → 1,847** (+53 past the old block-85.96M sync point); most recent **2026-07-01**; 1,570 unique disputed markets
- [x] focused recon (disputed V2/Legacy in the HF window): **538/538 match HF payout, pass_rate 1.0000, 0 mismatches**
- [~] full 1.25M-market recon: NOT run against the row-capped public endpoint — needs the hosted admin secret or a local re-sync
- [ ] post-cutoff disputes are NOT HF-joinable (labels only) — re-export to the release only if wanted (would add `hf_joinable=false` rows)
Gate status: DONE — the hosted indexer is live, synced to chain head, and the disputed-set recon is 1.0.
       Deploy config is isolated on `envio`; `main` untouched (still keyless-RPC).
Next first action: (optional) `python -m data.export_disputes --graphql-url <hosted-endpoint>` to fold the
       +53 post-cutoff disputes into the release, OR provide the hosted admin secret for a full recon.

---

## Day 12 — 2026-07-06
Phase: 3 → integration — wire the estimator BRAIN into the runtime + build the hazard λ model
Learn: audit vs `quant-implementation-full.excalidraw` (3 agents) found the pricing core + reward-aware
       exit complete and wired, but the **brain the diagram centers on was bypassed**: λ never flowed
       through `estimate_lambda` (paper used a hardcoded constant; `fit_hazard` had zero callers), σ
       shrank to a static 0.15, three config knobs were ignored, and two Panel-F decisions (size∝1/risk,
       time-based inventory cap) were missing. Fixing the inventory cap surfaced that paper markets used
       a stale past end-date anchor → T_t≡0; added a controlled decision clock. On the hazard model: the
       first fit scored AUC 0.95 — a LEAKAGE artifact (proposer_reliability nonzero only for disputed
       markets since controls had no proposer). The honest v1 rests on features fairly computable for
       both classes (category_base_rate + market_size) → **held-out AUC 0.68**; proposer/latency zeroed
       (can't be computed for arbitrary controls without leakage — indexer ResolutionRequest doesn't
       cover most HF controls). Base rate stays the honest default (DECISIONS #9 confirmed, not beaten).
Build: `estimators/hazard.py` (features + class-weighted logistic via existing `fit_hazard` + prior-
       correction to natural prevalence + held-out AUC/Brier + JSON-persisted `LoadedHazard`);
       `data/calibrate.py` (kappa_loss=0.76 from realizedJumpLogit); `forwardtest/runner.py`
       `build_markets`/`select_real_markets` (+`--source data`/`--hazard`); `execution/loop.py`
       inverse-risk sizing + time-decaying inventory cap + honors reduce_fraction/light_factor/
       shrinkage_strength + controlled `start_ts` clock; `data/prior_corpus.py` σ-prior cache;
       `config/{model.yaml,loader.py}` new frozen knobs. Tests: `test_hazard` (5), `test_loop_sizing`
       (6), `test_runner` real-builder (1); import-smoke extended. METHODOLOGY §3b.
Done-Checks:
- [x] λ WIRED: `run(source="data")` routes markets through `estimate_lambda` — real base rates (politics 1.83% ≫ crypto 0.085%) + Wilson CI, not a constant
- [x] hazard model built + integrated (λ_jump = calibrated logistic when `--hazard`); **held-out AUC 0.68**, honestly reported vs base rate; proposer/latency zeroed to avoid leakage
- [x] kappa_loss calibrated (0.76 = mean |realizedJumpLogit|), replaces the 0.05 placeholder
- [x] σ prior via category×price corpus wired (falls back to sigma_ref); `shrinkage_strength` now applied
- [x] SIZE ∝ 1/risk + hard time-to-resolution inventory cap (position can only shrink near T→0); config knobs honored
- [x] **101 pytest green** (+12: 6 sizing, 5 hazard, 1 real-builder)
- [ ] live path stays DEFERRED by design (py-sdk client, pUSD wrap, Builder Codes send — jurisdiction-gated); low-latency proposal watcher still v2 *(→ superseded Day 15: write path implemented behind the intact gate, never executed; watcher still v2)*
Gate status: DONE — every non-jurisdiction gap between Panels D/E/F/L and the code is closed; the paper
       forward-test now exercises the real brain end-to-end. Live leg intentionally gated, not missing.
Next first action: (optional) fit the hazard on proposed-but-not-disputed indexer controls (fair proposer
       feature, v2); otherwise the base-rate engine + wired execution are ready for the paper-live tape.

---

## Day 13 — 2026-07-06
Phase: 3 → edge proof — put the hazard λ on trial in the replay-ablation (does it BEAT the base rate?)
Learn: AUC 0.68 says the hazard *discriminates*; the honest question is whether it *trades* better.
       Injected it as a 4th replay arm (`lambda_jump_hazard`: identical reward-aware surgical exit as
       arm B, but the exit λ is the per-market structural hazard, not the flat category base rate) and
       ran it head-to-head over **362 disputed + 711 controls** across the λ*-grid. Result is **real but
       threshold-sensitive**: at the **frozen λ*=0.002 the structural λ WINS** — Sharpe 0.320 vs 0.274
       (+0.047), +1,093 pnl, avoiding +1,198 more jump-loss for +106 more forgone reward (it exits the
       big, jump-prone markets its `market_size` feature up-weights, holds the small ones). The edge
       holds for λ* ≤ 0.002 but **reverses at λ* ≥ 0.005** (the prevalence-recalibrated hazard pushes
       fewer markets over the higher threshold). So: directional evidence the structural λ sharpens exit
       TIMING at the operating point — NOT a uniform edge; underpowered (n=362, read via power_calc).
       Base rate stays the safe default; publish the whole curve, not the point (DECISIONS #11).
Build: `forwardtest/replay_ablation.py` — `_replay_market(lambda_hazard=…)` adds the B_hazard arm
       (arm B byte-for-byte unchanged, purely additive); `run_replay(hazard_model=…)` computes per-market
       structural λ from `[category_base_rate, market_size, 0, 0]` (market_size via the SAME true
       `_fill_count_map` training used — not the capped fills) and emits `lambda_jump_hazard`; CLI
       auto-loads the model. `tests/test_replay_hazard.py` (4). METHODOLOGY §3b extended with the table.
Done-Checks:
- [x] hazard arm injected + additive: arm B (base-rate) unchanged; `lambda_jump_hazard` runs the identical exit off the structural λ
- [x] head-to-head sample (362 disp + 711 ctrl): at frozen λ*=0.002, hazard Sharpe 0.320 > base 0.274; edge REVERSES at λ* ≥ 0.005
- [x] **POWERED rerun (1,409 disp + 2,912 ctrl) CONFIRMS**: at frozen λ*=0.002, hazard **Sharpe 0.302 > base 0.270** (+0.032, +3,756 pnl, +4,121 avoided-loss for ~+365 forgone); same reversal at λ* ≥ 0.005 — reproduced at full n, not a small-sample artifact
- [x] reported HONESTLY — threshold-sensitive exit-timing overlay; base rate remains the default
- [x] **105 pytest green** (+4 replay-hazard); train/serve market_size consistency (true fill count) enforced
- [ ] v2 fair-controls refit (proposer/latency features, indexer controls) would extend the model — deferred
Gate status: DONE — the hazard is proven in TRADING terms at full power: a real, reproducible,
       threshold-sensitive exit-timing improvement at the operating point (λ*≤0.002), honestly bounded.
Next first action: (optional) v2 fair-controls hazard refit; else the base-rate engine + the wired
       execution + the powered hazard-overlay finding stand.

---

## Day 14 — 2026-07-07
Phase: 3 → edge proof — v2 FAIR-CONTROLS hazard: does the leakage-free proposer feature add signal?
Learn: a clean NULL, reached through two artifacts caught in a row (the project's whole ethos). (1)
       v1 zeroed proposer_reliability because arbitrary HF controls had no proposer → nonzero only for
       disputed → AUC-0.95 leakage. Fixed it by sourcing controls from PROPOSED-BUT-NOT-DISPUTED indexer
       markets (real proposer, both classes). (2) That exposed a LIQUIDITY CONFOUND: disputed markets
       are far more liquid than controls, so market_size alone separates them — a naïve fair-controls
       fit scored an inflated AUC 0.96 on market_size, and controls read market_size 0 anyway (their
       fills weren't in the materialized slice). Controlling it with a market_size-MATCHED case-control
       fit (coarsened exact matching, 176 in-slice-liquid pairs) collapses discrimination to **held-out
       AUC ~0.50–0.64 (at/near chance; swings by split at n=176) vs size-only 0.70 — proposer coef ≈ 0
       (wrong-signed)**. VERDICT: **proposer reputation adds NO signal once liquidity is matched.** The structural "edge" was liquidity all
       along; deployed model stays size-only. (Ops: local indexer down (Docker off post-restart) →
       routed control-sourcing to the hosted HyperIndex endpoint w/ no-secret fallback; the full 2-h
       fill materialization proved impractical here (killed twice) → did the tractable in-slice matched
       study; original fill slice restored from backup.)
Build: `estimators/hazard.py` — `load_controls_from_indexer` (proposed-not-disputed, hosted-fallback +
       per-page retry, NegRisk tradeable-cid mapped), `_cem_match` (market_size CEM),
       `build_matched_training_rows` (the v2 evaluation harness), `_resolve_indexer`. Deployed path
       reverted to clean v1 (size-only; proposer/latency 0, documented as a proven null / v3).
       `tests/test_hazard.py` +1 (CEM balance). METHODOLOGY §3b: the v2 null.
Done-Checks:
- [x] leakage fixed: controls now carry a real proposer (proposed-not-disputed indexer markets), not disputed-only
- [x] liquidity confound identified: naïve fair-controls AUC 0.96 is market_size, not proposer
- [x] **market_size-matched case-control (176 pairs): held-out AUC ~0.50–0.64 (at/near chance, split-variant) < size-only 0.70; proposer coef ≈ 0 → NULL**
- [x] deployed model restored to clean v1 (size-only, AUC ~0.70); the confounded AUC-0.96 model NOT shipped
- [x] **106 pytest green** (+1 CEM match); harness kept for a future powered/matched (v3) study
- [ ] v3: a proposal-timestamp (`proposedAt`) indexer field for latency_anomaly + a fully-materialized powered matched study
Gate status: DONE — proposer_reliability is a clean, honestly-bounded NULL; the size-only hazard + the
       powered replay finding (Day 13) stand as the deliverable. No over-claim; two artifacts caught.
Next first action: (optional, v3) add `proposedAt` to the indexer + a powered matched study; else the
       base-rate/size hazard + wired execution + the λ*=0.002 replay edge are the standing result.

---

## Day 15 — 2026-07-07
Phase: consolidation — make the released dispute layer THE default everywhere, make training reproducible,
       implement (not execute) the gated live write path, and delete every stale story the corrections left behind.
Learn: three things worth recording. (1) NUMERATOR MIXING IS REAL: `python -m estimators.hazard` reproduces
       the deployed model end-to-end (n=3110, positives=1527, held-out AUC 0.697≈0.70; market_size coef
       0.247 matches), but the category_base_rate coef/offset shift vs the deployed cache — the deployed
       model was fit on the OLD 723-only base rates, and the numerator flip rescales that feature (the
       DATASET §4 "don't mix numerators" warning, observed in our own artifact). Deployed model left
       untouched (gitignored); regenerating is a deliberate operator step, and `--matched` now defaults to
       a separate `*_matched_eval.json` so the null-study can never clobber the deployed cache. (2) The
       adversarial review caught two real live-leg holes before any live use: `MAX_CAPITAL_USDC=nan`
       satisfied the gate while NaN-poisoning every `> cap` comparison (cap silently OFF → gate now
       requires a finite positive number), and the notional counter only incremented on confirmed acks
       (an ambiguous timeout-after-accept could leave a resting order uncounted → the cap now RESERVES
       before the send, fail-closed; only a definite INVALID_TICK rejection releases). (3) py-sdk CONFIRMED
       from the live repo/PyPI: pip `polymarket-client==0.1.0b13`, import `polymarket`, `SecureClient.create`
       does the L1 EIP-712 → L2 creds derivation; pinned exactly. Ops: recon's hosted fallback worked
       (resolver + COVERAGE-CAPPED warning printed) but a 34-min paginated pull died on one transient
       IncompleteRead → per-page retry added (same pattern as hazard's control pull). Envio alpha.21
       surfaced two pre-existing indexer-test breaks (createTestIndexer moved into `generated`;
       block-range validation vs start_block) — fixed, 8/8 vitest green against the PRUNED schema.
Build: `data/disputes.py` (load_disputes → released parquet default, RPC cache last resort; shared
       `resolve_indexer` + secret threading); `estimators/hazard.py` (`main(argv)` CLI, matched-eval path
       guard); `recon/check.py` + `data/export_disputes.py` (resolver reuse, hosted coverage-cap warnings,
       page retry); `execution/clob.py` (LIVE WRITE PATH behind the unchanged gate: `_SdkOrderAdapter` →
       `place_limit_order`, RateLimitError→"429" mapping for the backoff, INVALID_TICK single retry,
       reserve-before-send cap, `wrap_usdce_to_pusd` via lazy web3 approve+wrap, finite-cap gate);
       requirements pinned (`polymarket-client==0.1.0b13`, web3, eth-utils/eth-abi now listed);
       schema.graphql pruned (Fill/TokenMap/ReconStatus dead); sigma's dead GraphQL fill branch removed;
       docs refreshed (DATASET §4 base-rate note + §6/§8, METHODOLOGY §2, Readme, indexer README rewrite,
       15+ stale docstrings). Tests: 120→123 (+17 vs Day 14: resolver probe order, hosted recon/export
       fallbacks, load_disputes precedence + the 1,794 contract, live-client/adapter/wrap mocks, NaN-cap,
       exactly-once retry + reservation semantics, hazard CLI).
Done-Checks:
- [x] `load_disputes()` default = released parquet: **1,794** (v2 723 · negrisk 963 · other 108), verified via `python -m data.disputes` (sub-second, offline)
- [x] `python -m estimators.hazard` regenerates a hazard model end-to-end (2m50s local): n=3110/1527, AUC 0.697; numerator-shift vs deployed cache observed + documented (NOT silently redeployed)
- [x] live write path implemented BEHIND the intact gate — `_require_live_gate()` first line everywhere, paper modes import-clean (subprocess-verified: no requests/web3/polymarket on import), **never executed** (JURISDICTION.md still UNRESOLVED/paper-only)
- [x] adversarial review (4 lenses + refutation): 2 majors found + fixed (NaN cap, reserve-before-send); sigma docstring; recon page retry
- [x] **123 pytest green** (+17) and **8/8 indexer vitest green** against the pruned schema (`pnpm codegen` + lifecycle test pass)
- [ ] deployed hazard_model.json regeneration on the 1,794 numerator — deliberate operator step (rerun the replay after, since λ_hazard inputs shift)
- [ ] live-loop adapter (LiveClob for run_loop) + own-fill polling — still v2; runner keeps refusing MODE=live
Gate status: DONE — the data layer has ONE default story (the released parquet), training is one command,
       and the live leg is code-complete but jurisdiction-gated exactly as designed; every stale claim the
       NegRisk correction obsoleted is gone from the living docs.
Next first action: (operator) decide whether to regenerate the deployed hazard model on the 1,794 numerator
       and rerun the powered replay; (v2) LiveClob loop adapter + own-fill stream before any live session.

---

## Day 16 — 2026-07-11
Phase: Go-live planning (Builders Program)
Learn: Verified current Builders Program mechanics against builders.polymarket.com / program docs:
       continuous & permissionless; Builder Codes (bytes32 → `OrderFilled.builder`; only matched
       orders earn); builder fees ≤100bps taker / 50bps maker settle to the builder-profile wallet;
       weekly USDC pool split by attributed-volume share (Sun–Sat UTC epochs since 2025-11-02;
       third-party est. ~0.5–1% of attributed volume); grants = $2.5M, traction-gated. Consistent
       with DECISIONS.md #12 — no corrections needed.
Build: **JURISDICTION RESOLVED — option 1 (non-US operating entity)**; resolution row added to
       JURISDICTION.md (entity details to be recorded before the first real order). Authored
       `ROADMAP.md` (8-phase paper→live sequencing: network-truth/creds → auth reads → LiveClob
       adapter → RiskGovernor → selection/allocation + reorg-guarded proposal detector → live
       session log + dashboard → metrics-gated rollout ladder → grant package; each phase with a
       numeric exit gate; ranked go-live risks) and `BUSINESS_PLAN.md` (positioning, 4 stacked
       revenue lines, traction plan, cost structure, business risks, M1–M5 milestones). No engine
       code changed; the gate stays intact.
Done-Checks:
- [x] every file path / symbol cited in ROADMAP.md verified present (`_require_live_gate`,
      `_live_notional_spent`, `BUILDER_CODE` wiring at clob.py:262, loop.py:273 live-mode raise,
      session_log `simulated: True`, ablation `MIN_DISPUTES_FOR_SIGNAL=10`)
- [x] both docs cross-checked against DECISIONS.md (no dispute-lock language; two-strikes flow
      respected; pUSD not USDC.e; py-sdk pin not py-clob-client; replay = primary edge proof)
- [x] pool-rate figure labeled as third-party estimate, not a program guarantee
Gate status: PLANNING DONE — live leg unblocked on paper; nothing live may run until ROADMAP
       Phase 0's exit gate (on-chain builder-code attribution proof) passes on the non-US host.
Next first action: (operator, non-US host) ROADMAP Phase 0 — re-verify normalizer shapes vs live
       endpoints, re-confirm §D addresses on Polygonscan, verify the pinned SDK surface, register
       the Builder Code, and prove attribution with one manual post-only order.

---

## Day NN — YYYY-MM-DD
Phase:
Learn:
Build:
Done-Checks:
Gate status:
Next first action:
