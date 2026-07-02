# PolyLambda  ·  `poly_lambda`

**A belief-volatility market-making bot for Polymarket that treats disputes as jumps — and exits before they lock your capital.**

PolyLambda indexes the Polymarket → UMA resolution lifecycle and CLOB fills with **Envio HyperSync/HyperIndex**, estimates a market's belief-volatility (**σ**), its dispute jump-intensity (**λ**), and its fair value, then quotes via an **Avellaneda–Stoikov** model augmented with a jump-risk premium. When a resolution proposal lands or λ spikes, it **flattens and pulls liquidity before the dispute lock** — the single behavior that separates it from naive LP farming.

> **Status:** research / forward-test. This is not financial advice. It places real orders only in explicit live mode, and is designed to be run with *tiny* capital (or paper-live) until the edge is validated. Custody/vault is intentionally **out of scope** for v1. See [Safety](#safety--disclaimers).

> ## ⚠️ CORRECTION NOTICE — read this before trusting the thesis below
>
> A June 2026 verification pass found that **one load-bearing premise of the thesis is
> factually false**, plus three other critical corrections. The text below is **preserved as
> the original ideation**, annotated inline with `⚠ CORRECTION` callouts. For the full story:
> - **[ANALYSIS.md](ANALYSIS.md)** — how the ideation converged + the findings + honest bottom line.
> - **[DECISIONS.md](DECISIONS.md)** — the corrections table, verified contract addresses, the engine decision.
> - **[JURISDICTION.md](JURISDICTION.md)** — the Polymarket ToS / US-person constraint (gates live mode).
>
> **The big one:** disputes do **NOT** lock your position. The CLOB stays **open** during a
> dispute; only **redemption/payout** freezes and exit liquidity degrades (~5c haircut). A
> dispute is a *directional price jump with degraded-but-present liquidity* — hedgeable at a
> cost — not an un-hedgeable lock. Everywhere this README says "lock / can't trade out", read
> "redemption frozen + degraded exit liquidity + directional jump".
>
> **Engine decision:** build λ to emit **λ_select** (market-selection filter, reward-farmer
> framing) *and* **λ_jump** (directional jump premium + reward-aware exit, jump-avoidance
> framing); the historical-replay ablation decides which edge is real.

---

## TL;DR

- **What:** an automated market maker for Polymarket binary markets.
- **Edge:** it *prices* adverse selection and *avoids* the dispute-lock — instead of farming rewards blindly and getting frozen for 4–6 days when a market disputes.
- **Why it's defensible:** the resolution-risk engine (dispute/lock/capture model) is the **jump-intensity term λ** in the pricing model, not a bolt-on filter. That fusion is the moat.
- **How it's validated:** live/paper-live forward-testing + a **λ-ablation** (model with the jump term ON vs OFF) that proves the term earns its keep — or honestly doesn't.

> **⚠ CORRECTION (edge + validation):** "frozen for 4–6 days when a market disputes" — only
> *redemption* freezes; trading continues (degraded). And the **live** λ-ablation is
> statistically powerless in 18 days (~0–3 disputes expected, ≈0 DVM hard-locks). The
> **primary** edge proof is a **historical counterfactual replay over the ~184 indexed
> historical disputes + matched controls**; the live ablation is a pre-registered,
> explicitly-underpowered sanity check. See [DECISIONS.md](DECISIONS.md) #1, #11.

---

## The thesis (read this first)

A market maker's resting orders are free options written to the market. The enemy is **adverse selection** — informed flow picking off the stale side on news, and above all on **resolution**.

On Polymarket the resolution event is special: when a market enters dispute, **positions lock for ~4–6 days**. You can't trade out. So a dispute isn't just a big price jump — it's an **un-hedgeable jump**: the gap arrives *and* removes your steering wheel at the same instant.

> **⚠ CORRECTION (the thesis-breaking fact):** This paragraph is the false premise. Disputes
> do **not** lock positions and you **can** trade out (at a ~5c haircut into thinner liquidity).
> Only redemption/payout freezes. Also, the dispute flow is **two-strikes**: the *first*
> dispute auto-resets on-chain (~2–4h, fresh ~2h liveness); only the *second* escalates to the
> DVM (the 4–6 day event), so the costly event is **bimodal and far rarer** than implied.
> The corrected jump is **directional** (resolves toward 0 or 1) and **hedgeable at a cost**.

That reframes the whole problem:
1. You must flatten inventory **before** a likely jump (you can't manage through it).
2. Your spread must carry an explicit **jump-risk premium**.
3. The thing that estimates jump probability and cost **is your resolution-risk engine** → it becomes the model's **λ**.

> **⚠ CORRECTION (reframing):** (1) becomes "*reduce/skew* inventory while exit liquidity still
> exists, pricing the haircut — and only when `E[jump loss] > forgone rewards + spread`", because
> pulling liquidity forfeits the dominant income line (rewards require uptime / two-sided depth).
> (2) The jump premium should be **directional** (skew the reservation price), not a symmetric
> widening. (3) holds — but λ is also a slow **market-selection** filter (λ_select).

PolyLambda is the implementation of that idea.

---

## How it works (the model)

Model a market's implied probability `p ∈ (0,1)` in **log-odds** space, `X = ln(p/(1−p))`, as a jump-diffusion:

```
dX = μ·dt  +  σ·dW  +  J·dN
     drift    diffusion   jumps (Poisson intensity λ)
```

Three estimators feed one pricing core:

| Estimator | What | Method |
|---|---|---|
| **σ** (`estimators/sigma.py`) | belief-volatility | logit-return EWMA + hierarchical shrinkage toward a category prior (robust on thin/wash markets) |
| **λ** (`estimators/lambda_engine.py`) | dispute jump-intensity + jump cost | calibrated hazard/logistic model on *structural* signals (proposer reliability, category base rate, market size, latency, ambiguity, voter concentration) — **not** rule-text |
| **fair value** (`estimators/fair_value.py`) | model mid | depth-weighted book mid + light favorite-longshot tilt at long horizons (no lookahead) |

> **⚠ CORRECTION (estimators):** **σ** — naive EWMA on thin/wash markets measures
> *manipulation*, not belief; add a trade-quality/wash filter, a robust/trimmed estimator, and
> condition the shrinkage prior on category **AND** price level. **λ** — disputes are ~1% of
> markets (184/18,427), so the full six-signal calibrated model is **calibration-limited**;
> scope λ-v1 to category base-rate + a few *point-in-time-safe* features (drop subjective
> "ambiguity"; **exclude** post-dispute "voter concentration" → lookahead leakage) and report
> λ **with a confidence interval**. Emit **λ_select + λ_jump**. See [DECISIONS.md](DECISIONS.md) #9.

**Pricing** (`pricing/quote.py`), Avellaneda–Stoikov + jump augmentation:

```
reservation price:  r = s − q·γ·σ²·(T−t)                 # inventory skew
diffusion spread:   δ = γ·σ²·(T−t) + (2/γ)·ln(1+γ/k)     # inventory-risk + liquidity
jump premium:       δ_total = δ + κ·λ·E[loss | jump]      # vanishes when λ low
quotes:             bid = r − δ_total/2 ,  ask = r + δ_total/2
```

> **⚠ CORRECTION (pricing — formulas are right, application needs more):** The A-S formulas are
> transcribed **exactly** correctly (verified vs the original paper; the log term is `2/γ`).
> Missing pieces to add: compute `r`/`δ` in **logit space**, then map the half-spread to price
> via the **Jacobian** `δ_p ≈ p(1−p)·δ_x`; add a near-boundary **spread floor** and an
> **inventory cap** `|q_max| ~ 1/max(p(1−p), ε)`; guard the **(T−t)→0 spread collapse** (spread
> collapses exactly when jump risk peaks); make `κ·λ·E[loss|jump]` **directional** (skew `r`),
> since the symmetric form just worsens both quotes. Note `k` (liquidity) ≠ `κ` (jump weight).

> There is **no clean closed form** for jump-diffusion market-making with forced locks — the jump handling is a principled heuristic on top of the rigorous diffusion base, validated empirically, not a theorem.

**Exit-on-risk** (`execution/loop.py`) — the defining behavior:

```
if proposal_detected(market) or λ(market,t) > λ*:
    cancel resting orders
    flatten inventory → 0 BEFORE the challenge window
    do not re-quote until resolved
```

> **⚠ CORRECTION (exit-on-risk must be reward-aware):** Pulling liquidity earns **zero** reward
> score during exactly the windows you exit (Liquidity Rewards score uptime + two-sided depth;
> single-sided is ~⅓ in [0.10,0.90] and **zero** outside it). So exit only when
> `E[jump loss] > forgone rewards + spread`, and the ablation **must net reward loss against
> avoided adverse selection** — otherwise λ-ON looks free when it is actually costly. Source the
> time-critical proposal signal from a **low-latency log subscription** (not the batch indexer),
> with a reorg-confirmation guard before any costly flatten.

---

## Architecture

```
Polygon ── Envio HyperSync/HyperIndex ──► Postgres / GraphQL
  (UMA OOv2 · CTF Adapter · CTF · CTF Exchange : lifecycle + OrderFilled)
        │  fast on-chain data
        ▼
ESTIMATORS (Python):  σ · λ(+jump cost) · fair value
        │
        ▼
PRICING:  Avellaneda–Stoikov + jump premium + inventory skew  →  bid / ask
        │
        ▼
EXECUTION LOOP (CLOB API):  quote · cancel · manage inventory · EXIT-ON-RISK
        │
        ▼
FORWARD-TEST:  live / paper-live  ·  P&L + inventory + rewards logged
        │
        └─► λ-ABLATION  =  edge proof
        ┄┄► [DEFERRED, not in v1] vault custody
```

> **⚠ CORRECTION (indexer):** Architecture is correct. Two notes: a **near-exact open-source
> reference** exists — `enviodev/polymarket-indexer` (~4B events/6 days) — adapt it rather than
> greenfield; but it covers **UmaSportsOracle, not generic OOv2**, so OOv2 proposal/dispute
> decode is the **one net-new, safety-critical** indexing piece. Index OrderFilled/
> TokenRegistered/ConditionPreparation as **normal handlers on fixed addresses** (id-keyed
> entities) — *not* dynamic-contract registration. See [DECISIONS.md](DECISIONS.md) #13.

---

## Repo structure

```
poly_lambda/
├── data/                     # historical backbone: DuckDB over the HF dataset (see DATASET.md)
│   ├── hf.py                 # connection + DATA_SOURCE switch + verified column registry
│   ├── fills.py              # order_filled → sigma tape (deriveFill in SQL)
│   ├── conditions.py         # payout vectors (recon ground truth)
│   ├── metadata.py base_rates.py cache.py prior_corpus.py dossier.py
├── indexer/                  # Envio HyperIndex (TS) — SCOPED to the OOv2 dispute lifecycle only
│   ├── config.yaml           # OOv2 + adapter (fills now come from the HF dataset)
│   ├── schema.graphql        # Market, ResolutionRequest, Dispute
│   └── src/EventHandlers.ts
├── recon/                    # reconciliation invariant (indexed == on-chain payout)
├── estimators/
│   ├── sigma.py
│   ├── lambda_engine.py
│   └── fair_value.py
├── pricing/
│   └── quote.py              # A-S + jump augmentation + inventory skew
├── execution/
│   ├── clob.py               # CLOB API wrapper (read book, place/cancel)
│   └── loop.py               # quoting loop + exit-on-risk
├── forwardtest/
│   ├── runner.py             # live / paper-live harness + P&L logging
│   └── ablation.py           # λ ON vs OFF edge test
├── notes/                    # dayNN-*.md learning artifacts
├── METHODOLOGY.md            # full model write-up + honest limitations + ablation result
├── VISION.md                 # standing spec: scope-lock + non-negotiables (re-read each session)
├── LEDGER.md                 # daily build tracking
└── README.md
```

> **⚠ CORRECTION (add files):** add `forwardtest/replay_ablation.py` (the **primary** historical
> edge proof), `config/model.yaml` (γ, κ, λ\*, EWMA β, shrinkage, **positioning switch**), and
> the docs `ANALYSIS.md` / `DECISIONS.md` / `JURISDICTION.md` (this correction set).

> **✅ UPDATE (data backbone — see [DATASET.md](DATASET.md)):** the historical fill tape +
> resolutions now come from the public HF dataset `moose-code/polymarket-onchain-v1` (1.17B fills,
> queried in place via DuckDB in `data/`), so the local indexer is **scoped down to the OOv2 dispute
> lifecycle only** — the one thing HF lacks. This unblocks σ / recon / λ base-rates / the
> replay-ablation without a multi-day local backfill. Dispute **labels** still require running the
> scoped indexer (`indexer/`).

---

## Tech stack

- **Indexing:** Envio HyperIndex (TypeScript) → Postgres + GraphQL
- **Quant / bot:** Python 3.11+ — `numpy`, `pandas`, `scikit-learn` (σ, λ), `py-clob-client` (CLOB API)
- **Tooling:** `pnpm` (Envio), `uv`/`pip` (Python), `pytest`

> **⚠ CORRECTION (dead SDK):** `py-clob-client` is **archived and non-functional against
> production** (CLOB **V2** launched Apr 28 2026). Use **`Polymarket/py-sdk`** (official,
> **BETA — pin a version**). Remove all nonce logic (V2 uses a **ms timestamp**); add a **pUSD**
> collateral on-ramp (wrap USDC.e via CollateralOnramp); integrate **Builder Codes** (bytes32)
> from day one. See [DECISIONS.md](DECISIONS.md) #4–#7.

---

## Prerequisites

- Node.js 20+ and `pnpm`
- Python 3.11+
- A Polygon RPC endpoint (for verification; Envio HyperSync handles bulk indexing)
- A Polymarket account + API credentials (only needed for live/paper-live execution)
- Docker (Envio local Postgres) — or a Postgres instance

---

## Setup

```bash
# 1. clone
git clone https://github.com/<you>/poly_lambda.git
cd poly_lambda

# 2. indexer
cd indexer
pnpm install
# fill in verified contract addresses + start block in config.yaml  (see note below)
pnpm envio dev          # starts the indexer + local Postgres + GraphQL

# 3. python env (from repo root, new shell)
cd ..
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 4. config
cp .env.example .env
# fill in: DB connection, RPC url, (later) CLOB credentials
```

> **⚠ Verify contract addresses yourself.** Do **not** trust any address from memory or docs — confirm the UMA CTF Adapter, Optimistic Oracle V2, Conditional Tokens, and CTF Exchange addresses on Polygonscan, and put them in `indexer/config.yaml`. The `questionId ↔ conditionId ↔ requestId` join depends on getting this exactly right.

> **⚠ CORRECTION (the join):** the addresses have been verified — see the table in
> [DECISIONS.md](DECISIONS.md#d-verified-contract-addresses-polygon-chain-137) (re-confirm
> before live). And the join is **not** `questionId ↔ conditionId ↔ requestId`: use
> `conditionId = keccak256(abi.encodePacked(adapter, questionId, 2))`; there is **no opaque
> requestId** — a UMA request is the 4-tuple `(adapter, YES_OR_NO_IDENTIFIER, requestTimestamp,
> ancillaryData)`. Pick the correct adapter (V2 vs NegRisk vs legacy) per market or markets
> silently drop from the join.

---

## Configuration

`.env` (minimum):

```
DATABASE_URL=postgres://...
POLYGON_RPC_URL=https://...
# execution (only for live/paper-live)
CLOB_API_KEY=
CLOB_API_SECRET=
CLOB_API_PASSPHRASE=
MODE=paper            # paper | paper-live | live
MAX_CAPITAL_USDC=50   # hard cap — keep tiny until edge is validated
```

Model parameters live in `config/model.yaml` (γ risk-aversion, κ jump-premium weight, λ* exit threshold, EWMA β, shrinkage strength). Start conservative; tune in Phase 3.

> **⚠ CORRECTION (params + jurisdiction):** **Freeze** γ, κ, λ\*, EWMA β, shrinkage from the
> historical replay and **forbid in-test tuning** (tuning 5 knobs on ~0–3 live events is fitting
> noise; publish λ\* **sensitivity curves** instead). And **before any real order**, resolve the
> ToS/jurisdiction gate in [JURISDICTION.md](JURISDICTION.md) — US persons (and their bots) are
> barred from trading via UI **and** API. Default to **paper / paper-live** until resolved.

---

## Running it

```bash
# index + verify
pnpm --filter indexer envio dev          # index the lifecycle + fills
python -m recon.check                    # reconciliation: indexed outcome == on-chain payout (must be 100%)

# estimators (sanity)
python -m estimators.sigma --market <conditionId>
python -m estimators.lambda_engine --train          # fit + calibrate the dispute model
python -m estimators.fair_value --market <conditionId>

# the bot (paper mode first — simulated fills, no real orders)
python -m forwardtest.runner --mode paper

# forward-test live/paper-live (real book; real orders only in live with MAX_CAPITAL_USDC)
python -m forwardtest.runner --mode paper-live

# the edge proof
python -m forwardtest.ablation          # λ-term ON vs OFF → risk-adjusted P&L delta
```

**Always run `paper` → `paper-live` → `live`, in that order.** Never start in `live`.

> **⚠ CORRECTION (recon + edge proof):** reconciliation cannot be a flat "must be 100%" — it
> will be permanently red or silently gamed by async/bimodal resolution, reorgs, and
> multi-adapter joins. Redefine it as **100% on the ELIGIBLE set** (settled + past confirmation
> depth + supported adapter) with **counted exclusion buckets**. And the **primary** edge proof
> is `forwardtest/replay_ablation.py` (historical counterfactual over ~184 disputes, **net of
> forgone rewards**); `forwardtest/ablation.py` (live) is a labeled-underpowered sanity check.

---

## Data model (Week-1 entities)

```graphql
type Market            { id(=conditionId) questionId ancillaryData endDate status finalOutcome }
type ResolutionRequest { id(=requestId) market proposer bond proposalTs round status }
type Dispute           { id request disputer disputeTs round }
type Fill              { id market price size side timestamp }   # from OrderFilled
```

`Market 1:1* ResolutionRequest 1:N Dispute` (one Request per reset round — don't overwrite).
**Reconciliation invariant:** every `RESOLVED` market's `finalOutcome` must equal the on-chain CTF payout. Pass rate must be 100%.

> **⚠ CORRECTION (keying):** there is **no `requestId`** — key `ResolutionRequest` by
> `(conditionId, requestTimestamp)` (or a hash of the 4-tuple) so each **reset round** is a
> distinct row; detect the auto-reset from the adapter `priceDisputed` callback. Encode
> time-to-resolution as **bimodal** (~2–4h happy path vs 4–6 days escalated). Reconciliation =
> 100% on the **eligible** set (see correction above).

---

## Scope (v1)

**IN:** Envio indexer (lifecycle + fills) · reconciliation · σ/λ/fair-value estimators · A-S + jump pricing · execution loop · exit-on-risk · forward-test + P&L · λ-ablation.

**OUT (do not let creep in):** ❌ custody/vault contract ❌ ML beyond logistic/hazard for λ ❌ multi-platform ❌ depositor UI ❌ historical order-book reconstruction (forward-test instead) ❌ categorical/multi-outcome markets (binary only).

> **✅ KEEP (scope-lock is correct):** this OUT list is the single most schedule-protective
> decision in the plan — keep it **verbatim**. (Add: the *primary* edge proof is a historical
> *dispute* replay, which is **not** order-book reconstruction and is explicitly in scope.)

---

## Roadmap (18-day MVP)

- **Days 1–7 — learning-heavy:** own the lifecycle, Envio, CLOB API, Avellaneda–Stoikov, σ estimation, λ hazard model, synthesis. Light validation code.
- **Days 8–14 — code-heavy:** full indexer + reconciliation, production σ/λ, pricing engine, execution loop + exit-on-risk, forward-test harness, λ-ablation.
- **Days 15–18 — finalize:** forward-test runs + tuning + fixes, submission package (demo + `METHODOLOGY.md` + metrics), submit.

**Hard gates:** model understood (D7) → bot runs live/paper + ablation number (D14) → submitted MVP (D18).

> **⚠ CORRECTION (roadmap deltas):** sequence by **data dependency** (σ + fair_value first; λ
> last, gated on labeled disputes); carve out **OOv2 decode** (D2–D3) and a **CLOB V2 SDK spike**
> (week 1); add a **D7→D8 thesis re-derivation** half-day; **stand up the paper-live logging loop
> by ~D9** so tape accrues; add **2 buffer days**; **freeze params at D16** and reserve D17–D18
> strictly for packaging. The D18 gate is a **decision branch** ("if λ-ON ≈ λ-OFF net of
> rewards → it's a generic A-S reward farmer; finalize that framing"), not "submitted MVP".
> The Builders Program is **continuous & permissionless** (no deadline/judging) — see
> [DECISIONS.md](DECISIONS.md) #12.

---

## Safety & disclaimers

- **Not financial advice.** This is research software for market-making study and a grant MVP.
- **Model risk is the dominant risk.** A miscalibrated σ or λ produces *systematically* bad quotes and *loses real money* in live mode. Treat forward-testing as validation, not earning. Start at `MAX_CAPITAL_USDC` ≈ tiny.
- **Order of operations:** `paper` → `paper-live` → `live`. Never skip.
- **Un-hedgeable jumps can't be fully avoided** — exit-on-risk reduces lock exposure, it does not eliminate it.
- **Credentials & logs:** never commit `.env`; sanitize logs in long-running runs; re-audit API permission scope regularly.
- **Custody is out of scope** for v1 — the bot trades its own configured capital, it does not hold third-party funds.

> **⚠ CORRECTION (safety):** "un-hedgeable jumps" → jumps are **hedgeable at a cost** (the
> position is tradable; redemption is what freezes). Add: **jurisdiction (ToS)** is a binding,
> possibly-disqualifying gate for live mode ([JURISDICTION.md](JURISDICTION.md)); and resolution
> is **not** a risk-free oracle read — the UMA DVM has been governance-attacked (Mar 2025) and a
> suspected adapter key-compromise was investigated (Jun 2026). Price these on contentious markets.

---

## License

MIT (or your choice) — add a `LICENSE` file.

---

*Built for the Polymarket Builders Program. The engine is the moat: dispute-risk as jump-intensity, deepest quant placed where it's actually executable.*

> **⚠ FINAL NOTE:** the engine *is* a legitimate moat — but only once re-grounded in the
> corrected reality (no trading lock; rewards as the dominant income; λ as both a selection
> filter and a directional jump premium). The original thesis above is preserved for the record;
> the corrections supersede it. Start at **[ANALYSIS.md](ANALYSIS.md)**.
