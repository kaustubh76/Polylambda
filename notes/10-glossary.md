# 10 · Glossary

> Plain-English vocabulary for the model, stats, pricing, execution, and data layers. Folds the
> `quant-implementation-full.excalidraw` K + M glossaries into text. Deeper detail:
> [04-model-pricing.md](04-model-pricing.md), [03-data-backbone.md](03-data-backbone.md).

## Model & math

- **p** — the market's "% chance"; the price of a YES share ≈ probability.
- **log-odds `X = ln(p/(1−p))`** — a stretched ruler for probability: maps `0..1 → −∞..+∞` so quotes
  can't pass 0/1.
- **jump-diffusion** `dX = μ dt + σ dW + J dN` — belief moves smoothly (diffusion) *and* jumps
  (resolution/dispute/shock, a Poisson process of intensity λ).
- **σ (belief-vol)** — how fast belief drifts on news; drives spread **width**.
- **λ (jump intensity)** — how likely a dispute/resolution jump is soon; the "engine".
- **λ\*** (`lambda_star`) — the exit threshold; when `lambda_jump > λ*` (and the reward-aware gate
  passes) the engine reduces inventory.
- **jump_drift** — the *direction* of the expected jump (toward 0 or 1) — used to skew quotes.
- **E[loss|jump] / `e_loss`** — expected adverse move if the jump lands, incl. a ~5c exit haircut.

## Estimator & stats

- **EWMA** — recency-weighted running average of squared moves; knob `b` = memory (high = smooth/slow).
- **hazard / logistic** — a model giving `P(dispute soon)`; the S-curve classifier.
- **shrinkage / prior** — with too little data, lean on the category average (a new restaurant judged by
  its cuisine); James-Stein weight `n/(n+strength)`.
- **structural signals** — facts about the market (proposer, size, category), NOT the rule wording.
- **calibration** — are your %s honest? "30%" should happen ~30% of the time (a good weather forecaster);
  measured by Brier score.
- **Wilson CI** — a confidence interval for a rate that behaves well at small counts / near 0.
- **wash trading** — fake self-trades (maker == taker) that move price & pollute σ; filtered out.
- **no lookahead** — never use future info at time `t` (grading without the later answer key). "Voter
  concentration" is excluded because it is only known *after* a dispute.

## Pricing / Avellaneda-Stoikov

- **Avellaneda-Stoikov** — the classic bid/ask formula given inventory, vol, and time (a quote
  thermostat).
- **s** — mid price (current fair middle) — here the depth-weighted book mid.
- **r (reservation price)** — the inventory-adjusted center your quotes sit around: `r = s − qγσ²(T−t)`.
- **q** — signed inventory (positive = long).
- **d (diffusion spread)** — the A-S spread from vol + liquidity: `γσ²(T−t) + (2/γ)ln(1+γ/k)`.
- **jump premium** — extra spread `κ·λ·E[loss]` charged for jump risk; vanishes when λ is low.
- **k** — order-arrival / liquidity knob. **κ (kappa)** — jump-premium weight (a *different* knob).
- **Jacobian `p(1−p)`** — the factor that converts a log-odds half-spread back into price space.
- **favorite-longshot tilt** — favorites are slightly underpriced, longshots overpriced; a light fix,
  tapered near resolution.

## Execution, validation & on-chain

- **exit-on-risk** — on a proposal / λ-spike, cancel & reduce inventory before danger — now
  **reward-aware** (only if `E[loss] > forgone rewards + spread`).
- **adverse selection** — getting picked off: your stale resting order trades right before bad news.
- **CLOB** — Central Limit Order Book = the exchange's resting buy/sell list.
- **maker / taker** — maker posts a resting order (earns rewards, no fee); taker crosses to fill now
  (pays fee). **post-only** = an order that refuses to be a taker.
- **liquidity rewards / rebates** — daily payout for tight two-sided quotes; here a *simulated score*,
  never folded into P&L.
- **ablation** — turn a part OFF to measure its worth (A/B-test the λ term).
- **arms A/B/C** — diffusion-only / surgical λ-jump exit / blanket λ-select avoidance.
- **UMA OOv2 / DVM** — the oracle that decides outcomes; the DVM is the token-holder vote settling
  disputes.
- **conditionId / questionId** — the on-chain IDs joining a Polymarket market to its UMA question; the
  join key of the whole data backbone.
- **NegRisk** — multi-outcome markets whose tradeable conditionId differs from the UMA one; recovered via
  `data/negrisk_map.py`.
- **reconciliation (recon)** — the hard gate: indexed `finalOutcome` must equal the on-chain HF payout.
- **engine wallet** — the backend key that acts as the on-chain market maker on Amoy (posts quotes,
  flags disputes, resolves).
- **pUSD / Builder Codes** — Polymarket collateral wrapper + a bytes32 reward-attribution tag (mainnet
  execution path).
