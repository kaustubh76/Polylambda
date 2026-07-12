# PolyLambda — Jurisdiction & ToS Constraint (RESOLVED — option 1)

> **Status: RESOLVED 2026-07-11 — option 1 (non-US / eligible operator).** The live leg will be
> operated by a **non-US entity** (operator to record entity details in the resolution log below
> before the first real order). The full `paper → paper-live → live` path is open, sequenced by
> [ROADMAP.md](ROADMAP.md). Until ROADMAP Phase 0's exit gate passes, the code default remains
> **paper / paper-live** (the `_require_live_gate` stays intact; `JURISDICTION_ACK` may only be
> set by the non-US operator on the operating host).

---

## The constraint

Polymarket's Terms of Service prohibit **US persons** (and persons in certain other restricted
jurisdictions) from trading on the main (non-US) venue — **via the UI AND via the API**,
**including agents/bots** developed by persons in restricted jurisdictions. (Stated in
Polymarket's own — now archived — `agents` repo and ToS.)

Separately, the ToS bars scraping/reproducing site content without written permission.

> **Important:** automation itself is **not** the problem — bots and market-making are
> explicitly welcomed (Polymarket publishes AMM guides, runs Maker Rebates + Liquidity Rewards,
> and shipped an official agents framework). **Jurisdiction is the binding constraint**, plus
> anti-manipulation rules (no wash trading / spoofing).

---

## Why it's existential for this project

The MVP's most credible evidence is a **live (or tiny-capital) forward-test**. If the operator
is a US person:

- **Live / real-order trading on the main venue is ToS-prohibited** — the live leg cannot
  legally run, and the edge proof can never exceed paper.
- This does **not** reduce the engineering value, but it **changes what "forward-test" can
  mean** and the project's positioning.

A US-typical email is on file for this session, which is a flag to clarify the *operating*
jurisdiction/entity before relying on the live path.

---

## The options (pick before any real order is placed)

1. **Non-US / eligible operator** → full `paper → paper-live → live` path is open; run a
   tiny-capital live forward-test (`MAX_CAPITAL_USDC` tiny).
2. **US person** → do **not** trade the main venue via UI/API. Either:
   - scope the entire effort to **paper / paper-live** and reposition as a research / tooling
     MVP (still demonstrates the model, indexer, estimators, and a historical-replay ablation), or
   - target the **CFTC-registered Polymarket US** venue and its Market Maker Program instead.
3. **Paper-only regardless** (safe default) → `paper` + `paper-live` only; defer the live
   decision entirely. **This is the assumed default until this file is updated.**

---

## Consequences baked into the plan regardless of choice

- The forward-test harness is built **paper-live-first** (public, no-auth WebSocket book +
  REST book/price), so it works under any jurisdiction outcome.
- Paper-live is **logic / microstructure validation only** — it cannot observe true queue
  position, fill probability, or realized rewards/rebates; those require real resting orders.
  Never report simulated rewards as P&L.
- The **historical counterfactual replay** (over the ~184 indexed disputes) is the primary
  edge proof and needs **no live trading at all** — so the headline result survives even if the
  live leg is permanently off.

---

## Resolution log

| Date | Decision | By | Notes |
|---|---|---|---|
| 2026-07-11 | **Option 1 — non-US / eligible operator.** Live leg unblocked; sequenced by [ROADMAP.md](ROADMAP.md). | operator | Entity name/registration to be recorded here **before the first real order**. `JURISDICTION_ACK` legitimate only on the non-US operating host. Anti-manipulation rules (no wash/spoof) apply regardless. |
