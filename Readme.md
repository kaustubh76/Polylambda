# PolyLambda  ·  `poly_lambda`

**Disputes are jumps, not locks, priced into the spread.**

A dispute is a directional price jump, not a lock: the engine folds that jump intensity (λ) into
pricing and pulls liquidity only when E[jump loss] > forgone rewards.

A market maker's resting orders are free options written to the market, and the worst adverse
selection arrives at resolution. PolyLambda models that resolution risk as the jump term of a
jump-diffusion and prices it, rather than screening dispute-prone markets out. The historical
replay says that distinction is the whole edge: surgical exits earn, blanket avoidance does not
([REPORT.md §4](REPORT.md)).

> **Status:** research / forward-test. Not financial advice. Real orders only in explicit live
> mode, with tiny capital, until the edge is validated. Custody/vault is out of scope for v1.

> **Live demo:** **<https://polylambda.vercel.app>** — the research dashboard plus a continuous
> on-chain testnet engine: the production loop drives engine-signed PolyLambdaMarket contracts on
> Polygon Amoy (chainId 80002), quoting from the live estimators, every fill a decoded `Traded`
> event. Testnet only; the mainnet CLOB write path stays jurisdiction-gated
> ([JURISDICTION.md](JURISDICTION.md)).

> **Where things are written down.** Each concept is stated in full in exactly one place and
> linked from everywhere else — the map is [DECISIONS.md §F](DECISIONS.md). For what the project
> believed before its June 2026 verification pass and why it changed, see
> [DECISIONS.md §C](DECISIONS.md) (16 corrections of record) and [ANALYSIS.md](ANALYSIS.md).

---

## How it works

Model the implied probability `p` in log-odds `X = ln(p/(1−p))` as a jump-diffusion — the
transform keeps quotes from ever crossing 0 or 1:

```
dX = μ·dt  +  σ·dW  +  J·dN
     drift    diffusion   jumps (Poisson, intensity λ)
```

Three estimators feed one pricing core. Detail: [notes/04-model-pricing.md](notes/04-model-pricing.md).

| Estimator | What it measures | Method |
|---|---|---|
| **σ** ([estimators/sigma.py](estimators/sigma.py)) | belief-volatility → spread width | wash-filtered logit-return EWMA + shrinkage toward a (category × price-bucket) prior |
| **λ** ([estimators/lambda_engine.py](estimators/lambda_engine.py)) | dispute jump intensity + cost | category base rates with Wilson CIs + a structural hazard model on point-in-time-safe features |
| **fair value** ([estimators/fair_value.py](estimators/fair_value.py)) | model mid | depth-weighted book mid (never last trade — wash-prone) + a tapered favorite–longshot tilt |

λ emits two signals from one model: **λ_select** (slow, market selection and sizing) and
**λ_jump** (the directional premium and the exit trigger).

**Pricing** ([pricing/quote.py](pricing/quote.py)) — Avellaneda–Stoikov, computed in logit space
and mapped back through the sigmoid:

```
reservation:  r = x_mid − q·γ·σ²·(T−t)        inventory skew
jump skew:    r += κ·λ·jump_drift             jumps are directional → lean the center
diffusion:    δ = γ·σ²·(T−t) + (2/γ)·ln(1+γ/k)
jump premium: δ += κ·λ·E[loss|jump]           vanishes as λ → 0
quotes:       bid = sigmoid(r − δ/2),  ask = sigmoid(r + δ/2)
```

Guards: a `(T−t)→0` floor (the spread must not collapse exactly when jump risk peaks), a
near-boundary spread floor, and a time-decaying position cap. The λ *pricing* terms are
second-order by construction — [REPORT.md §2.2](REPORT.md) quantifies this honestly; the realized
edge comes from the exit gate below.

**Exit-on-risk** ([execution/loop.py](execution/loop.py) `should_exit`) — the defining behavior,
and the canon home for its pseudocode. Pulling quotes forfeits Liquidity Rewards, so the gate
prices that cost before it fires: it trims only when the expected jump loss beats the rewards and
haircut given up. On exit it reduces 50% at the touch and re-quotes lighter — never goes dark.

---

## Architecture

```
moose-code HF dataset (history) ─┐
keyless Polygon RPC (live tail) ─┤──► data/  (DuckDB + dispute labels)
        │
        ▼
ESTIMATORS:  σ · λ (+jump cost) · fair value
        ▼
PRICING:  A-S + directional jump premium + inventory skew  →  bid / ask
        ▼
EXECUTION LOOP:  quote · cancel · manage inventory · reward-aware EXIT-ON-RISK
        ▼
FORWARD-TEST:  paper / paper-live / testnet  ·  P&L + inventory + rewards logged
        └─► REPLAY-ABLATION  =  the edge proof
```

The historical fill tape and resolutions come from the public HF dataset
`moose-code/polymarket-onchain-v1`, queried in place via DuckDB. Dispute labels ship in-repo as
the released `dataset_release/polymarket-oov2-disputes-v1` parquet — the one thing HF lacks. The
scoped Envio indexer is optional/legacy: needed only to refresh the release or for
`DATA_SOURCE=graphql`. Detail: [DATASET.md](DATASET.md),
[notes/03-data-backbone.md](notes/03-data-backbone.md).

## Repo structure

```
data/          history backbone: DuckDB over the HF dataset + dispute labels
estimators/    sigma.py · lambda_engine.py · hazard.py · fair_value.py
pricing/       quote.py — A-S + jump augmentation
execution/     clob.py · loop.py (exit-on-risk) · risk.py · proposal_feed.py · testnet_*
forwardtest/   runner.py (paper harness) · replay_ablation.py · replay_full.py
recon/         reconciliation invariant (indexed outcome == on-chain payout)
webapp/        FastAPI backend + React dashboard (backend/constants.py = prose of record)
indexer/       Envio HyperIndex — optional/legacy, OOv2 dispute lifecycle only
notes/         developer reference (start at notes/README.md)
config/        model.yaml — the frozen parameter set
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # DB connection, RPC url, (later) CLOB credentials
```

Model parameters live in [config/model.yaml](config/model.yaml) (γ, κ, λ\*, EWMA β, shrinkage).
They are **frozen** — tuning five knobs on a handful of live dispute events fits noise; publish λ\*
sensitivity curves instead. Every knob is documented in
[notes/07-config-reference.md](notes/07-config-reference.md).

Verified contract addresses are in [DECISIONS.md §D](DECISIONS.md) — re-confirm on Polygonscan
before any live use, and pick the correct adapter (V2 / NegRisk / legacy) per market or the
`conditionId` join silently drops markets.

## Running it

```bash
python -m recon.check                          # reconciliation gate
python -m estimators.lambda_engine --train     # fit + calibrate the dispute model
python -m forwardtest.runner --mode paper      # the bot: simulated fills, no real orders
python -m forwardtest.replay_ablation          # the primary edge proof
```

**Always `paper` → `paper-live` → `live`, in that order.** Never start in `live`. Every entry
point is listed in [notes/08-entrypoints-runbook.md](notes/08-entrypoints-runbook.md).

---

## Scope (v1)

**IN:** dispute-lifecycle data backbone · reconciliation · σ/λ/fair-value estimators · A-S + jump
pricing · execution loop · reward-aware exit-on-risk · forward-test + P&L · replay-ablation.

**OUT (do not let creep in):** ❌ custody/vault contract ❌ ML beyond logistic/hazard for λ
❌ multi-platform ❌ depositor UI ❌ historical order-book reconstruction ❌ categorical /
multi-outcome markets (binary only).

This OUT list is the single most schedule-protective decision in the project — keep it verbatim.
The historical *dispute* replay is not order-book reconstruction and is explicitly in scope.

## Safety & disclaimers

- **Not financial advice.** Research software for market-making study and a grant MVP.
- **Model risk dominates.** A miscalibrated σ or λ produces systematically bad quotes and loses
  real money in live mode. Forward-testing is validation, not earning.
- **Jumps are hedgeable at a cost, not avoidable.** Exit-on-risk reduces exposure into a ~5c
  haircut; it does not eliminate the loss.
- **Resolution is not a risk-free oracle read.** The UMA DVM has been governance-attacked
  (Mar 2025) and an adapter key-compromise was investigated (Jun 2026) — price these on
  contentious markets ([DECISIONS.md §D](DECISIONS.md)).
- **Jurisdiction: RESOLVED (non-US operator).** Polymarket's ToS bars US persons and their bots
  from trading via UI/API; the live leg runs under a non-US entity ([JURISDICTION.md](JURISDICTION.md)).
- **Credentials:** never commit `.env`; sanitize logs; re-audit API scope regularly.

## License

MIT — add a `LICENSE` file.

---

*Built for the Polymarket Builders Program. The engine is the moat: dispute risk as jump
intensity, with the deepest quant placed where it is actually executable.*
