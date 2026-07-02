# PolyLambda — Methodology & Findings

> The honest write-up: what the model is, how the public dataset became its historical backbone, what
> the primary edge proof actually showed, and where the limits are. Companion docs: [ANALYSIS.md](ANALYSIS.md)
> (ideation post-mortem), [DECISIONS.md](DECISIONS.md) (corrections of record), [DATASET.md](DATASET.md)
> (the dataset dossier + all reproducible numbers), [JURISDICTION.md](JURISDICTION.md) (the ToS gate).

## 1. The model (unchanged core, verified)

PolyLambda quotes a Polymarket binary market by modeling its implied probability `p` in log-odds
`X = ln(p/(1−p))` as a jump-diffusion `dX = μdt + σdW + J·dN`, and prices with Avellaneda–Stoikov +
a jump term. Three estimators feed one pricing core (all pure, unit-tested):

- **σ** ([estimators/sigma.py](estimators/sigma.py)) — logit-return robust EWMA + hierarchical
  shrinkage toward a (category × price-level) prior; wash filter first.
- **λ** ([estimators/lambda_engine.py](estimators/lambda_engine.py)) — dispute jump model emitting
  `λ_select` (market-selection) and `λ_jump` (directional jump premium + reward-aware exit), with a
  Wilson CI (disputes are ~1% → calibration-limited by design).
- **fair value** ([estimators/fair_value.py](estimators/fair_value.py)) — depth-weighted mid + tapered
  favorite-longshot tilt.

Pricing ([pricing/quote.py](pricing/quote.py)): A-S in logit space, directional jump skew on the
reservation price, boundary-safe sigmoid mapping, (T−t)→0 guard, inventory cap. Exit is **reward-aware**
([execution/loop.py](execution/loop.py) `should_exit`): flatten only when `E[jump loss] > forgone
rewards + spread`. The corrected thesis (DECISIONS.md #1): a dispute is a **directional jump with
degraded-but-present liquidity**, not a trading lock.

## 2. The data backbone (the enabling change)

Every historical-data consumer was a stub pointing at a local Envio indexer that would take days to
backfill 1.17B fills. The public **`moose-code/polymarket-onchain-v1`** dataset (2.74B records, indexed
with the same Envio indexer PolyLambda references) replaces that: the [data/](data/) package queries it
in place with DuckDB (`hf://…`, no download). See [DATASET.md](DATASET.md) for the full analysis. Key
verified facts: 1,172,658,611 fills spanning **2022–2026** (74% in 2026); 992,485 resolved conditions;
all columns VARCHAR/camelCase; the fill↔market join (`order_filled.assetId = market_data.id`) validated
30/30; `deriveFill` SQL parity-tested against the TypeScript indexer.

**The two-source split (DECISIONS.md #13).** HF supplies fills, resolutions, metadata, and category
denominators — but **not** OOv2 dispute events. Dispute labels come from
[data/disputes.py](data/disputes.py), which pulls OOv2 `DisputePrice` logs via a keyless RPC (no
Docker), derives `conditionId = keccak256(adapter ++ keccak256(ancillary) ++ 2)`, and joins to HF —
validated **723/723** for the V2 + Legacy adapters. The local Envio indexer is scoped down to only the
OOv2 dispute lifecycle (the one net-new piece), and remains **mandatory for NegRisk** (see §5).

## 3. The λ signal (real base rates)

Joining the 723 disputes to derived categories, the per-category dispute base rate is starkly ordered:

| Category | Rate | | Category | Rate |
|---|---:|---|---|---:|
| **politics** | **0.92%** | | tech-ai | 0.22% |
| geopolitics | 0.57% | | sports | 0.10% |
| economics | 0.39% | | **crypto** | **0.042%** |

**Politics is ~22× more dispute-prone than crypto.** This is exactly what `λ_select` captures — the
market-selection edge, now in data (numerators are V2/Legacy-only over all-adapter denominators →
lower bounds; the ordering is the signal).

## 4. The primary edge proof (historical replay-ablation)

The live λ-ablation is statistically powerless in weeks (~1% dispute rate), so the primary proof is a
historical counterfactual ([forwardtest/replay_ablation.py](forwardtest/replay_ablation.py)): over
indexed disputes + matched controls, replay arms **A** (diffusion-only, λ off), **B** (+λ_jump exit),
**C** (+λ_select filter), net of forgone rewards, across a λ*-grid, with a pre-registered power calc.

**Result (56 disputed + 223 control markets, 2022–2023, fill-tape counterfactual).** The λ signal is
the **category dispute base rate**, so `λ*` is scaled to that range (~0.0003–0.009). Corrected pnl_net /
sharpe across the grid:

| arm | λ*=0.0005 | λ*=0.005 | λ*=0.01 |
|---|---:|---:|---:|
| diffusion_only | 1408.6 / 0.167 | 1408.6 / 0.167 | 1408.6 / 0.167 |
| **lambda_jump** (surgical exit) | **1536.8 / 0.183** | 1502.9 / 0.179 | 1408.6 / 0.167 |
| lambda_select (blanket avoidance) | 620.6 / 0.112 | 1102.4 / 0.138 | 1408.6 / 0.167 |

The arms **converge to diffusion at λ*=0.01** (above every base rate → no exits → a clean sanity check),
and the λ*-sensitivity is real. **λ_jump beats diffusion by ~9% at low λ*** (avoided directional loss
> its cost); **λ_select is worst** — at λ*=0.0005 it forfeits ~977 of reward to avoid ~189 of loss.
**The edge is the surgical jump-exit, not blanket market-selection avoidance** (DECISIONS.md §A). This
result was corrected after an adversarial review found the first pass had a hardcoded
`proposal_detected=True` (which bypassed the λ* threshold) and filtered arm C on volatility instead of
the category rate. **This is a positive signal, not a proof:** see §5.

## 5. Honest limitations

1. **NegRisk gap (the big one).** 963 disputes — the 2024+ high-liquidity era — are not recoverable
   from external UMA/OO events. I tested **4 keccak derivations** (across 2 contracts × 2 event types:
   ancillary keccak, and `QuestionResolved` questionID × two oracle addresses × identity) — all **0%
   HF-join** (DATASET.md §5) — plus a `ConditionPreparation`-event angle, which fires at market
   *creation* not resolution (0 logs in the resolution-era range). NegRisk assigns questionIds via
   NegRiskIdLib and prepares conditions through its own path; recovering them needs the NegRiskAdapter's
   own events — i.e., the scoped local indexer. So the replay above covers the *thin* 2022–2023 era,
   not where most liquidity/disputes live.
2. **Statistical power.** ~1% dispute rate; the replay is small-N and underpowered — read it through the
   `power_calc`, report the CI, do not over-claim.
3. **No order book.** The replay uses the fill-tape mid (per scope); it tests whether the λ_jump *exit*
   saves more than it costs, an execution question — not a *predictive* edge.
4. **Jurisdiction.** Live trading is ToS-gated ([JURISDICTION.md](JURISDICTION.md)); the historical
   replay needs no live trading and is the always-valid headline.

## 6. Reproduce
See [DATASET.md](DATASET.md) §8. `pytest tests/` (36 green) covers deriveFill/deriveConditionId parity,
the data-layer contracts, and the pure cores; `python -m data.dossier` reproduces the numbers; the
dispute + replay pipeline runs end-to-end with `python -m data.disputes` → `materialize_slice` →
`python -m forwardtest.replay_ablation`.
