# PolyLambda — implementation notes (developer context)

> **Purpose of this folder.** A comprehensive, verifiable map of *what is actually implemented today*,
> written to be loaded as context for further development. Every file starts with a **Source of truth**
> pointer to the code so claims stay checkable. These notes describe current state only — no roadmap
> (see `../ROADMAP.md` for that).

## What PolyLambda is

A Polymarket market-making engine that prices UMA dispute risk as the jump term of its quoting
model. The thesis is stated once in [../Readme.md](../Readme.md), the model in
[../REPORT.md §2](../REPORT.md). These notes are the structural developer reference for what is
*actually implemented today*.

## How to navigate

| File | What it covers |
|------|----------------|
| [01-architecture.md](01-architecture.md) | System overview, package import graph, end-to-end runtime data flow, the three data planes, paper/paper-live/live gating |
| [02-module-reference.md](02-module-reference.md) | Every Python module: purpose, key functions/classes with `file:line`, imports/imported-by |
| [03-data-backbone.md](03-data-backbone.md) | HF dataset + DuckDB, no-Docker dispute derivation, NegRisk map, base rates, recon gate, released artifact |
| [04-model-pricing.md](04-model-pricing.md) | The jump-diffusion model, σ/λ/fair-value estimators, A-S logit pricing core + jump augmentation + exit gate |
| [05-forwardtest-ablation.md](05-forwardtest-ablation.md) | Paper harness, session-log schema, the replay-ablation edge proof (arms A/B/C + hazard), pinned numbers |
| [06-onchain-webapp.md](06-onchain-webapp.md) | `PolyLambdaMarket.sol`, engine-wallet vs user-signed paths, webapp backend/frontend, deploy configs, Amoy addresses |
| [07-config-reference.md](07-config-reference.md) | Every `config/model.yaml` + `Config` knob (default + meaning + env override) and all `.env` variables |
| [08-entrypoints-runbook.md](08-entrypoints-runbook.md) | Every `python -m …` entry point and how to run forward-test / ablation / recon / hazard-train / webapp / deploy |
| [09-testing.md](09-testing.md) | The pytest suite (file-by-file), frontend Vitest, indexer parity tests, the offline testing philosophy |
| [10-glossary.md](10-glossary.md) | Model / stats / pricing / data vocabulary in plain English |
| [11-testnet-proof.md](11-testnet-proof.md) | 2026-07-11 proof-of-life: the full on-chain Amoy lifecycle e2e (11 signed txns + Amoyscan links), hosted-app endpoint sweep, engine quote refresh |

## Relationship to the rest of the repo

- **Top-level docs** (`../Readme.md`, `../METHODOLOGY.md`, `../DATASET.md`, `../DECISIONS.md`,
  `../ANALYSIS.md`, `../JURISDICTION.md`, `../LEDGER.md`, `../ROADMAP.md`) are the canonical narrative /
  decision record / build ledger. These notes are the **structural developer reference** that complements
  them — deeper on module wiring, thinner on narrative.
- **Business / spec docs** — `../BUSINESS_PLAN.md` and `../polycool_info.md` (business context),
  `../WEATHER_COPYTRADE.md` (a design spec only — **no code exists for it yet**; it reuses the `data/`
  plane, not the λ/A-S engine).
- **Live demo** — the deployed dashboard + on-chain Amoy market: <https://polylambda.vercel.app>
  (see [06-onchain-webapp.md](06-onchain-webapp.md); proof-of-life run in
  [11-testnet-proof.md](11-testnet-proof.md)).
- **Diagrams** — `../quant-implementation-full.excalidraw` (the model study, panels A–N) and
  `../system-flow.excalidraw` (system zones ①–⑦). `04-model-pricing.md` mirrors panels A–J; Panel N /
  zone ⑦ mirror `06-onchain-webapp.md`.
- `day01-lifecycle.md` — the original day-1 bootstrap note (kept for history).
