# PolyLambda — implementation notes (developer context)

> **Purpose of this folder.** A comprehensive, verifiable map of *what is actually implemented today*,
> written to be loaded as context for further development. Every file starts with a **Source of truth**
> pointer to the code so claims stay checkable. These notes describe current state only — no roadmap
> (see `../ROADMAP.md` for that).

## What PolyLambda is (one paragraph)

PolyLambda is a Polymarket market-making engine built on one thesis: **quote two-sided liquidity with
Avellaneda-Stoikov in log-odds space, and use an OOv2 dispute-hazard signal (λ) to do reward-aware
exit-on-risk** — because on Polymarket a resolution/dispute is a *directional jump* you can trade out of
at a haircut (the CLOB stays open), not a freeze. The engine estimates σ (belief-volatility), λ (jump
intensity), and a fair-value mid, prices A-S quotes augmented with a jump premium + directional skew,
and pulls/reduces inventory only when `E[jump loss] > forgone rewards + spread`. It ships with a
**historical replay-ablation as the primary edge proof**, a paper/paper-live forward-test harness, a
released dispute dataset (labelled from **keyless RPC** — no indexer required), and a FastAPI+React
dashboard that also drives a live **on-chain testnet market on Polygon Amoy**. Everything defaults to
paper-only; the live mainnet CLOB write path is hard-gated by jurisdiction.

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
- **Live demo** — the deployed dashboard + on-chain Amoy market: <https://polylambda-9lu2.onrender.com>
  (see [06-onchain-webapp.md](06-onchain-webapp.md); proof-of-life run in
  [11-testnet-proof.md](11-testnet-proof.md)).
- **Diagrams** — `../quant-implementation-full.excalidraw` (the model study, panels A–N) and
  `../system-flow.excalidraw` (system zones ①–⑦). `04-model-pricing.md` mirrors panels A–J; Panel N /
  zone ⑦ mirror `06-onchain-webapp.md`.
- `day01-lifecycle.md` — the original day-1 bootstrap note (kept for history).
