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

## 3. The other findings

Three more were critical — a dead SDK on the build path (`py-clob-client` archived; CLOB V2
launched Apr 2026), a live λ-ablation that is statistically powerless at a ~1% dispute rate, and
a jurisdiction gate that can zero out the live leg entirely. Alongside them sat a set of smaller
corrections: the Builders Program's actual shape, two income programs rather than one, an
open-source reference indexer worth adapting, several math gaps in the pricing map, and σ
measuring manipulation rather than belief on wash-traded markets.

**All thirteen, with sources and the corrected fact to build on, are the table in
[DECISIONS.md §C](DECISIONS.md).** That table is the record; this document does not duplicate it.

---

## 4. What survived the pass

The **A-S diffusion engine** and the log-odds approach — correct and defensible, with a finite
`(T−t)` horizon that is economically real here because markets have a true resolution date. The
**scope-lock** (binary-only, no custody, no heavy ML, no order-book reconstruction), which was
the single most schedule-protective decision made. The **paper → paper-live → live** discipline
and the **ablation-as-falsification** instinct — good science; the ablation only needed the
historical replay to have statistical power.

---

## 5. Honest bottom line

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
