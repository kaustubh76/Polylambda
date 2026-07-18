# 07 · Config & environment reference

> **Source of truth.** `config/model.yaml` (the freeze-doc) and `config/loader.py`; `.env.example` for
> environment. Precedence: **dataclass defaults < `model.yaml` < environment**.

## 1. `config/model.yaml` (frozen model parameters)

> `model.yaml` is the **FREEZE-doc** — freeze before the forward-test (tuning 5 knobs on ~0–3 live
> dispute events is fitting noise; publish `lambda_star` sensitivity curves from the *replay*, not one
> tuned point). `config/loader.py` uses a hand-rolled parser (no pyyaml dep).

### Avellaneda-Stoikov (`pricing/quote.py` → `QuoteParams`)
| Knob | Default | Meaning |
|------|---------|---------|
| `gamma` | 0.5 | risk aversion — higher = wider spreads, faster inventory shedding |
| `k` | 5.0 | order-arrival / liquidity — higher = fills fall off faster from mid |
| `kappa` | 1.0 | **jump-premium** weight (distinct from `k`) |
| `min_horizon` | 0.02 | `(T−t)→0` guard: floor on effective horizon |
| `boundary_floor` | 0.002 | minimum price-space half-spread near 0/1 |
| `base_inventory_cap` | 100.0 | base inventory cap (tightens toward 0/1) |

### σ estimator (`estimators/sigma.py`)
| Knob | Default | Meaning |
|------|---------|---------|
| `ewma_b` | 0.94 | EWMA memory (high = smooth/slow, low = twitchy/fast) |
| `shrinkage_strength` | 0.5 | pull per-market σ toward the (category × price) prior |
| `min_trades_for_sigma` | 20 | trade-count floor below which the prior is used outright |

> **Gotcha:** `estimators.sigma.estimate_sigma_from_fills` has its own default `strength=20.0`; the loop
> passes `getattr(cfg, "shrinkage_strength", 20.0)`, so the *effective* shrinkage is the yaml's `0.5`
> when config is loaded, `20.0` only if the attribute is missing. Don't be surprised by the two numbers.

### λ engine / exit (`estimators/lambda_engine.py`, `execution/loop.py`)
| Knob | Default | Meaning |
|------|---------|---------|
| `lambda_star` | 0.002 | exit threshold for `lambda_jump`. The λ signal is a **category dispute base rate** (~0.0004–0.021), so the old `0.15` could never fire — scale-fixed 2026-07-05. `loader.py` **raises** if `lambda_star > 0.05` (`LAMBDA_STAR_SCALE_BOUND`). |
| `kappa_loss` | 0.76 | `E[loss|jump]` scaling — **calibrated** from the released disputes' `realizedJumpLogit` (mean \|Δlogit\| = 0.76 over 1,149 disputes, `data/calibrate.py`) |
| `lambda_v1` | `base_rate` | v1 scope marker (not parsed into `Config`) |

### Execution sizing + inventory (`execution/loop.py:tick`)
| Knob | Default | Meaning |
|------|---------|---------|
| `sigma_ref` | 0.15 | reference belief-vol: at `sigma_ref` (and λ=0) size == `quote_size` |
| `size_floor` | 0.25 | size never shrinks below 25% of `quote_size` from the σ term |
| `size_lambda_k` | 20.0 | λ sensitivity of size (at `lambda_jump` 0.01 → size ÷1.2) |
| `inventory_cap_horizon_days` | 3.0 | hard position cap ramps to `base_inventory_cap` over this horizon; ~0 at resolution so inventory can only DECREASE near T→0 |

### Positioning / data block
| Knob | Default | Meaning |
|------|---------|---------|
| `positioning` | `both` | `reward_farmer` \| `jump_avoid` \| `both` (env `POSITIONING`) |
| `data.source` | `hf` | mirrors `DATA_SOURCE`; env wins |
| `data.fill_limit` | 5000 | per-market tape cap for σ |
| `data.prior_min_markets_per_cell` | 30 | (category × price) floor before trusting a σ prior |
| `data.prior_sample_per_category` | 2000 | stratified market sample for the prior corpus |
| `data.control_ratio` | 3 | matched non-disputed controls per disputed market (replay) |

### Loop knobs (dataclass-only defaults, env-overridable)
`quote_size=10.0` (outcome tokens/side) · `reduce_fraction=0.5` (inventory fraction taker-reduced on
exit) · `light_factor=0.3` (re-quote size multiplier while defensive).

## 2. `Config` dataclass + env overrides (`config/loader.py`)

`load_config(path=DEFAULT_PATH) -> Config`. Env variables that override yaml:
- `MODE` — `paper | paper-live | live` (validated; anything else raises). Default `paper`.
- `MAX_CAPITAL_USDC` — hard notional cap for any live order (default `0.0`).
- `POSITIONING` — overrides `positioning`.
- `LAMBDA_STAR` — overrides `lambda_star` (still guarded > 0.05).

## 3. `.env` variables (`.env.example`)

**Data layer:** `DATA_SOURCE` (`hf | graphql`), `HF_DATASET`, `HF_TOKEN`, `GRAPHQL_URL` / `DATABASE_URL`
(local Envio), `INDEXER_GRAPHQL_URL` (**optional/legacy** — see below), `POLYGON_RPC_URL` (the live
dispute plane; defaults to the keyless tenderly gateway), `AMOY_RPC_URL`.

> **`INDEXER_GRAPHQL_URL` is opt-in and unset by default**, in code *and* in every deploy config. The
> live feed goes straight to keyless RPC when it's absent. Setting it to a **stale** endpoint is worse
> than leaving it empty — `live.py` will probe it for reachability/freshness before falling back
> (`ENVIO_FRESH_MAX_S`, default 2 days). Do not re-introduce a baked-in default.
**Run mode:** `MODE`, `MAX_CAPITAL_USDC`, `POSITIONING`.
**Gated CLOB (live only, jurisdiction-gated):** py-sdk creds + `JURISDICTION_ACK`, `BUILDER_CODE`.
**On-chain testnet:** `MARKET_ADDRESS`, `AMOY_USDC_ADDRESS`, `AMOY_GAS_GWEI`, `ENGINE_COLLATERAL_USDC`,
`ENGINE_MAX_TRADE`, and `ENGINE_PRIVATE_KEY` / `ENGINE_ADDRESS` (written into `.env` by
`scripts/gen_engine_wallet.py`, never committed).
