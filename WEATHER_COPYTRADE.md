# WEATHER_COPYTRADE.md — Smart-Money Weather Copytrade Signal (design)

> **Status:** design / documentation only. **No code exists yet** for anything below — this
> is the spec a contributor would implement. It deliberately reuses PolyLambda's existing
> **data plane**, not its pricing engine (see §1).
>
> **Companion:** [polycool_info.md](polycool_info.md) — the analyst brief on the top
> Polymarket weather traders this signal is seeded from. Read it first; every caveat there
> applies here.

---

## 0. TL;DR

Turn the [polycool_info.md](polycool_info.md) weather-trader leaderboard into a **verified,
ranked smart-money feed**: (1) resolve the named handles to on-chain wallets, (2)
reconstruct their real weather-market PnL from the HuggingFace fill tape to **verify or
falsify** the marketing numbers, (3) detect the *floor-buy / NO-grind fingerprint*
independently so new smart-money wallets surface without being on the list, and (4) rank
and expose them in the dashboard. It reuses `data/` (HF DuckDB over `order_filled`),
`data/metadata.py` (market categorization), `data/conditions.py` (payouts), and the webapp
scaffolding. It does **not** touch the λ/A–S engine. Research/paper only, under the same
[JURISDICTION.md](JURISDICTION.md) gate as the rest of the project — **no live copy
execution in v1.**

---

## 1. Purpose & non-goals

**Purpose.** A defensible product feature: *verified* smart-money tracking on Polymarket
weather markets. The value over the raw Polycool list is that PolyLambda **checks the claims
on-chain** and **generalizes the pattern** into a detector — matching the repo's
"don't trust, verify" ethos ([ANALYSIS.md](ANALYSIS.md), the [Readme.md](Readme.md)
CORRECTION-NOTICE).

**Non-goals (explicit):**
- **Does not use the λ dispute-jump market-making engine.** Weather markets resolve
  deterministically (NWS/METAR observation); there is no UMA dispute jump, so
  [pricing/quote.py](pricing/quote.py) (Avellaneda–Stoikov) and
  [estimators/lambda_engine.py](estimators/lambda_engine.py) are the *wrong tools* and are
  intentionally untouched. This feature lives in the **data/signal plane**.
- **No live copy execution in v1.** Same gate as core PolyLambda —
  [JURISDICTION.md](JURISDICTION.md) (Polymarket ToS / US-person). The v1 output is a
  *signal/feed*, not an order router.
- **No independent forecast edge (deferred).** A true weather edge would need a
  GFS/ECMWF ensemble → bucket-probability model; that is a separate, larger effort noted in
  §7, not designed here.

---

## 2. The premise, stated honestly

The seed list is **Polycool Bot marketing** — self-reported, survivorship-selected, and
unaudited (full argument in [polycool_info.md](polycool_info.md) — the
source-bias notice). We do **not** assume the numbers are true. Phase 1's entire job is to
**verify them from on-chain data**; a leaderboard entry that we cannot reconcile to real
fills is reported as *unverified*, not quietly trusted. The honest failure mode is "we could
not verify," never a fabricated address or a made-up PnL.

---

## 3. Architecture — reuse map (grounded in real code)

Every design piece maps to an existing module. New work is thin glue on top.

| Design piece | Reuses (existing) | What's new |
|---|---|---|
| **Wallet-scoped fill reconstruction** | HF `order_filled` table (`maker`, `taker`, `timestamp`, amounts) via [data/hf.py](data/hf.py) `query()` + the `DERIVE_FILL_SQL` price/size logic in [data/fills.py](data/fills.py) | A `WHERE lower(maker)=? OR lower(taker)=?` query. **No per-wallet function exists today** — `fetch_fills_hf` is per-`conditionId` only |
| **Weather-market universe** | [data/metadata.py](data/metadata.py) `derive_category` / `category_case_sql` (keyword heuristic over `marketName`/`marketSlug`/`description`) | A **new `weather` category** (temp / °C / °F / city keywords). Today weather markets fall into `other` |
| **Realized-PnL truth** | [data/conditions.py](data/conditions.py) `resolved_conditions` / `payout_for` / `hf_payout_map` (payout vectors per `conditionId`) | Join wallet fills → payouts to compute realized PnL, win rate, avg entry, hold time |
| **Handle → wallet map** | (none — network gated, see §4a) | A hand-seeded `handle → 0x…` table; the X/Polymarket profile lookup is a **gate**, not built |
| **Fingerprint detector** | pure-function + synthetic-fixture test pattern from `tests/` (`test_hazard.py`, `test_sigma.py`) | Feature extractor over a wallet's weather fills (§4d) |
| **Ranked feed surfacing** | webapp pattern: [webapp/backend/services.py](webapp/backend/services.py) + [webapp/backend/routes.py](webapp/backend/routes.py) + `webapp/frontend/src/sections/*` + `client.ts` + `App.tsx` NAV | A `/smartmoney` (or `/weather`) endpoint + `SmartMoney.tsx` section — mechanical, mirrors `base_rates()` / `Ablation.tsx` |
| **"Would copying have paid" backtest** | [forwardtest/replay_ablation.py](forwardtest/replay_ablation.py) arm pattern (the `lambda_jump_hazard` arm is the template: per-market `(pnl, avoided, forgone)` over a fixed universe, Sharpe + power calc) | A copytrade arm over a **survivorship-free** weather-market universe |

**Data source note.** The historical backbone is the HuggingFace dataset
(`moose-code/polymarket-onchain-v1`) queried in place with DuckDB — see the `COLUMNS`
registry in [data/hf.py](data/hf.py). The Envio indexer ([indexer/](indexer/)) does **not**
index fills (CLOB `OrderFilled` is commented out) and has no wallet/trader entity, so it is
**not** used here. Live wallet data (Polymarket Data-API `/positions?user=`) is a future
option (§7), gated by the same SNI block described in §4a.

---

## 4. The four data problems

### (a) Handle → wallet resolution — **the hard gate**

The [polycool_info.md](polycool_info.md) entries are Polymarket **usernames**
(ColdMath, BeefSlayer, HondaCivic…), not addresses. To reconstruct anything on-chain we need
each wallet address. Available resolution paths, ranked:

1. **Already-given addresses** — a few entries are raw wallets (`0xf2e346ab`,
   `Dreamer3bcbcd6c` is wallet-derived). Use directly.
2. **X handles** — `@BeefSlayer_`, `@0xMarchyel` (HondaCivic), `@dpnd_poly` — resolvable via
   the trader's linked Polymarket profile.
3. **Username → address via Polymarket profile/leaderboard API** — the general case.

**The gate:** paths 2–3 require hitting `*.polymarket.com`, which the **dev network
SNI-blocks** (documented in [execution/clob.py](execution/clob.py) — live shapes are
fixture-tested only because the network blocks the host). So username resolution **cannot be
automated in this environment**. Documented approach:

- Maintain a hand-seeded `handle → 0x…` map (a small checked-in table, e.g.
  `data/weather_traders.py`), populated from the given addresses + any manually resolved
  ones.
- Every entry carries a `resolved: true|false` flag. **Unresolved handles are reported as
  unverified** — never guessed, never fabricated.
- The detector (§4d) does not depend on the map — it discovers wallets by *behavior*, so the
  product still works even if most handles stay unresolved.

### (b) Weather-market universe

Extend the keyword map in [data/metadata.py](data/metadata.py). Add a `weather` branch to
`CATEGORY_KEYWORDS` / `category_case_sql`, e.g. keywords:
`temperature`, `temp`, `highest-temp`, `°c`, `°f`, `degrees`, plus the recurring city names
(`tokyo`, `nyc`/`new-york`, `london`, `seoul`, `wellington`, `hong-kong`, `chicago`,
`miami`, `buenos-aires`, `mexico-city`, `lucknow`, `seattle`, `atlanta`, `austin`) and
climate terms (`hottest on record`, `global temp`, `anomaly`).

**Risk (document, don't hand-wave):** title string-matching is precision/recall-lossy —
"New York" appears in non-weather markets; some weather markets may omit the city in the
slug. Mitigation: match on `marketName || marketSlug || description` (all three are in HF
`market_data`), and treat the category as *coarse/best-effort* exactly as the module already
labels its other categories. A later pass could swap in Polymarket Gamma tags by slug (same
future note the module already carries).

### (c) Per-wallet PnL reconstruction

The SQL shape (pseudocode; new function, e.g. `data/weather_traders.py:wallet_weather_pnl`):

```
-- 1. weather-market conditionIds (via category_case_sql = 'weather')
-- 2. that market's outcome token ids  (data.metadata.tokens_for_condition)
-- 3. all fills where the wallet is maker or taker on those tokens:
SELECT ... FROM order_filled
WHERE (lower(maker) = :addr OR lower(taker) = :addr)
  AND makerAssetId IN (:weather_token_ids) OR takerAssetId IN (:weather_token_ids)
-- 4. derive price/size/side per DERIVE_FILL_SQL (data/fills.py)
-- 5. net position per conditionId; join condition payouts (data.conditions.payout_for)
-- 6. realized PnL = payout*held_size - cost_basis; win = payout matched the held leg
```

Outputs per wallet: realized PnL, position count, win rate, avg entry price, avg hold time,
payoff-skew. **These are the numbers that verify or falsify the leaderboard** — the
verification deliverable is a table of *claimed vs reconstructed* per trader, with a
reconciliation delta and a `verified | partial | unverified` verdict.

### (d) Floor-buy / NO-grind fingerprint detector

Define "smart money" by **behavior**, so wallets are discoverable independent of the list.
Features per wallet (over its weather fills):

- **Entry-price histogram concentration** — fraction of buys at 1–5¢ (floor-buyer) or NO at
  90–99¢ (grinder); the [polycool_info.md](polycool_info.md) *dead-zone* (15–40¢) fraction
  should be near zero for a true specialist.
- **Hold-to-resolution ratio** — floor-buyers hold; a high fraction of positions held to the
  payout event.
- **Positive-skew payoff** — many small losses, few large wins (convexity), consistent with
  the lottery structure. NO-grinders are the mirror (many small wins, rare large loss).
- **Volume / position count** — separates grinders (Poligarch/dpnd scale) from snipers.
- **Realized edge** — reconstructed PnL > 0 net, on ≥ a minimum sample (guard tiny-n like
  russell110320's n=150).

A wallet qualifies as a candidate when its fingerprint matches an archetype **and** its
reconstructed edge is positive on a sufficient sample. Rank by risk-adjusted realized PnL,
surfacing archetype + confidence + sample size (never a bare win rate — see the
[polycool_info.md](polycool_info.md) convexity argument).

---

## 5. Staged roadmap (documentation — Conventional-Commits, matching `git log`)

Each is a *potential* commit; files come from the reuse map (§3). None are executed here.

| # | Commit | Intent | Touches |
|---|---|---|---|
| 1 | `feat(data): weather category + wallet-scoped fill reconstruction over HF order_filled` | Add the `weather` category (§4b) and a per-wallet weather-fill query (§4c) | `data/metadata.py` (keyword map + `category_case_sql`), new `data/weather_traders.py`, reuse `data/fills.py` `DERIVE_FILL_SQL`, `data/hf.py` `query` |
| 2 | `feat(recon): on-chain verification of the weather-trader leaderboard (PnL/win-rate)` | Reconstruct each seeded wallet's PnL/win-rate; emit *claimed vs reconstructed* with a `verified/partial/unverified` verdict | new `recon/weather_verify.py` (mirror `recon/check.py`), `data/conditions.py` payouts, the §4a handle map |
| 3 | `feat(signal): floor-buy / NO-grind fingerprint detector → ranked smart-money feed` | Behavior-based detector (§4d) that discovers + ranks smart-money wallets independent of the list | new `estimators/`- or `recon/`-style module + synthetic-fixture tests (pattern: `tests/test_hazard.py`) |
| 4 | `feat(webapp): Smart-Money Weather section (service + route + section)` | Expose the ranked feed + verification table in the dashboard | `webapp/backend/services.py`, `webapp/backend/routes.py`, `webapp/backend/schemas.py`, `webapp/frontend/src/sections/SmartMoney.tsx`, `client.ts`, `App.tsx` NAV; published fallback in `constants.py` |
| 5 | `feat(forwardtest): copytrade replay-ablation — would-copying-have-paid, survivorship-free` | Backtest the signal: did following the fingerprint pay, net of copy-latency, over ALL weather markets (not just winners)? | `forwardtest/replay_ablation.py` (new arm, template = `lambda_jump_hazard`), test via `tests/test_replay_hazard.py` pattern |

**Sequencing rationale:** 1→2 verifies the premise before any strategy claim (de-risk).
3 generalizes it. 4 ships user value. 5 is the honest "does it actually pay" proof and can
lag — like the core λ ablation, it is the adjudicator, not the pitch.

---

## 6. Risks & honesty ledger

| Risk | Why it matters | Mitigation / what would have to be true |
|---|---|---|
| **Survivorship bias** | The seed list is winners only; it says nothing about the base rate of failed floor-buyers | The §4d detector + §5.5 backtest must run over the **full** weather-market/wallet population, winners and losers, or the edge is circular |
| **Handle→wallet unverifiable** | Username resolution is network-gated (§4a) | Seed only addresses we can stand behind; flag the rest `unverified`; the detector doesn't depend on the map |
| **String-match false positives** | Title keywords misclassify markets (§4b) | Match across name+slug+description; label the category coarse/best-effort; future Gamma-tag swap |
| **Copy-latency slippage** | Floor-buys fill at 1–2¢; a follower who sees the fill and copies arrives late, at a worse price — the realized copy edge < the leader's edge | The §5.5 backtest must model entry *after* the leader's fill; report copy-EV, not leader-EV |
| **Tiny samples / lucky tails** | High win rates on small n (russell110320 n=150; the 97–99% NO-grinders) can be pre-blowup insurance-sellers | Minimum-sample guards; rank on risk-adjusted realized PnL + skew, never bare win rate |
| **ToS / US-person gate** | Any live copy execution inherits the [JURISDICTION.md](JURISDICTION.md) constraint | v1 is signal-only; live routing stays gated/out-of-scope |

---

## 7. Open questions / decisions needed

1. **Polymarket API access** for username→wallet resolution — is there an off-dev-network
   path (the `*.polymarket.com` SNI block, §4a)? Without it, coverage is limited to
   hand-seeded addresses.
2. **Live layer later?** A Polymarket Data-API `/positions?user=` / `/activity?user=` path
   would give near-real-time positions for a live feed — same network gate; deferred.
3. **Independent forecast edge (future upgrade).** A GFS/ECMWF ensemble →
   `P(temp ∈ bucket)` estimator would let PolyLambda *originate* weather signals rather than
   only mirror others. This is the deepest moat and a separate design — explicitly deferred
   from this copytrade-signal spec.
4. **Feed cadence & storage** — batch (recompute from HF on a schedule, published-constant
   style like `/ablation`) vs on-demand. Start batch, matching the existing published-artifact
   pattern in `webapp/backend/constants.py`.
