# PolyLambda — Methodology & Findings

> The honest write-up: what the model is, how the public dataset became its historical backbone, what
> the primary edge proof actually showed, and where the limits are. Companion docs: [ANALYSIS.md](ANALYSIS.md)
> (ideation post-mortem), [DECISIONS.md](DECISIONS.md) (corrections of record), [DATASET.md](DATASET.md)
> (the dataset dossier + all reproducible numbers), [JURISDICTION.md](JURISDICTION.md) (the ToS gate).

## 1. The model

Stated in full in [REPORT.md §2](REPORT.md) — A-S in log-odds with a directional jump term and a
reward-aware exit gate. This document does not restate it; it covers what is *methodologically*
load-bearing and how to reproduce the numbers.

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
[data/disputes.py](data/disputes.py), which by **default** loads the git-tracked released dispute
layer (`dataset_release/polymarket-oov2-disputes-v1`: **1,848 disputes to chain head, all adapters,
100% HF-joinable**; the 1,794 inside the HF window feed the λ base rates — NegRisk joins via the
recovered tradeable conditionId, `data/negrisk_map.py`, see §5). The live dispute tail (and the release
regeneration) comes from a **keyless Polygon RPC** scan of OOv2 `DisputePrice` logs (no Docker);
`DATA_SOURCE=graphql` can instead source labels from a local **Envio indexer** (now optional/legacy —
the hosted deploy is retired) scoped to only the OOv2 dispute lifecycle. `load_disputes()` derives
`conditionId = keccak256(adapter ++ keccak256(ancillary) ++ 2)` — validated **723/723**, but it
covers the V2 + Legacy adapters only.

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
  `estimate_lambda` from the **real category base rates** above (+ Wilson CI, calibrated κ: since
  2026-07-16 **per-category** `kappa_by_category.json` with signed drift — `data/calibrate.py::
  calibrate_kappa_by_category`, shrunk toward the global mean for thin categories — falling back to
  the scalar `kappa_loss = 0.76`, the mean |realizedJumpLogit| over the released disputes) — the
  engine the diagram centers on, no longer a constant.
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
(`category_base_rate` + `market_size`); held-out AUC ≈ 0.70** (modest discrimination, market size adds
real signal). At ~1% prevalence this is calibration-limited; **the category base rate remains the
honest default**, with the hazard a directional overlay — not a validated edge.

**Does `proposer_reliability` add signal? (v2 fair-controls study — a NULL).** `proposer_reliability`
and `latency_anomaly` were zeroed in v1 because arbitrary HF controls had no proposer, so a nonzero
value was disputed-only → it trivially separated the classes (an AUC-0.95 leakage artifact). v2 fixes
the leakage by drawing controls from **proposed-but-not-disputed indexer markets** (which carry a real
proposer), so `proposer_reliability` is fair for both classes. But this exposed a **liquidity
confound**: disputed markets are systematically more liquid than controls, so `market_size` alone
separates them (a naïve fair-controls fit scores an inflated AUC 0.96 on `market_size`, not proposer).
Controlling it with a **market_size-matched case-control fit** (coarsened exact matching, 176 matched
pairs) collapses the discrimination to **held-out AUC ≈ 0.50–0.64 — at/near chance (it swings across
splits because n=176 pairs is small), below the size-only 0.70 — with `proposer_reliability`'s
coefficient ≈ 0 (even wrong-signed)**. **Verdict: proposer reputation adds no signal once liquidity is
matched — a clean null.** The apparent structural "edge" was liquidity all
along; the deployed model stays size-only. (`latency_anomaly` remains unbuildable — there is no
proposal timestamp in the parquet or the indexer schema; it needs a `proposedAt` field — v3. The
fair-controls loader + matcher live in `estimators/hazard.py` as the reusable evaluation harness.)

**Does the structural λ improve the exit? (replay head-to-head — POWERED: 1,409 disputed + 2,912
controls).** We injected the hazard λ as a 4th replay-ablation arm (`lambda_jump_hazard`, the *identical*
reward-aware surgical exit as arm B but driven by the per-market structural λ instead of the flat base
rate) and compared it to base-rate arm B on the same universe across the λ*-grid:

| λ* | arm B (base) pnl / Sharpe | arm B_hazard pnl / Sharpe | Δ Sharpe |
|---|---|---|---|
| 0.001 | 43092 / 0.309 | 43579 / 0.314 | +0.005 |
| **0.002** (frozen) | **38652 / 0.270** | **42408 / 0.302** | **+0.032** |
| 0.005 | 38652 / 0.270 | 37220 / 0.258 | −0.012 |
| 0.01 | 38230 / 0.267 | 36726 / 0.254 | −0.012 |

**At the frozen operating point λ*=0.002 the structural λ wins** (+3,756 pnl, +0.032 Sharpe): it avoids
**+4,121 more jump-loss for only ~+365 more forgone reward** by exiting the big, jump-prone markets its
`market_size` feature up-weights, while holding the small ones the flat base rate would also exit — it
recovers most of the Sharpe that raising λ* from 0.0005 (0.314, where both arms exit everything) to
0.002 otherwise costs. But the edge **holds only for λ* ≤ 0.002 and reverses at λ* ≥ 0.005** (the
prevalence-recalibrated hazard pushes fewer markets past the higher threshold → fewer exits → less
avoided loss). The pattern is **reproduced from a 362-market sample to the full 1,409 — powered, not a
small-sample artifact** — but it is a **threshold-sensitive exit-*timing* improvement, not a uniform
edge.** **Publish the whole curve; keep the base rate as the safe default.** The arm is purely additive
(base-rate arm B is byte-for-byte unchanged).

## 4. The primary edge proof (historical replay-ablation)

The live λ-ablation is statistically powerless in weeks (~1% dispute rate), so the primary proof is a
historical counterfactual ([forwardtest/replay_ablation.py](forwardtest/replay_ablation.py)): over
indexed disputes + matched controls, replay arms **A** (diffusion-only, λ off), **B** (+λ_jump exit),
**C** (+λ_select filter), net of forgone rewards, across a λ*-grid, with a pre-registered power calc.

**The numbers live in [REPORT.md §4](REPORT.md)** — pinned run, published run, and the full
4-arm × 5-λ\* grid, sourced from the committed artifact. They are not restated here.

**What is methodologically load-bearing is the *scale-invariance*.** The same ordering
(`lambda_jump > diffusion_only > lambda_select`) and the same λ\*-curve shape reproduce at every
scale the replay has been run at — a 56-market 2022–23 slice, a NegRisk-2024 liquid-era slice,
and the full 1,409-dispute universe. Absolute PnL is *not* comparable across those runs (it
scales with how many sampled controls have a joinable fill tape), so the ordering and the curve
shape are the claim; the level is not. Arms converge to diffusion at λ\*=0.01 — above every
category base rate, so no exits fire — which is the clean sanity check that the machinery is
wired correctly.

One correction of record worth keeping visible: the first pass of this replay was wrong. An
adversarial review found a hardcoded `proposal_detected=True` (bypassing the λ\* threshold
entirely) and arm C filtering on volatility instead of the category rate. The published results
post-date that fix.

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
See [DATASET.md](DATASET.md) §8. `pytest tests/` covers deriveFill/deriveConditionId
parity, the data-layer contracts, the indexer dispute source + recon buckets, the pure cores, the
paper forward-test engine, the wired sizing/inventory-cap, and the hazard model;
`python -m data.dossier` reproduces the numbers; the dispute + replay pipeline runs end-to-end with
`python -m data.disputes` → `materialize_slice` → `python -m forwardtest.replay_ablation`. With the
local indexer up: `python -m recon.check` (pass_rate + NegRisk `no_ground_truth` bucket) and
`python -m data.export_disputes` (the released `polymarket-oov2-disputes-v1` companion dataset).
