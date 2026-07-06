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
OOv2 dispute lifecycle (the one net-new piece); NegRisk disputes join HF via the recovered tradeable
conditionId (`data/negrisk_map.py`, see §5), so all adapters are covered.

## 3. The λ signal (real base rates — ALL adapters, regenerated 2026-07-05)

Joining all **1,527 unique disputed markets** (V2 + NegRisk + Legacy + other, from the 1,794 release
disputes) to derived categories, the per-category dispute base rate is starkly ordered:

| Category | Rate | | Category | Rate |
|---|---:|---|---|---:|
| **entertainment** | **2.11%** | | tech-ai | 0.52% |
| **politics** | **1.83%** | | sports | 0.17% |
| economics | 1.28% | | **crypto** | **0.085%** |
| geopolitics | 0.91% | | | |

**Politics is ~22× more dispute-prone than crypto** (1.83% vs 0.085%) — and the NegRisk-era numerators
reveal **entertainment as the most dispute-prone category** (2.11%; it looked near-safe at 0.11% on the
V2/Legacy-only numerators, n=3 vs n=59 now — culture/award markets with ambiguous resolution criteria).
This is exactly what `λ_select` captures: the market-selection edge, now on the full adapter set with
Wilson CIs (DATASET.md §5b).

### 3b. The engine, wired into the runtime (2026-07-06)

Earlier the estimators existed but the runnable loop bypassed them (a hardcoded λ constant, a static
σ prior). They are now fully integrated so the forward-test exercises the real brain:

- **λ into the loop.** `forwardtest.runner.build_markets` (source=`data`) resolves each market's
  `estimate_lambda` from the **real category base rates** above (+ Wilson CI, calibrated `kappa_loss`
  = 0.76, the mean |realizedJumpLogit| over the released disputes, `data/calibrate.py`) — the engine
  the diagram centers on, no longer a constant.
- **σ prior into the loop.** the hierarchical (category × price-bucket) prior
  (`data.prior_corpus` → `estimators.sigma.category_price_prior`) replaces the static 0.15; the loop
  also honors the frozen `shrinkage_strength`.
- **Panel-F execution.** quote **size ∝ 1/risk** (shrinks with σ and λ) and a **hard
  time-to-resolution inventory cap** (the allowed |position| ramps to 0 at resolution, so near the
  buzzer inventory can only be reduced) — both driven by frozen `config/model.yaml` knobs.

**The structural hazard model (`estimators/hazard.py`), honestly evaluated.** The diagram's Panel-D
λ method is a hazard/logistic on structural signals. We built it: class-weighted logistic on
point-in-time-safe features, prior-corrected back to the ~1% natural prevalence so its output is a
usable `λ_jump` (not the ~0.5 a balanced fit emits). The honest finding matches DECISIONS.md #9 —
**v1 rests on the features fairly computable for both disputed and control markets
(`category_base_rate` + `market_size`); held-out AUC ≈ 0.68** (modest discrimination, market size adds
real signal). `proposer_reliability` and `latency_anomaly` are retained in the schema but **zeroed in
v1**: they cannot be computed for arbitrary controls without label leakage (the indexer's
`ResolutionRequest` doesn't cover most HF-resolved controls). At ~1% prevalence this is
calibration-limited; **the category base rate remains the honest default**, with the hazard a
directional overlay — not a validated edge.

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

**Liquid-era confirmation (2026-07-05, NegRisk 2024 slice, 26 disputed + 132 controls).** With the
NegRisk map unblocking the fill join, the same ablation on the *liquid* NegRisk era reproduces the
ordering: at λ*=0.0005, **λ_jump 1888.7 / 0.375 > diffusion 1882.2 / 0.373 > λ_select 0.0** (λ_select
forfeits ~1895 reward to avoid ~13 loss), converging at λ*=0.01 (|λ_jump − diffusion| = 1.2). Small-N
and surgical, but the conclusion — surgical exit > avoidance — now holds on real 2024 NegRisk fills, not
only the thin V2 era.

## 5. Honest limitations

1. **NegRisk gap — RESOLVED (2026-07-05), not a limitation.** The 2024+ high-liquidity disputes are
   NegRisk, and a prior version of this doc called them "structurally absent from HF" (V2 100% / NegRisk
   0% join). That was **wrong** — an artifact of joining on the indexer's *phantom* conditionId (a
   `deriveConditionId(0x2f5e…)` fallback that exists nowhere on-chain). NegRisk markets **trade** under a
   conditionId whose oracle is the NegRiskAdapter `0xd91E80cF…`, recovered from the NegRiskOperator's
   `QuestionPrepared` event (`data/negrisk_map.py`: 132,004 questions mapped, **100% present in HF**).
   With the map, **every adapter joins HF 100%** — V2 723/723, **NegRisk 943/943 (was 0/350)** — and the
   powered liquid-era replay runs on real fills (§4). Recon's `finalOutcome` check stays **pass_rate 1.0
   on the eligible V2/Legacy set**; NegRisk stays in the `no_ground_truth` bucket only because the
   indexer keys its `finalOutcome` by the phantom cid (not an HF gap — the join itself is 100%). Root
   cause of the earlier error: tenderly `eth_getLogs` silently returns empty for >1M-block ranges, so
   "0 found" was really "range too wide". The remaining honest caveats are #2–#4 below (power, no order
   book, jurisdiction), not a data gap.
2. **Statistical power.** ~1% dispute rate; the replay is small-N and underpowered — read it through the
   `power_calc`, report the CI, do not over-claim.
3. **No order book.** The replay uses the fill-tape mid (per scope); it tests whether the λ_jump *exit*
   saves more than it costs, an execution question — not a *predictive* edge.
4. **Jurisdiction.** Live trading is ToS-gated ([JURISDICTION.md](JURISDICTION.md)); the historical
   replay needs no live trading and is the always-valid headline.

## 6. Reproduce
See [DATASET.md](DATASET.md) §8. `pytest tests/` (**101 green**) covers deriveFill/deriveConditionId
parity, the data-layer contracts, the indexer dispute source + recon buckets, the pure cores, the
paper forward-test engine, the wired sizing/inventory-cap, and the hazard model;
`python -m data.dossier` reproduces the numbers; the dispute + replay pipeline runs end-to-end with
`python -m data.disputes` → `materialize_slice` → `python -m forwardtest.replay_ablation`. With the
local indexer up: `python -m recon.check` (pass_rate + NegRisk `no_ground_truth` bucket) and
`python -m data.export_disputes` (the released `polymarket-oov2-disputes-v1` companion dataset).
