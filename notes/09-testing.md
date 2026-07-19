# 09 · Testing

> **Source of truth.** `tests/test_*.py` (+ `conftest.py`), `indexer/test/`,
> `webapp/frontend/src/**/__tests__/`. Run with `pytest -q`.

## Philosophy — offline, deterministic, no network

The entire Python suite runs with **no network and no DuckDB**: data-layer logic is tested with pure
functions + fixtures, the CLOB live shapes are fixture-tested (real network is SNI-blocked), and the paper
adapters are seeded/deterministic. `conftest.py` just makes the repo root the pytest rootdir. This is what
lets the suite be a fast, reliable gate.

## Python suite — 19 files, 173 tests collected

> This table is hand-maintained and has silently drifted several times (44 → 101 → 123 → 141 → 172 → 173, with
> the docs lagging each time). **Verify, don't trust:**
> `.venv/bin/python -m pytest --collect-only -q | tail -1` — 173 as of 2026-07-19.

| File | # | Covers |
|------|---|--------|
| `test_sigma.py` | 9 | logit returns, EWMA / robust EWMA, `shrink`, wash filter, category-price prior, full pipeline |
| `test_quote.py` | 10 | A-S transforms, reservation/spread/jump terms, `compute_quote` boundary + inventory behavior |
| `test_fair_value.py` | 4 | depth-weighted mid, tilt taper, `estimate_fair_value`, missing-side error |
| `test_hazard.py` | 7 | feature transforms, `LoadedHazard.predict`, prior-recalibrated fit, `estimate_lambda(model=…)` |
| `test_loop_sizing.py` | 6 | inverse-risk sizing, time-to-resolution inventory cap, config-knob honoring |
| `test_forgone_rewards.py` | 9 | reward-score model + the `should_exit` gate |
| `test_paper_fill.py` | 8 | `PaperClob` synthetic fills + `ConservativeFillModel` queue honesty |
| `test_clob.py` | 18 | read-path normalizers (fixtures) + `LiveGateError` write-path gate |
| `test_config.py` | 6 | loader precedence, env overrides, `lambda_star` / `MODE` guards |
| `test_runner.py` | 9 | a complete deterministic honest paper session log |
| `test_ablation.py` | 5 | arm split + `underpowered` caveat (the live reader) |
| `test_replay_hazard.py` | 4 | the B_hazard arm vs arm B |
| `test_data_fills.py` | 4 | `deriveFill` SQL vs the TS `indexer/src/lib.ts` parity oracle |
| `test_data_layer.py` | 8 | pure data-layer logic + the rewired estimators |
| `test_liveness_refresh.py` | 23 | the post-pivot behaviour: parquet⊕live merge dedupe, RPC freshness gate, per-category calibration |
| `test_disputes.py` | 20 | no-Docker dispute logic (keccak derivation, joins, adapter coverage, HF-window guard) |
| `test_negrisk_map.py` | 4 | NegRisk map derivation + canary |
| `test_webapp.py` | 10 | FastAPI endpoints |
| `test_webapp_services.py` | 8 | the service layer (engine bridge) |

> **Coverage gap to know:** `test_ablation.py` tests only the *live* underpowered reader with synthetic
> fixtures. The **replay-ablation results** are pinned as a dated artifact
> (`forwardtest/results/replay_ablation_2026-07-11.json`, from the 2026-07-11 re-verification run) but
> are **not guarded by any regression test** — if the estimators/exit-gate change, re-run
> `python -m forwardtest.replay_ablation`, pin a fresh JSON, and update the docs by hand. Absolute PnL
> moves with control fill-tape coverage; the invariant to check is the arm ordering
> (λ_jump > diffusion > λ_select at low λ*). See [05-forwardtest-ablation.md](05-forwardtest-ablation.md).

## Indexer (TypeScript / Envio)

`cd indexer && npm test` runs two runners in sequence (both green as of 2026-07-11):

- **vitest** — `test/lib.test.ts` over `src/lib.ts` (`deriveConditionId`, `deriveFill` — the parity
  oracle for `data/fills.py`).
- **`node --test`** — `test/handlers.node.test.ts`, the full resolution-lifecycle integration test
  (`QuestionInitialized → ProposePrice → DisputePrice → QuestionReset` via Envio's
  `createTestIndexer`). It runs under plain node, not vitest, because envio's `HandlerLoader`
  registers `tsx/esm` module hooks and lazily imports the handler file through them — the same path
  `envio start` uses — which vitest's module pipeline can't reproduce (its transform trips on envio's
  ink TUI dependency graph). `vitest.config.ts` excludes `test/*.node.test.ts`; the npm script invokes
  `"$npm_node_execpath" --test` so the node that runs npm (≥22, per `engines`) also runs the test —
  a bare `node` in npm's script PATH can resolve to a stale system install.

## Frontend (React / Vite)

`cd webapp/frontend && npm test` — Vitest under `src/lib/__tests__/` and `src/test/`: export/format
helpers, the Amoy `testnet` constants, URL state, and an App smoke test.
