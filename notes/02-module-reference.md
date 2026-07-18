# 02 · Module reference

> **Source of truth.** The modules themselves. Line numbers are approximate anchors (they drift with
> edits) — grep the symbol name if a line is off. Signatures are current as of this writing.

Interface convention: clob adapters are **duck-typed** — anything with
`get_book / get_micro / place / cancel / step / tape` works as a clob in `execution.loop`.

---

## `estimators/` — the statistical models (pure math + thin data wrappers)

### `estimators/sigma.py` — belief-volatility σ
Pipeline: wash-filter → logit returns → winsorized EWMA → James-Stein shrink toward a
(category × price-bucket) prior; falls back to the prior when the cleaned tape has `< min_trades`.
- `logit_returns(prices) -> list[float]` (:41)
- `ewma_sigma(returns, b) -> float` (:47) — `v_t = b·v_{t-1} + (1-b)·r_t²`, returns √v
- `robust_ewma_sigma(returns, b, winsor_k=5.0) -> float` (:57)
- `shrink(sigma_market, sigma_prior, n_obs, strength) -> float` (:69) — `w = n/(n+strength)`
- `wash_filter(fills, min_size) -> list[dict]` (:78) — drops self-crosses (maker==taker), sub-min, out-of-range
- `category_price_prior(observations, category, price_level, default=0.5) -> float` (:96)
- `estimate_sigma_from_fills(fills, *, prior, b=0.94, min_size=1.0, min_trades=20, strength=20.0, winsor_k=5.0) -> float` (:113) — the pure orchestrator
- `estimate_sigma(graphql_url, condition_id, category, prior, **cfg)` (:149) — I/O wrapper
- const `PRICE_BUCKETS` (:25)
- **Imports:** `data.fills.fetch_fills_hf` (lazy). **Imported by:** `execution.loop`, `data.prior_corpus`, `forwardtest.runner`.

### `estimators/lambda_engine.py` — the dispute jump model (two signals)
Emits `lambda_select` (slow, market-selection/sizing) and `lambda_jump` (jump intensity for the pricing
premium + exit trigger), plus `jump_drift`, `e_loss`, and a Wilson CI.
- `@dataclass LambdaOutput(lambda_select, lambda_jump, jump_drift, e_loss, ci_low, ci_high)` (:28)
- `category_base_rate(category, dispute_counts=None, counts=None) -> dict` (:47) — delegates to `data.base_rates`
- `fit_hazard(labeled_rows) -> (model, metrics)` (:67) — class-weighted `LogisticRegression`, reports Brier; needs ≥30 rows spanning both classes
- `estimate_lambda(market_conditionid, features, *, dispute_counts=None, model=None, kappa_loss=DEFAULT_KAPPA_LOSS) -> LambdaOutput` (:90)
- consts `SAFE_FEATURES = (category_base_rate, market_size, proposer_reliability, latency_anomaly)` (:39), `DEFAULT_KAPPA_LOSS=0.76` (:44)
- **Imports:** `data.base_rates`, `data.disputes` (lazy), sklearn/numpy. **Imported by:** `estimators.hazard`, `forwardtest.runner`, `forwardtest.replay_ablation`, `webapp` services.

### `estimators/hazard.py` — structural dispute-onset model (feature builder + persisted predictor)
Builds point-in-time-safe features, the labeled training set, fits, and persists a dependency-light JSON
predictor that `estimate_lambda(model=...)` consumes. **The deployed model is size-only** —
`category_base_rate` + `market_size`; `proposer_reliability` / `latency_anomaly` are a *proven null* kept
at 0 (:197).
- feature transforms: `market_size_feature` (:30), `proposer_reliability_feature(..., exclude=True)` (:35), `latency_anomaly_feature` (:50), `feature_row(...)` (:59)
- `class LoadedHazard(coef, intercept, feature_order, offset=0.0)` with `predict_proba(X)` (:75) — no sklearn/pickle at load
- `save_hazard_model` (:101) / `load_hazard_model` (:111) → `.data_cache/hazard_model.json`
- training set builders: `build_training_rows(*, control_per_category=200)` (:271), `build_matched_training_rows(...)` (:335, CEM-matched controls), `_cem_match(...)` (:298), `load_controls_from_indexer(n, *, graphql_url=None)` (:208)
- `train_and_cache(*, matched=False, graphql_url=None, path=...)` (:383), `fit_and_save(...)` (:417, held-out Brier+AUC + prior-correction offset)
- **Entry point:** `python -m estimators.hazard [--matched] [--graphql-url] [--path]` (:453)
- **Imports:** `data.hf`, `data.base_rates`, `data.disputes`, `data.metadata`, `data.prior_corpus`, `data.negrisk_map`, duckdb, sklearn. **Imported by:** `forwardtest.runner`, `forwardtest.replay_ablation`.

### `estimators/fair_value.py` — model mid
Depth-weighted book mid + light tapered favorite-longshot tilt; no lookahead.
- `depth_weighted_mid(bids, asks) -> float` (:17) — raises if a side is missing
- `favorite_longshot_tilt(mid, T_t, *, strength=0.02, taper_horizon=10.0) -> float` (:30)
- `estimate_fair_value(book, T_t, *, strength=0.02, taper_horizon=10.0) -> float` (:39)
- **Imported by:** `execution.loop`.

---

## `pricing/` — Avellaneda-Stoikov quote core (zero I/O)

### `pricing/quote.py`
Works in log-odds; the directional jump skews the reservation price; guards a `(T-t)→0` floor and a
boundary-tightened inventory cap; maps quotes back to price via an endpoint-sigmoid.
- `to_logit(p)` (:31) / `to_prob(x)` (:37)
- `reservation_logit(x_mid, q, gamma, sigma, T_t)` (:43) — `r = s − q·γ·σ²·(T−t)`
- `diffusion_spread_logit(gamma, sigma, T_t, k)` (:48) — `γσ²(T−t) + (2/γ)·ln(1+γ/k)`
- `jump_premium_logit(kappa, lam, e_loss)` (:55) — `κ·λ·E[loss]`
- `price_half_spread_via_jacobian(p, half_spread_logit)` (:61) — `p(1−p)·h` (intuition/tests)
- `inventory_cap(p, base_cap)` (:68) — tightens toward 0/1
- `@dataclass QuoteParams(gamma=0.5, k=5.0, kappa=1.0, min_horizon=0.02, boundary_floor=0.002, base_inventory_cap=100.0)` (:74)
- `compute_quote(mid, q, sigma, T_t, *, lam=0.0, e_loss=0.0, jump_drift=0.0, params=None) -> (bid, ask)` (:84) — the main entry; `bid < ask` in price space
- **Note:** `k` = A-S liquidity/order-arrival; `kappa` = jump-premium weight (distinct knobs).
- **Imported by:** `execution.loop`, `config.loader` (uses `QuoteParams`).

---

## `execution/` — the quoting loop + adapters

### `execution/loop.py` — quoting loop + reward-aware exit-on-risk
Two layers: (1) the liquidity-rewards score model + the exit gate; (2) `MarketState` + `tick()` + `run_loop`.
- reward model: `_reward_score(mid, bid, ask, bid_size, ask_size, max_spread, min_size)` (:47), `forgone_rewards_if_exit(market_state)` (:64)
- `should_exit(lambda_jump, lambda_star, e_jump_loss, forgone_rewards, spread, proposal_detected) -> bool` (:89) — `(proposal OR λ_jump>λ*) AND (E[loss] > forgone_rewards + spread)`
- `@dataclass MarketState(cid, token_id, category, arm, end_date_ts, inventory=0, cash=0, order_ids, micro, lam=None, sigma_prior=0.15, defensive=False, sim_reward_score=0, n_exits=0)` (:108) — `arm ∈ {lambda_on, lambda_off}`
- `tick(state, book, now_ts, cfg, clob, log=None, *, proposal_detected=False) -> MarketState` (:133) — one decision cycle
- `run_loop(markets, mode="paper", *, n_ticks=100, interval_s=5.0, clob=None, log=None, proposal_detector=None, cfg=None, start_ts=None)` (:245)
- consts: `REDUCE_FRACTION=0.5`, `LIGHT_FACTOR=0.3`, `DANGER_WINDOW_DAYS`, `DAILY_FLOOR_USD`
- **Note:** the exit gate runs on the **λ-ON arm only** (:156). `proposal_detected` defaults to a stub (v2, always-False, :274) — live, exit fires on `λ_jump > λ*`.
- **Imports (lazy):** `estimators.fair_value`, `estimators.sigma`, `pricing.quote`, `config.loader`, `execution.paper`. **Imported by:** `forwardtest.runner`, `forwardtest.replay_ablation` (`should_exit`), `webapp` services.

### `execution/clob.py` — Polymarket CLOB V2 wrapper (ungated READ / hard-gated WRITE)
Uses the official **py-sdk** (`polymarket-client==0.1.0b13`, import `polymarket`), **not** py-clob-client.
- READ (no auth): `read_book(token_id)` (:60), `get_market_microstructure(token_id)` (:83), `read_trades(token_id, since_ts=0, *, limit=500)` (:119)
- WRITE (gated by `_require_live_gate`, :143): `place_order(..., post_only=True)` (:241), `cancel_orders(order_ids)` (:292), `wrap_usdce_to_pusd(amount)` (:323)
- `class LiveGateError` (:139), `_SdkOrderAdapter` (:164), `_live_client()` (:201, lazy)
- consts `CLOB_REST`, `GAMMA_REST`, `DATA_API`, `COLLATERAL_ONRAMP`, `USDC_E`; reads `BUILDER_CODE` env (:262)
- **Imported by:** `execution.paper` (read path only). Live shapes are fixture-tested (network SNI-blocked).

### `execution/paper.py` — simulated adapters
- `class PaperClob(token_ids, *, seed=7)` (:91) — fully synthetic, deterministic; `SyntheticBook` (:51, seeded latent logit random walk + Poisson taker prints), `SimOrder` (:34)
- `class PaperLiveClob(token_ids)` (:177) — REAL public book/tape via `execution.clob` read path; fills via `ConservativeFillModel` (:145, queue-honest — rests behind all same-price depth; every fill tagged `queue_model="conservative"`)
- **Imports:** `execution.clob` (read only). **Imported by:** `execution.loop`, `forwardtest.runner`.

---

## `forwardtest/` — the forward-test harness + edge proof

### `forwardtest/runner.py` — paper / paper-live harness
Builds `MarketState`s, runs `run_loop`, writes JSONL, returns per-market/per-arm summary. **P&L excludes
`sim_reward_score` by construction.**
- `select_real_markets(n_markets)` (:82) — reads `dataset_release/.../disputes.parquet`
- `build_markets(market_rows, *, hazard_model=None, sigma_corpus=None, cfg=None, dispute_counts=None, seed=7)` (:96) — REAL λ + σ prior
- `run(mode="paper", markets=None, *, n_ticks=20, interval_s=0.0, out_path=None, seed=7, n_markets=4, cfg=None, source="synthetic", hazard=False)` (:140)
- consts `PAPER_UNIVERSE` (:29), `_PAPER_KAPPA_LOSS=1.5`
- **Entry point:** `python -m forwardtest.runner --mode --ticks --interval --seed --markets --out --source --hazard` (:247)
- log → `.data_cache/sessions/session-{mode}-s{seed}-n{ticks}.jsonl`

### `forwardtest/replay_ablation.py` — THE PRIMARY edge proof
Historical counterfactual over disputed markets (local labels) + matched HF controls. Arms: **A**
diffusion-only, **B** `lambda_jump` reward-aware surgical exit, **C** `lambda_select` blanket avoidance,
+ optional **B_hazard** (structural λ). Reports P&L net of forgone rewards + Sharpe across a `lambda_star`
grid with a pre-registered power calc.
- `@dataclass AblationResult(arm, lambda_star, n_disputes, n_controls, pnl_net_of_rewards, sharpe, avoided_loss, forgone_rewards)` (:39)
- `power_calc(markets_quoted, dispute_rate, resting_fraction)` (:51)
- `load_disputes(graphql_url)` (:62) — DATA_SOURCE switch (hf parquet vs graphql)
- `_replay_market(...)` (:112, calls `execution.loop.should_exit`), `run_replay(graphql_url, lambda_star_grid, *, control_ratio=3, fill_limit=5000, ...)` (:186)
- **Entry point:** `python -m forwardtest.replay_ablation` (:299) — grid `[0.0005,0.001,0.002,0.005,0.01]`

### `forwardtest/ablation.py` — LIVE λ-ON vs λ-OFF reader (underpowered by design)
- `run_live_ablation(session_log_path)` (:55), `_arm_rollup(records, arm)` (:18), const `MIN_DISPUTES_FOR_SIGNAL=10`
- **Entry point:** `python -m forwardtest.ablation <session_log.jsonl>` (:92)

### `forwardtest/session_log.py` — JSONL record schema
Every record carries `{t, type, mode, simulated:true}`. Types: `session_start, tick, quote, fill, exit,
dispute_witnessed, session_end`.
- `open_log(path)` (:27), `append(fh, record_type, *, mode, t=None, **fields)` (:33), `read(path)` (:42, crash-tolerant)

---

## `data/` — historical backbone over the HF dataset (via DuckDB)

- **`hf.py`** — DuckDB connection + source switch + path resolver + verified column registry. `table_path` (:78), `connect()` (:94, cached, httpfs), `with_retry` (:115), `query`/`query_df` (:137/:142), `live_columns` (:147, drift guard). Consts `HF_DATASET`, `DATA_SOURCE`, `CACHE_DIR`, `TABLE_LAYOUT`, `COLUMNS`.
- **`disputes.py`** — OOv2 dispute labels **without Docker**. `derive_condition_id(adapter, ancillary)` (:101) = keccak(adapter‖keccak(ancillary)‖2); `load_disputes()` (:371, released parquet → indexer → RPC fallback), `dispute_counts_by_category()` (:409, the λ numerator), `fetch_oov2_disputes` (:117), `resolve_indexer` (:189). Consts `OOV2`, `NEGRISK`, `ADAPTER_OF`, `RELEASE_PARQUET`. **Entry:** `python -m data.disputes` (:436).
- **`negrisk_map.py`** — recovers UMA↔tradeable conditionId for NegRisk from `QuestionPrepared` logs. `build_negrisk_map` (:85, canary-validated), `load_negrisk_map()` (:127), `derive_negrisk_cid` (:58). **Entry** (:150).
- **`base_rates.py`** — λ denominator + Wilson CI. `category_counts_hf()` (:20), `category_base_rate(category, dispute_counts, counts=None)` (:54), `_wilson(k, n, z=1.96)` (:43).
- **`fills.py`** — CLOB fill tape from `order_filled` (SQL port of `indexer/src/lib.ts:deriveFill`). `fetch_fills_hf(condition_id, *, limit=5000, years=None, canonical_token=None)` (:56) → single-axis-normalized `{price,size,side,maker,taker,timestamp}`.
- **`metadata.py`** — market metadata + tokenId↔conditionId + derived category. `derive_category` (:30), `tokens_for_condition` (:50), `canonical_token` (:61), `market_meta` (:67). Const `CATEGORY_KEYWORDS`.
- **`conditions.py`** — recon ground truth. `resolved_conditions` (:24), `payout_for` (:43), `hf_payout_map()` (:55), `resolution_counts()` (:68).
- **`cache.py`** — materialize a local slice. `prefetch_state_tables` (:24), `materialize_slice(condition_ids, ...)` (:49), `clear()` (:105).
- **`prior_corpus.py`** — build the σ prior corpus. `build_sigma_observation_corpus` (:26), `build_and_cache_sigma_prior` (:46), `load_sigma_prior` (:63). Cache `.data_cache/sigma_prior.json`.
- **`calibrate.py`** — data-derived `kappa_loss`. `calibrate_kappa_loss` (:28), const `KAPPA_LOSS_CALIBRATED=0.76`. **Entry** (:50).
- **`dossier.py`** — reproducible DATASET.md numbers. `main(full=False)` (:131). **Entry:** `python -m data.dossier [--full]` (:143).
- **`export_disputes.py`** — package the dispute layer as the releasable HF dataset (`dataset_release/`). `export_dispute_dataset(...)` (:273). **Entry** (:323, argparse).

---

## `recon/` — reconciliation invariant (eligible-set gate)

### `recon/check.py`
Compares each eligible indexed `Market.finalOutcome` to the HF payout vector; exclusion buckets are
first-class (a NegRisk phantom conditionId is `no_ground_truth`, counted as coverage, never a mismatch).
- `@dataclass ReconReport(eligible, matched, excluded_*, excluded_no_ground_truth=0, mismatches=None)` with `.pass_rate` (:34)
- `run_recon(graphql_url, rpc_url="", confirmation_depth=128, *, chain_head_ts=None, log=print)` (:91)
- **Entry point:** `python -m recon.check` (:163). **Imported by:** `webapp` services (recon-live).

---

## `indexer/` — Envio HyperIndex (TypeScript/ReScript) — LEGACY / OPTIONAL

> **Not the label producer any more.** The shipped
> `dataset_release/polymarket-oov2-disputes-v1/disputes.parquet` is now produced from **keyless RPC**
> (`data.disputes.load_disputes_rpc` → `data/export_disputes.py --source auto`), and the RPC rows were
> validated exact against the indexer rows. `indexer/README.md:52` puts it plainly: *"If you just need
> dispute labels, don't run the indexer at all."* Nothing on the default path requires it; it survives
> as an optional second implementation, plus the only source for `estimators.hazard --matched` and the
> live-ablation / live-recon panels (which degrade honestly to the published artifacts).

Indexes the Polymarket resolution lifecycle on Polygon (chain 137) → Postgres + Hasura GraphQL. **CLOB
fills are NOT indexed here** (those come from HF via `data/fills.py`).
- `config.yaml` — contracts `ConditionalTokens`, `UmaCtfAdapter` (V2+NegRisk+Legacy), `OptimisticOracleV2` (ProposePrice/DisputePrice/Settle); keyless RPC; `start_block: 28000000`.
- `schema.graphql` — entities `Market`, `ResolutionRequest` (two-strikes), `Dispute`, plus join indices `QuestionIndex`, `RequestIndex`.
- `src/EventHandlers.ts` — the lifecycle handlers. `src/lib.ts` — `deriveConditionId` (:14, TS twin of `data.disputes.derive_condition_id`), `deriveFill` (:41, parity-tested against `data/fills.py`).

---

## `webapp/`, `contracts/`, `scripts/`

Covered in detail in [06-onchain-webapp.md](06-onchain-webapp.md). In short: `webapp/backend` (FastAPI)
serves the built `webapp/frontend` (React/Vite) SPA and bridges to the real engine via `services.py`
(paper research endpoints) and `chain.py` (the on-chain Amoy market); `contracts/PolyLambdaMarket.sol`
is the on-chain market; `scripts/` generates the engine wallet, deploys the market, and runs an on-chain
e2e lifecycle test.
