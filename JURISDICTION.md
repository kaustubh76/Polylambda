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

## Why it matters

The MVP's most credible evidence is a **live (or tiny-capital) forward-test**, which is ToS-legal
only for a non-US / eligible operator (**option 1**, resolved below). The engineering value and the
**historical-replay edge proof stand regardless** of whether the live leg ever runs. The operating
entity must be confirmed non-US and recorded (log below) **before the first real order**.

---

## The decision — RESOLVED: option 1

**Option 1 — non-US / eligible operator** (resolution log below): full `paper → paper-live → live`
is open; the live forward-test runs tiny (`MAX_CAPITAL_USDC` small), sequenced by ROADMAP Phase 0.
The alternatives were option 2 (US person → paper/paper-live only, or the CFTC-registered Polymarket
US venue) and option 3 (paper-only default) — both superseded.

---

## Consequences baked in regardless

- The forward-test harness is **paper-live-first** (public no-auth book/price), so it works under any
  jurisdiction outcome; paper-live validates **logic/microstructure only** (no true queue position,
  fill probability, or rewards — never report simulated rewards as P&L).
- The **historical counterfactual replay** (1,848 released disputes) is the primary edge proof and
  needs **no live trading** — the headline survives even if the live leg stays off.

---

## Resolution log

| Date | Decision | By | Notes |
|---|---|---|---|
| 2026-07-11 | **Option 1 — non-US / eligible operator.** Live leg unblocked; sequenced by [ROADMAP.md](ROADMAP.md). | operator | Entity name/registration to be recorded here **before the first real order**. `JURISDICTION_ACK` legitimate only on the non-US operating host. Anti-manipulation rules (no wash/spoof) apply regardless. |
