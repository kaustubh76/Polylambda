# 01 · Architecture

> **Source of truth.** The package layout under the repo root; entry points enumerated in
> [08-entrypoints-runbook.md](08-entrypoints-runbook.md). Mirrors `system-flow.excalidraw` (zones ①–⑦).

## 1. System overview

PolyLambda is a Python market-making engine (+ a TypeScript Envio indexer + a React dashboard) with a
strict one-way dependency flow. The core is pure math and pure data-access modules; I/O lives at the
edges (data adapters, clob adapters, the web app). Everything is **offline-first** — the test suite runs
with no network and no DuckDB, and every live data plane degrades to an "offline" state independently.

## 2. Package import graph

```
config/                 ─────────────────────────────► consumed by everything (frozen params)
data/  (HF + DuckDB backbone, dispute labels)
   └──► estimators/  (σ, λ, hazard, fair_value)
           └──► pricing/  (Avellaneda-Stoikov in log-odds + jump augmentation)
                   └──► execution/  (loop, paper/paper-live/clob adapters)
                           └──► forwardtest/  (runner, session_log, ablation, replay_ablation)
recon/     validates indexed outcomes vs HF payouts (a hard gate)
indexer/   (TS/Envio) produces the dispute labels shipped in dataset_release/
webapp/    FastAPI + React over the engine; also drives the on-chain Amoy market (contracts/)
```

Rule of thumb: **arrows point down; nothing lower imports something higher.** `estimators` never imports
`execution`; `pricing` has zero I/O; `execution.loop` is the only place the math meets an order book.

## 3. End-to-end runtime data flow

1. **Config + data source** — `config.loader.load_config()` reads `config/model.yaml` + env into a
   `Config` (incl. a `pricing.quote.QuoteParams`). `data.hf` sets `DATA_SOURCE` (default `hf`) and
   resolves table paths (local `.data_cache` slice, else `hf://`).
2. **Labels & denominators** — `data.disputes.load_disputes()` yields OOv2 dispute labels (released
   parquet by default; NegRisk joined via `data.negrisk_map`). `dispute_counts_by_category()` is the λ
   **numerator**; `data.base_rates.category_counts_hf()` is the HF **denominator** →
   `category_base_rate()` returns a Wilson CI.
3. **Estimators** — `estimators.lambda_engine.estimate_lambda()` → `LambdaOutput(lambda_select,
   lambda_jump, jump_drift, e_loss, ci_low, ci_high)` (optionally using the persisted
   `estimators.hazard` structural model). `estimators.sigma` builds σ from the `data.fills` tape,
   winsorized-EWMA then shrunk toward the `data.prior_corpus` (category × price) prior.
   `estimators.fair_value` builds the mid from the book (depth-weighted + light favorite-longshot tilt).
4. **Pricing** — `pricing.quote.compute_quote(mid, q, sigma, T_t, lam, e_loss, jump_drift, params)`
   returns `(bid, ask)` in price space.
5. **Execution loop** — `execution.loop.tick()` reads the book from an injected clob adapter
   (`PaperClob` synthetic or `PaperLiveClob` real-read), recomputes fair/σ, evaluates the reward-aware
   `should_exit` gate (λ-ON arm only), sizes ∝ 1/risk, applies a hard time-to-resolution position cap,
   and places/cancels **post-only** quotes. `run_loop` steps the adapter and applies fills.
6. **Forward-test logs** — `forwardtest.runner.run()` runs both arms (`lambda_on` / `lambda_off`),
   streaming `session_start → tick/quote/fill/exit → session_end` to JSONL via `forwardtest.session_log`.
   **P&L = cash + inventory·mark**; the simulated liquidity-reward score is reported **separately**,
   never folded into P&L.
7. **Edge proof** — `forwardtest.replay_ablation.run_replay()` is the primary proof: replays arms
   A/B/C(+B_hazard) over historical disputes + matched HF controls, net of forgone rewards, across a
   `lambda_star` grid with a pre-registered power calc. `forwardtest.ablation` is a lighter
   (underpowered-by-design) sanity reader over a live session log.
8. **Recon gate** — `recon.check.run_recon()` compares each eligible indexed `Market.finalOutcome`
   against the HF payout vector before estimators consume indexed data.

## 4. The three independent data planes

The deployed system reads three *unrelated* sources; each can be offline without breaking the others
(mirrors `system-flow.excalidraw` zone ⑦ / `quant-implementation-full.excalidraw` Panel N):

| Plane | Source | Used by | Config |
|-------|--------|---------|--------|
| **Historical** | HF dataset `moose-code/polymarket-onchain-v1` via DuckDB over `hf://` | estimators, recon, base rates, replay-ablation | `DATA_SOURCE=hf`, `HF_DATASET` |
| **Live disputes** | Envio HyperIndex GraphQL (hosted or local) | `webapp` LiveIndexer, hazard controls, recon-live | `INDEXER_GRAPHQL_URL` |
| **On-chain market** | Polygon Amoy RPC → `PolyLambdaMarket` | `webapp` LiveTestnet section | `AMOY_RPC_URL`, `MARKET_ADDRESS` |

## 5. Run modes and gating

`MODE ∈ {paper, paper-live, live}` (validated in `config/loader.py`):

- **paper** — fully synthetic book/fills (`execution.paper.PaperClob`). Deterministic, seedable.
- **paper-live** — REAL public book + tape via the `execution.clob` read path; fills simulated by a
  queue-honest `ConservativeFillModel` (rests behind all same-price depth). No orders are ever sent.
- **live** — the only mode that can send mainnet orders; hard-gated by `execution.clob._require_live_gate`
  (needs `MODE=live` **and** `JURISDICTION_ACK=RESOLVED_SEE_JURISDICTION_MD` **and** a finite positive
  `MAX_CAPITAL_USDC`). Per `../JURISDICTION.md`, US persons are paper-only. The default is paper.

The **on-chain Amoy testnet** path is a *separate* surface (`webapp/backend/chain.py`) that is
testnet-guarded by `chain_id == 80002` and deliberately bypasses the mainnet gate — it is a demo, not
mainnet trading. See [06-onchain-webapp.md](06-onchain-webapp.md).
