# PolyLambda — Business Plan (Polymarket Builders Program)

> **Status:** planned 2026-07-11. Companion to [ROADMAP.md](ROADMAP.md) (the technical
> sequencing). Facts below are cross-checked against [DECISIONS.md](DECISIONS.md) (which wins
> over the original Readme) and against builders.polymarket.com / docs.polymarket.com as of
> July 2026. Estimates are labeled as estimates.

---

## 1. Positioning

**"The dispute-aware market maker."** PolyLambda is (to our knowledge) the only Polymarket MM
that prices UMA resolution risk as a **native jump term** in its quoting model, rather than as a
bolted-on filter — and the historical replay over the 1,794 in-window disputes shows the surgical λ_jump exit
beats both naive diffusion quoting and blanket dispute-avoidance.

- **The engine is the moat:** σ / λ / fair-value estimators → Avellaneda–Stoikov in log-odds with
  a directional jump premium → reward-aware exit-on-risk.
- **The credibility artifacts:** the released public dispute dataset
  (`polymarket-oov2-disputes-v1`, 1,848 disputes to chain head, 100% HF-joinable, CC-BY-4.0), the hazard model
  card with its honest fair-controls null, and a methodology that documents its own corrected
  premises.
- **The public face:** the quant-terminal dashboard (already deployed) — extended with the live
  P&L / attribution panel (ROADMAP Phase 5).
- **The volume generator:** the live bot itself, operated by the non-US entity
  (see [JURISDICTION.md](JURISDICTION.md) resolution log).

## 2. Program facts we are building against

Verified per DECISIONS.md #12 + program docs:

- The Builders Program is **continuous and permissionless** — no deadline, no rubric, no demo
  day. Bots are explicitly welcomed; jurisdiction was the binding constraint (now resolved).
- **Builder Codes**: a bytes32 code attached to every order; it travels with the order, is
  validated by the CLOB, and appears in the `builder` field of each `OrderFilled` event.
  **Only matched orders earn** — resting orders that never fill pay nothing.
- Three program revenue layers:
  1. **Builder fees** we set (≤100bps taker / 50bps maker), settling to the builder-profile wallet;
  2. **Weekly USDC rewards pool** split by share of total attributed volume (epochs
     Sun 00:00 – Sat 23:59 UTC, live since 2025-11-02). Third-party back-calculations (PolyTrack)
     put the pool at **~0.5–1% of attributed volume** — an estimate, not a program guarantee;
  3. **Grants** — a **$2.5M fund, traction-gated**: working product + active users. (The older
     "$100–$75K" range was Microgrants, not this.)
- Venue-native income, independent of the program:
  - **Liquidity Rewards**: size × uptime × *quadratic* midpoint proximity; two-sided boost;
    single-sided credited ~⅓ inside [0.10, 0.90] and **zero outside it**; $1/day floor;
    per-market `max_spread` / `min_size`.
  - **Maker Rebates**: ~20–50% of taker fees by category (per-category taker fees since
    2026-03-23: 0% geopolitical → ~1.80% crypto, max near p=0.50).
- **Collateral is pUSD** (USDC.e migrated): wrap via CollateralOnramp
  (`0x93070a847efEf7F70739046A929D47a521F5B8ee`, re-verify per DECISIONS.md §D);
  rewards/rebates pay in pUSD.

## 3. Revenue model — four stacked lines, one live bot

| Line | Mechanism | Driver | When it starts |
|---|---|---|---|
| Liquidity Rewards | two-sided quoting near midpoint; the exit gate already nets forgone rewards before pulling liquidity | size × uptime × proximity² | tiny canary (ROADMAP Phase 6.2) |
| Maker Rebates | share of taker fees on our filled maker volume | fill volume × category fee | tiny canary |
| Builder Code weekly pool | our own volume attributed to our own code | share of total attributed volume (~0.5–1% est.) | first attributed fill |
| Grant | traction-gated application | live volume + product + public dataset | ROADMAP Phase 7 |

The engine's core interaction is already modeled: **exit-on-risk forfeits liquidity rewards during
exactly the windows it pulls quotes**, so the exit fires only when
`E[jump loss] > forgone rewards + spread`. The replay ablation nets this — that is the edge.

**Capital plan:** $20–50 canary → $200–1k scaled canary → grow only while the daily-loss and
portfolio caps hold and attributed volume trends up (gates in ROADMAP Phase 6). We acknowledge
the **anti-scale property** (reward normalization erodes APY as size grows — excalidraw Panel I);
custody/vault/depositor products stay **out of scope** exactly as the scope-lock says.

## 4. Traction plan (what the grant application will show)

The grant fund gates on *working product + active users*. Our traction story has three prongs:

1. **Live, verifiable volume** — weeks of uptime; attributed volume on-chain (`OrderFilled.builder`);
   markets quoted; disputes survived with exit-on-risk fired; net P&L vs the λ-OFF counterfactual
   arm. All of it visible on the public dashboard, none of it simulated (every simulated figure in
   the product is stamped `simulated: true` — live figures will be stamped `simulated: false`).
2. **Public goods already shipped** — the dispute dataset (the missing dispute layer for the
   ecosystem, CC-BY-4.0), the hazard model card, the reproducible dossier
   (`python -m data.dossier`), 141 green tests.
3. **The narrative** — *"we built the missing dispute-risk layer for Polymarket and we run it
   live."* Product + public good + volume, which is precisely the axis the program says it funds
   ("product innovation and traction").

Submission package = ROADMAP Phase 7's four legs, linked from one README.

## 5. Cost structure

Deliberately thin:

- **Infra:** one small VM/container for the bot + dashboard (Fly/Render configs already in repo),
  keyless Polygon RPC (the live dispute feed; no paid indexer), plus optional RPC access — low tens of $/month.
- **Trading capital:** the canary ladder above; risk-capped by `MAX_CAPITAL_USDC`, the persisted
  ledger, and max-loss/day (ROADMAP Phase 3).
- **Key custody:** dedicated low-balance operating wallet, funded just-in-time — the bankroll
  never sits behind one hot env var.
- **No headcount assumptions** — the roadmap is executable by the existing builder.

## 6. Risks (business-level)

| Risk | Stance |
|---|---|
| DVM manipulation (Mar 2025: ~25% voting power forced a ~$7M market) | This is *the* risk the engine prices — λ_jump exists because of it. Not assumed away. |
| Adapter/oracle contract risk (Jun 2026 suspected key compromise touching the adapter) | Re-verify all §D addresses on Polygonscan before live use (ROADMAP Phase 0). |
| Beta SDK (`polymarket-client==0.1.0b13`, pinned) surface drift | One-adapter-class fix by design; verified in Phase 0 before funds move. |
| Per-market dynamic tick/min-size; sports auto-cancel at game start | Read `tick_size`/`min_order_size` at runtime; handle INVALID_TICK / INVALID_ORDER_MIN_SIZE (DECISIONS.md #7). |
| Reward pool dilution / anti-scale | Treat the 0.5–1% pool rate as an estimate; size capital to realized capture, not projections. |
| Program terms change (fees, pool, grant criteria) | The bot's venue-native income (rewards + rebates) survives program changes; builder-code revenue is upside, not the base case. |
| Jurisdiction | Resolved: live leg operated by the non-US entity, recorded in JURISDICTION.md. No US person operates or trades. Anti-manipulation rules (no wash/spoof) apply regardless and are already enforced in the estimators (wash filter) and ops discipline. |

## 7. Milestones (business view)

| Milestone | Definition of done | Roadmap dep |
|---|---|---|
| M1 — Attribution proven | one filled order carries our builder code on-chain | Phase 0 |
| M2 — Live loop trusted | 1-hour live run reconciles exactly; zero orphans | Phase 2 |
| M3 — Unattended-safe | fault-injection suite green (kill-switch, daily-loss, breaker) | Phase 3 |
| M4 — First revenue week | one full Sun–Sat epoch with attributed volume + reward accrual visible on the dashboard | Phases 5–6 |
| M5 — Grant filed | submission README + application sent, backed by ≥2 weeks of live metrics | Phase 7 |

**Definition of done for this milestone (per operator decision): a grant-ready polished
product** — deployed, generating live attributed volume, with the application assembled and filed.
