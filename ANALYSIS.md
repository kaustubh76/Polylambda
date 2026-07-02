# PolyLambda — Ideation Analysis & Verification Findings

> **What this is:** an honest post-mortem of the PolyLambda ideation (the 18-day MVP ship
> plan) against verified, current (June 2026) reality. The diffusion engine and the core
> idea are sound — but one load-bearing premise is factually false, and three other
> findings are critical. This document explains how the idea converged and what changed.
>
> Companion docs: [DECISIONS.md](DECISIONS.md) (corrections table + addresses),
> [JURISDICTION.md](JURISDICTION.md) (the ToS gate). Verification was done by a
> multi-agent web-research + 4-lens critique sweep; sources are cited inline in DECISIONS.md.

---

## 1. How the ideation converged

The concept reached its final form through a clean logical chain:

1. **Start** — the obvious Polymarket play: rest two-sided limit orders, farm the Liquidity
   Rewards pool.
2. **Problem** — a resting order is a free option written to the market; you get picked off
   by informed flow, worst of all at **resolution**.
3. **Insight #1** — resolution on Polymarket isn't ordinary news; it's a *structural* event
   tied to the UMA dispute machinery, so it should be **modeled**, not just filtered out.
4. **Insight #2 (the unifying move)** — don't bolt a "dispute filter" onto pricing; make
   dispute risk the **jump-intensity λ** in a jump-diffusion of the log-odds price, so the
   resolution-risk engine *is* a native term in an Avellaneda–Stoikov spread. → "the engine
   is the moat."

This convergence is **intellectually sound**. Avellaneda–Stoikov in log-odds space with a
jump premium is a published, current approach (arXiv 2510.15205; GLFT arXiv 1105.3115), and
the A-S formulas in the README are transcribed **exactly** right. The diffusion engine is
legitimate quant, not theater. The problem is purely in the *premises* the edge was built on.

---

## 2. The thesis-breaking finding

> **The plan's claim that disputes "lock positions for 4–6 days — you can't trade out" is
> VERIFIED FALSE.**

During a dispute the **CLOB stays open** — you can keep buying and selling. What freezes is
**redemption/payout**, and exit liquidity degrades (wider spreads, ~5c haircut to exit). So a
dispute is a **directional price jump with degraded-but-present liquidity** — *hedgeable at a
cost* — **not** an un-hedgeable lock.

This single fact poisons the whole downstream chain: the "un-hedgeable jump" framing, the λ\*
flatten trigger, `E[loss | jump]`, the exit-on-risk loop, and the entire P&L cost model all
inherit the defect. **It is the #1 thing to fix, before any pricing code.**

Second structural correction: the dispute flow is **two-strikes** — the *first* dispute
auto-resets on-chain (~2–4h, fresh ~2h liveness); only the *second* escalates to the UMA DVM
(the 4–6 day event). Time-to-resolution is **bimodal**, and the costly event is far rarer than
the README implies.

---

## 3. The three other critical findings

| Finding | Status | Why it matters |
|---|---|---|
| **`py-clob-client` is archived & dead** | build-breaking | CLOB **V2** launched Apr 28 2026. The pinned SDK cannot place an order against production. Must use `Polymarket/py-sdk` (official but **BETA**), drop all nonce logic (V2 uses ms timestamps), add a **pUSD** collateral on-ramp, and integrate **Builder Codes**. |
| **The λ-ablation is statistically powerless in 18 days** | edge-proof-breaking | Disputes are ~1% of markets (184 / 18,427); DVM escalations ~1.5% of those. A solo MM cycling 30–60 markets expects **~0–3 disputes, ≈0 hard-locks** in 18 days — you cannot compute a Sharpe on n≈1. The original "live λ-ablation = edge proof" is wall-clock- and sample-impossible. Fix: make a **historical counterfactual replay over the ~184 indexed disputes** the primary edge proof. |
| **Jurisdiction can zero out the live leg** | existential | Polymarket ToS bars **US persons (and their bots) from trading via UI AND API**. If the operator is US-based, the entire live forward-test is prohibited. See [JURISDICTION.md](JURISDICTION.md). |

---

## 4. Smaller but valuable corrections

- **Builders Program is continuous & permissionless** (Builder Codes) — no deadline, no
  judging panel. Grants are **traction-gated** (working product + active users), so near-term
  income is the **automatic rails**: weekly USDC builder rewards + Maker Rebates + Liquidity
  Rewards. **Bots are explicitly welcomed** — jurisdiction is the only binding constraint.
- **Two income programs, not one:** Maker Rebates (~20–50% of taker fees, by category) *and*
  standalone Liquidity Rewards (size × uptime × *quadratic* midpoint-proximity; single-sided
  penalized ~⅓ in [0.10,0.90], **zero outside it**). Model both.
- **A near-exact open-source reference indexer exists** — `enviodev/polymarket-indexer`
  (~4B events / 6 days on Polygon). Biggest available time-saver; adapt rather than greenfield.
  *Gap:* it covers UmaSportsOracle, **not** generic OOv2 — that's the one net-new indexing piece.
- **Math gaps (not errors):** add the logit→price Jacobian `δ_p ≈ p(1−p)·δ_x`, a near-boundary
  spread floor, an inventory cap, a `(T−t)→0` spread-collapse guard, and make the jump term
  **directional** (skew the reservation price, not just widen a symmetric spread).
- **σ on thin/wash markets** measures manipulation, not belief → add a trade-quality filter and
  condition the shrinkage prior on category **and** price level.

Full detail and sources: [DECISIONS.md](DECISIONS.md).

---

## 5. What's solid (keep it)

- The **A-S diffusion engine** and the log-odds approach — correct and defensible; the
  finite `(T−t)` horizon is **economically real** here (markets have a true resolution date).
- The **scope-lock** (binary-only, no custody, no heavy ML, no historical book reconstruction)
  — the single most schedule-protective decision. Keep verbatim.
- The **paper → paper-live → live** discipline and the **ablation-as-falsification** instinct —
  good science; the ablation just needs the historical replay to have statistical power.
- **Envio multi-contract on Polygon** — correct architecture, fully confirmed.

---

## 6. Top risks (ranked)

1. **Edge built on a false mechanic** (no trading lock) → re-derive the thesis first.
2. **λ-ablation powerless live** → historical replay becomes the primary edge proof.
3. **Dead SDK on the critical path** → CLOB V2 migration spike in week 1.
4. **OOv2 indexing is the one unproven, safety-critical path** (reference repo only covers
   UmaSportsOracle).
5. **Jurisdiction** can kill the live leg ([JURISDICTION.md](JURISDICTION.md)).
6. **Exit-on-risk forfeits the dominant income line** (rewards require uptime / two-sided
   depth) → exit must be **reward-aware**: flatten only when `E[jump loss] > forgone rewards + spread`.
7. **100% reconciliation is infeasible as a flat gate** → "100% on the *eligible* set" with
   counted exclusion buckets.

---

## 7. Honest bottom line

In **18 days solo (~90h)** PolyLambda **can** ship: a working Envio indexer (adapted from the
reference) + eligible-set reconciliation + robust σ/fair-value + a base-rate λ-v1 (with
intervals) + a corrected logit-space A-S core + a paper/paper-live bot + a **historical-replay**
λ-ablation. It **cannot** ship the original headline — a statistically significant *live*
λ-ablation over real dispute events — because that is wall-clock- and sample-bound.

The MVP is achievable **once re-baselined to the honest deliverable**; as literally written
(live edge proof, `py-clob-client`, positions-lock thesis, flat-100% recon, λ as a calibrated
6-feature model) it is not.

**Single highest-leverage fix:** correct the false load-bearing fact (no trading lock during
disputes). It is the root that poisons the thesis, the λ interpretation, `E[loss|jump]`, λ\*,
the exit loop, and the cost model. Fix it before a line of pricing code — and the corrected
reality (frozen redemption + degraded liquidity + *directional* jump; rewards as the dominant
income; exiting forfeits reward score) reshapes λ from a fast exit trigger into a slow
market-selection filter, which is both defensible and buildable in the time available.
