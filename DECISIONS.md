# PolyLambda — Decisions & Corrections of Record

> **Purpose:** lock in the facts so the false premises are never silently reintroduced.
> Every row in the corrections table replaces a claim in the original ideation. When in
> doubt, this file wins over the original `Readme.md`. See [ANALYSIS.md](ANALYSIS.md) for
> the narrative and [JURISDICTION.md](JURISDICTION.md) for the ToS gate.

---

## A. Strategy decision — build the engine to support BOTH positionings

The core "exit-before-lock" premise is false (see correction #1), which forks the project.
**Decision:** do **not** hard-pick a framing yet. Build the engine so it serves both, and let
the historical-replay ablation (net of forgone rewards) decide which edge actually exists.

λ emits **two consumable signals** from the same hazard model:

| Signal | Drives | Framing it serves |
|---|---|---|
| **λ_select** | market selection / sizing — avoid or underweight dispute-prone markets | reward-farmer |
| **λ_jump** | directional jump premium + **reward-aware** exit-on-risk (flatten only when `E[jump loss] > forgone rewards + spread`) | jump-avoidance |

Positioning is a switch in `config/model.yaml`, not a rewrite. The ablation reports
diffusion-only vs +λ_jump exit vs λ_select filter, and the final README/METHODOLOGY framing is
chosen from that result.

---

## B. Jurisdiction decision — documented, deferred

Per instruction, the Polymarket ToS / US-person constraint is **documented as an open
decision** that gates live mode rather than decided now. Full detail in
[JURISDICTION.md](JURISDICTION.md). Until resolved, assume **paper / paper-live only**.

---

## C. Corrections of record (false/stale → corrected fact)

| # | Original (wrong/stale) | Corrected fact to build on | Source |
|---|---|---|---|
| 1 | Disputes **lock** positions 4–6 days; "can't trade out"; un-hedgeable | **CLOB stays OPEN** during disputes; only **redemption/payout freezes**; exit liquidity degrades (~5c haircut). A dispute = **directional price jump + degraded-but-present liquidity** (hedgeable at a cost). | UMA disputes guide; startpolymarket.com/learn/how-markets-resolve |
| 2 | Single dispute → DVM → 4–6 days | **Two-strikes**: 1st dispute auto-resets on-chain (`_reset` updates `requestTimestamp`, fresh ~2h liveness, resolves ~2–4h); only the **2nd** dispute escalates to the DVM (commit/reveal 24h+24h = 48h base, 48–96h with scheduling). Time-to-resolution is **bimodal**. | UmaCtfAdapter source; docs.uma.xyz/protocol-overview/dvm-2.0 |
| 3 | `conditionId == questionId`; join via opaque `requestId` | `conditionId = keccak256(abi.encodePacked(adapter, questionId, 2))` (oracle = adapter address, outcomeSlotCount = 2 for binary). `questionId = keccak256(ancillaryData-with-initializer)`. **No opaque requestId** — a UMA request is the 4-tuple `(adapter, YES_OR_NO_IDENTIFIER, requestTimestamp, ancillaryData)`. | UmaCtfAdapter source |
| 4 | `py-clob-client` (CLOB V1) | **Archived & non-functional vs production.** Use `Polymarket/py-sdk` (official, **BETA — pin a version**) or `py-clob-client-v2` (stopgap, also deprecated). CLOB **V2** (Apr 28 2026): order struct dropped `feeRateBps/nonce/taker`, added **ms timestamp** → **remove all nonce logic**; POLY_BUILDER_* headers removed. | github.com/Polymarket/py-sdk; py-clob-client (archived) |
| 5 | Collateral = USDC.e | Migrated **USDC.e → pUSD** (1:1 USDC-backed). Wrap via **CollateralOnramp** before trading; rewards/rebates pay in **pUSD**. | docs.polymarket changelog |
| 6 | "makers free, no rebate" | Makers free **and earn**: **Maker Rebates** (~20–50% of taker fees by category) + standalone **Liquidity Rewards** (size × uptime × *quadratic* midpoint-proximity; two-sided boost; single-sided ~⅓ in [0.10,0.90], **zero outside it**; $1/day floor; per-market `max_spread`/`min_size`). Per-category **taker** fees exist since Mar 23 2026 (0% geopolitical → ~1.80% crypto, max near p=0.50). **Model both income lines.** | docs.polymarket/market-makers/maker-rebates; /liquidity-rewards |
| 7 | tick / min order size = global constants | **Per-market and dynamic** (tick ∈ {0.1, 0.01, 0.001, 0.0001}; tightens near extremes >0.96 / <0.04). Read `tick_size`/`min_order_size` at runtime; handle INVALID_TICK / INVALID_ORDER_MIN_SIZE. Sports markets auto-cancel limit orders at game start. | docs.polymarket/trading/orders/create |
| 8 | A-S spread used directly in price space | Add **logit→price Jacobian** `δ_p ≈ p(1−p)·δ_x`; near-boundary **spread floor**; **inventory cap** `|q_max| ~ 1/max(p(1−p), ε)`; **(T−t)→0 collapse guard** (spread collapses exactly when jump risk peaks); make jump term **directional** (skew the *reservation price*). Note notation: `k` = order-arrival/liquidity vs `κ` = jump-premium weight. | arXiv 2510.15205; arXiv 1105.3115; original A-S paper |
| 9 | naive EWMA σ | EWMA on wash prints measures **manipulation**, not belief (and spread ∝ σ² → over-wide quotes → under-fill on reward markets). Add a **trade-quality/wash filter** (drop self-crosses, sub-min-size prints), a robust/trimmed estimator, a volume floor; condition the shrinkage prior on **category AND price level** (logit σ is heteroskedastic in price space). | quant critique |
| 10 | reconciliation "= 100%" flat gate | **100% on the ELIGIBLE set** (settled + past confirmation depth + supported adapter), with **counted exclusion buckets** (pending / in-dispute / reorg-window / unsupported-adapter) reported as first-class metrics. Configure Envio confirmed-block depth / reorg handling for chain 137. | engineering critique |
| 11 | live λ-ablation = the edge proof | **Underpowered** in 18 days (~0–3 disputes witnessed, ≈0 DVM hard-locks). Make **historical counterfactual replay over the ~184 indexed disputes + matched controls** the *primary* edge proof; keep the live ablation as a **pre-registered, explicitly-underpowered** sanity check. Pre-register the power calc. | quant/edge critique |
| 12 | "Builders Program submission / deadline / judging" | **Continuous & permissionless** (Builder Codes, bytes32 via CLOB V2 SDK). **No deadline / rubric / demo-day.** Weekly USDC rewards (Sun–Sat UTC epochs since Nov 2 2025); grants are **traction-gated** (working product + active users). The "$100–$75K" range is the older Microgrants, not Builders grants. **Bots welcomed**; jurisdiction is the binding constraint. | builders.polymarket.com; docs.polymarket/developers/builders |
| 13 | "OrderFilled tape needs dynamic-contract indexing" | **Over-engineering.** Token IDs / outcome tokens are uint256 ERC-1155 `positionId`s = **event params on fixed addresses** → normal handlers + id-keyed entities. `contractRegister` is address-based, reserved for genuine factory deployments (FPMM pools), which binary-only v1 likely doesn't need. | Envio docs; enviodev/polymarket-indexer |

---

## D. Verified contract addresses (Polygon, chain 137)

> Re-confirm on Polygonscan before any live use. Multiple adapter versions are live — pick the
> correct one per market type or the conditionId/questionId join silently drops markets.

| Component | Address |
|---|---|
| OptimisticOracleV2 (OOv2) | `0xeE3Afe347D5C74317041E2618C49534dAf887c24` |
| UMA CTF Adapter **V2** | `0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74` |
| Neg Risk UMA CTF Adapter | `0x2F5e3684cb1F318ec51b00Edba38d79Ac2c0aA9d` |
| Legacy UMA CTF Adapter | `0x71392E133063CC0D16F40E1F9B60227404Bc03f7` |
| ConditionalTokens (CTF) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| USDC.e (collateral, contract layer) | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |
| pUSD CollateralOnramp | `0x93070a847efEf7F70739046A929D47a521F5B8ee` |

**Resolution-risk caveats (price these, don't assume a clean oracle read):**
- DVM is a token-weighted vote and has been attacked (Mar 2025: ~25% voting power forced a
  ~$7M market to "Yes").
- Jun 2026: suspected private-key compromise of an internal top-up wallet touching the adapter
  on Polygon — verify the adapter address you target is the current/uncompromised one.

---

## E. References to reuse (do not greenfield)

- **`github.com/enviodev/polymarket-indexer`** — near-exact reference (Exchange / NegRiskExchange
  / ConditionalTokens / NegRiskAdapter as normal handlers; ~4B events / 6 days). *Gap: no
  generic OOv2 — that's the net-new piece.*
- **`Polymarket/py-sdk`** (pin a version) — CLOB V2 auth (L1 EIP-712 derive + L2 HMAC),
  orders (GTC/GTD, post-only), batch order/cancel, `streams/` WebSocket.
- **arXiv 2510.15205** (logit jump-diffusion A-S for prediction markets), **arXiv 1105.3115**
  (GLFT perpetual horizon), **original A-S paper** (Cornell-hosted PDF).
