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
- [~] C3 cache+σ-prior+replay end-to-end on real disputed markets (running)
- [ ] NegRisk disputes (963) — need the local indexer's NegRiskAdapter events (documented gap)
Gate status: on-track — λ + replay now run on REAL disputes (no Docker). NegRisk coverage deferred.
Next first action: finish C3 proof; optionally run the full ~700-market replay via a materialized slice.

---

## Day NN — YYYY-MM-DD
Phase:
Learn:
Build:
Done-Checks:
Gate status:
Next first action:
