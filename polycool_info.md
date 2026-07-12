# Polymarket Weather Traders — Strategy Brief (captured July 2026)

> **What this is.** An analyst's rewrite of the "Top 67 Polymarket Weather Traders"
> list, enriched with derived edge analysis, a robustness critique, and a concrete bridge
> to the **PolyLambda** engine. Every trader profile and metric from the source is
> preserved below; the analytical layers around it are new.

> ## ⚠ SOURCE-BIAS NOTICE — read before trusting the numbers
>
> This list originates as **Polycool Bot marketing** (a Polymarket copytrading product).
> That colors everything:
> - **"Top 67" but only 20 are profiled.** The remaining 47 are not in the source; do not
>   invent them.
> - **Metrics are self-reported on-chain snapshots**, selected for winners
>   (**survivorship bias**) — not an audited, trade-level ledger.
> - **The source is incentivized** to make copytrading look easy and safe. Win rates and
>   PnL below are plausible but **unverified**; several can't be reconciled without
>   trade-level data (see *Robustness* section).
>
> Treat this as a **hypothesis-generation** document, not evidence. The tone here matches
> the repo's own [ANALYSIS.md](ANALYSIS.md) / [Readme.md](Readme.md) CORRECTION-NOTICE
> discipline: preserve the claim, flag what's load-bearing, verify before trusting.

---

## TL;DR

- **The market:** Polymarket **weather** markets — daily temperature buckets for specific
  cities ("Highest temp in Tokyo 16°C on Mar 20", "NYC 43–44°F"), plus a few global-climate
  and record markets. Resolution is a **deterministic weather observation** (NWS / METAR /
  official station reading), not a contested UMA dispute.
- **The edge (in one line):** *forecast-vs-market mispricing on convex, cheap tails.* Buy
  the correct temperature bucket at **1–5¢** and let it resolve to **$1.00**, or sell the
  near-certain tail at **90–99¢**.
- **Why win rate is a trap:** the two most-profitable profiles here win **15.8%** and
  **81.7%** of the time. Both print money. Payoff **convexity × position sizing** dominates
  batting average.
- **Two edges dominate:** *floor-buyers* (long-lottery YES at 1–5¢) and *NO-grinders*
  (insurance-selling at 90–99¢). The **15–40¢ dead zone** is where almost nobody trades.
- **Relevance to PolyLambda:** this is a **different regime** from our dispute/λ thesis
  (no jump-lock risk; resolution is deterministic). The reusable assets are the **indexer +
  HF fills pipeline** (to verify these wallets) and the **replay-ablation harness** (to test
  a weather strategy honestly). The Avellaneda–Stoikov quoter is mostly the *wrong* tool
  here — see *Bridge to PolyLambda*.

---

## Master comparison table

Metrics normalized from the source's inconsistent labels ("PnL" / "Weather PnL" / "Total
gains"). **PnL** = the headline realized figure; **N** = predictions or positions as
reported; **PnL/N** = derived (headline PnL ÷ N), a rough per-bet edge proxy. Blank =
not given in source.

| # | Handle | Archetype | PnL | N (preds/pos) | Win rate | Best win | Net worth | PnL/N |
|---|--------|-----------|-----|---------------|----------|----------|-----------|-------|
| 1 | **ColdMath** | Automated floor-sniper (AI bot) | $124.9K | 6,575 | 81.7% | $12.4K | — | ~$19 |
| 2 | **VibeTrader** | High-volume mispricing hunter | $132.9K | 5,114 | 37.7% | $21.3K | — | ~$26 |
| 3 | **gopfan2** | Conviction whale (since 2022) | $247.9K | 2,294 | 62.1% | $51.6K | $81.1K | ~$108 |
| 4 | **BeefSlayer** | Penny sniper (US cities) | $63.6K | 1,585 | 68.2% | $4.1K | — | ~$40 |
| 5 | **HondaCivic** | Precision floor-buyer | $55.4K | 3,828 | 84.2% | $15.1K | $238.6K | ~$14 |
| 6 | **HenryTheAtmoPhD** | Domain-expert (atmos. sci.) | $55.8K | 3,472 | 36.5% | $4.9K | — | ~$16 |
| 7 | **JoeTheMeteorologist** | Model-driven conviction | $112.1K | 3,025 | 15.8% | $71.2K | — (vol $1.17M) | ~$37 |
| 8 | **russell110320** | Global-climate whale | $43.9K | 150 | 64.3% | $10.3K | — | ~$293 |
| 9 | **Railbird** | NO-grinder (model-confirmed) | $23.8K | 5,937 | 75.4% | $1.2K | — | ~$4 |
| 10 | **Maskache2** | Asian-market grinder | $34.1K | 1,941 | 33.5% | $7.6K | — | ~$18 |
| 11 | **Poligarch** | Volume king | $50.6K | 23,686 | 61.1% | — | $39.1K | ~$2 |
| 12 | **NoonienSoong** | Data-driven selective | $29.7K | 2,683 | 97.1% | — | $33.1K | ~$11 |
| 13 | **IWantYourMoney** | Floor buyer (repeat one move) | — | 8,200+ | — | — | — (avg 20×) | — |
| 14 | **dpnd** | Scale operator (ultra-long holds) | $27.8K | 19,289 | 48.2% | — | $45.4K | ~$1 |
| 15 | **Capillatus** | Quiet high-win-rate | $10.4K | 639 | 76.4% | — | $15.2K | ~$16 |
| 16 | **TheySeemeBuyingTheyHatin** | High win rate | $13.8K | 380 | 98.8% | — | — | ~$36 |
| 17 | **Dreamer3bcbcd6c** | Automated (bot-likely) | $9.8K | 3,485 | 99.4% | — | $292K | ~$3 |
| 18 | **0xf2e346ab** | Rules-based (YES<15¢ / NO>40–50¢) | $25.7K | 1,420 | — | — | — | ~$18 |
| 19 | **NullHyper** | Consistent (~1.5d holds) | $7.7K | 1,410 | 81.2% | — | — | ~$5 |
| 20 | **cyberkurajber** | Resilient (high-conviction) | $14.9K | 2,181 | 34.4% | — | — | ~$7 |

> **Reading the table:** `PnL/N` is deliberately crude — it divides a *headline* PnL by a
> *count* that mixes predictions vs positions across profiles, so treat it as an order-of-
> magnitude tell, not a Sharpe. It does surface the two structural extremes: **whales**
> (russell110320 ~$293/bet on n=150, gopfan2 ~$108) concentrate size; **grinders**
> (Poligarch ~$2, dpnd ~$1) profit through sheer volume on thin per-bet edge.

---

## Derived edge analysis (the robust-thinking layer)

### 1. Win rate is the wrong lens; convexity × sizing is the right one

The single most important pattern: **JoeTheMeteorologist wins 15.8%** of the time with
**$112K PnL**, while **ColdMath wins 81.7%** with $124.9K. Both are top-3 profitable. That
is only possible because the payoff is **convex and asymmetric**.

Floor-buying at **1–5¢** is a **long-lottery** structure. If you buy YES at 2¢ and it
resolves to $1.00, that is **+4,900%** on the stake. Break-even math:

> Risk **$20** at 2¢ for a **$1,000** payout (50×). You need to be right **1 in 50** to
> break even. Risk **$20** for a **$12K** payout (the ColdMath best trades, ~600×) and you
> need **1 in 600**. A 15.8% win rate on 600× convexity is enormously positive-EV.

Implication for anyone copying this: **the batting average is noise; the edge lives in
(a) picking buckets that are underpriced relative to a real forecast, and (b) sizing so the
lottery-losses don't ruin you before the winners land.** This is a Kelly/positive-skew
problem, not a "high win rate" problem.

### 2. Two dominant edges, opposite risk shapes

| | **Floor-buyers** | **NO-grinders** |
|---|---|---|
| Trade | Long YES at **1–5¢** on the correct bucket | Short the tail: buy **NO at 90–99¢** on buckets that won't hit |
| Payoff shape | **Convex** (small loss, huge win) — buying lottery tickets | **Concave** (small win, rare huge loss) — selling insurance |
| Win rate | Low-to-moderate, high variance | **High** (looks "safe"), fat left tail |
| Failure mode | Slow bleed if you never pick right; solved by cheap size | **Ruin** if a "can't happen" bucket hits at size |
| Exemplars | ColdMath, BeefSlayer, HondaCivic, IWantYourMoney, 0xf2e346ab | Railbird, and the NO leg of ColdMath's bot; high-win-rate profiles (NoonienSoong 97.1%, TheySeemeBuyingTheyHatin 98.8%, Dreamer3bcbcd6c 99.4%) are structurally consistent with tail-selling |

The high-win-rate names deserve suspicion, not admiration: a 97–99% win rate is the
**signature of insurance-selling**, which is precisely the strategy that looks flawless
until the tail event bankrupts it. Their reported PnL ($10–30K) is modest relative to the
floor-buyers precisely because they're collecting pennies against a rare dollar loss.

### 3. The dead zone (15–40¢)

The source's price-bucket playbook — and the data — agree that **almost no top trader
enters at 15–40¢**. That band has the worst risk/reward: neither the convex cheap-tail
upside of the 1–5¢ floor nor the high-probability "insurance premium" of the 90–99¢ tail.
It's the middle of the distribution where the market is most efficient and the edge is
smallest.

**Price-bucket playbook (from the source, retained):**

| Band | Move | Who |
|---|---|---|
| **1–10¢** | Buy YES on the bucket you expect to hit; risk $10–$100 for $1K–$15K | ColdMath, BeefSlayer, HondaCivic |
| **40–60¢** | Contested — only with high model conviction | russell110320, HenryTheAtmoPhD (macro/climate) |
| **90–99¢** | Buy NO on near-impossible buckets; needs size to matter | Railbird, ColdMath's NO leg |
| **15–40¢ (dead zone)** | **Avoid** — worst risk/reward | ~nobody in the top 20 |

---

## Archetype taxonomy

The 20 collapse into five repeatable playbooks:

1. **Automated floor-sniper** — bots that scan live weather (METARs, GFS/ECMWF) 24/7 and
   snipe 1–2¢ mispricings, catching 3am opportunities humans sleep through.
   *Traders:* ColdMath (Claude-powered "Clawdbot"), Dreamer3bcbcd6c (bot-likely, $292K
   wallet), plausibly NoonienSoong. *Edge:* latency + coverage.

2. **High-volume mispricing hunter** — human/semi-auto, many categories, aggressive
   sizing, low win rate carried by massive winners.
   *Traders:* VibeTrader ($328K gross gains, 37.7% win), Maskache2, cyberkurajber.
   *Edge:* breadth + tolerance for variance.

3. **Conviction whale** — few, large, patient positions; ~14–29 day holds.
   *Traders:* gopfan2 ($247.9K, since 2022), russell110320 (global-climate, n=150,
   $5K–$32K bets), HenryTheAtmoPhD. *Edge:* capital + patience on high-confidence reads.

4. **Domain-expert** — meteorology/atmospheric-science knowledge to price where the market
   hasn't priced the science.
   *Traders:* HenryTheAtmoPhD (retired atmos-sci professor), JoeTheMeteorologist
   (GFS/ECMWF before entries). *Edge:* structural forecasting advantage.

5. **NO-grinder / tail-seller** — buy NO at 90–99¢ on buckets that almost certainly won't
   hit; high win rate, thin per-bet edge, fat left tail.
   *Traders:* Railbird (~75% win, NO specialist), and the high-win-rate cluster
   (NoonienSoong, TheySeemeBuyingTheyHatin, Dreamer3bcbcd6c). *Edge:* consistency, at the
   cost of ruin risk.

**Cross-cutting patterns (from source, validated):** city specialists (3–5 cities:
Seoul, NYC, London, Wellington recur) beat generalists; bots are taking share; and
domain expertise (GFS/ECMWF discipline) is a durable, structural advantage.

---

## Robustness & data-quality critique

The parts the marketing page omits. **What would have to be true** for each headline claim
to be trustworthy:

- **Survivorship bias (the big one).** This is a leaderboard of winners. For every
  ColdMath there is an unknown population of floor-buyers who bled out. The list tells you
  *nothing* about the base rate of failure, so it **overstates** the strategy's expectancy.
  *To trust it:* you'd need the full distribution of everyone running the same fingerprint,
  winners and losers.
- **Tiny samples.** russell110320's 64.3% win rate is on **n=150**; NoonienSoong's 97.1%
  is on selective entries. Small-n win rates have wide confidence intervals — a 97% on a
  few hundred selective bets is not distinguishable from a lucky insurance-seller pre-blowup.
- **Metric reconciliation fails without trade-level data.** You cannot derive a $132.9K PnL
  from "5,114 predictions, 37.7% win rate" without every fill's size and price. The
  headline PnL, win rate, and "total gains vs total losses" figures come from **different
  denominators** and often don't tie out (e.g. "gains $328K" vs "PnL $132.9K" for
  VibeTrader). Treat each number as a **separate, unaudited snapshot**.
- **Handles → wallets are unverified.** Some entries are already just wallets
  (0xf2e346ab, Dreamer3bcbcd6c). Copytrading requires resolving each handle to an address
  and confirming it's the same actor — the source doesn't provide the mapping.
- **Source incentive.** Polycool profits when you believe copytrading is easy. High-win-rate
  hero numbers and 40,000% best-trades are exactly what a copytrade funnel selects for.

**Bottom line:** this is a strong **hypothesis generator** (the floor-buy convexity edge is
real and mechanically sound) and a weak **evidence base** (the specific numbers are
unaudited and survivorship-selected). Verify on-chain before risking capital.

---

## Bridge to PolyLambda (implementation connection)

> **Chosen product direction:** the **smart-money copytrade signal** — verify these wallets
> on-chain, detect the floor-buy / NO-grind fingerprint, and surface a ranked feed. It is
> designed in full, grounded in the actual codebase, in
> **[WEATHER_COPYTRADE.md](WEATHER_COPYTRADE.md)**. The mapping below is the regime analysis
> that motivates it.

This is a **different regime** from PolyLambda's core thesis, and being honest about that is
the point.

| | **PolyLambda core (dispute/λ)** | **Weather markets** |
|---|---|---|
| Resolution | UMA optimistic oracle, **disputable** | Deterministic weather observation (NWS/METAR) |
| Core risk | Dispute jump + redemption freeze + degraded exit | Forecast error; **no** dispute-lock dynamic |
| Edge | Price adverse selection (jump-intensity **λ**) + avoid the lock | **Forecast-vs-market** mispricing on convex tails |
| Right tool | Avellaneda–Stoikov quoting + jump premium | Lottery/EV sizing on a forecast probability |

So most of PolyLambda's *pricing* machinery does **not** transfer — but a lot of its
**data and validation** machinery does. Concrete mapping onto existing modules:

- **[data/hf.py](data/hf.py), [data/fills.py](data/fills.py),
  [data/metadata.py](data/metadata.py) + [indexer/](indexer/) (Envio HyperSync/HyperIndex).**
  This is the reusable crown jewel. The same pipeline that joins the HF dataset to on-chain
  fills can **resolve these weather-trader wallets and reconstruct their trade history** —
  turning the unaudited leaderboard into a verified ledger. This directly answers the
  *Robustness* critique above.
- **[estimators/fair_value.py](estimators/fair_value.py) / [estimators/sigma.py](estimators/sigma.py).**
  A weather market's "fair value" is a **forecast bucket-probability** — `P(temp ∈ bucket)`
  from a GFS/ECMWF ensemble — **not** a belief-volatility random walk. `sigma.py`'s
  belief-vol model doesn't apply; what carries over is the *interface* (an estimator that
  emits a fair value the strategy compares against market price). A new
  `estimators/weather_forecast.py` would slot into the same shape.
- **[forwardtest/replay_ablation.py](forwardtest/replay_ablation.py) /
  [forwardtest/runner.py](forwardtest/runner.py).** The natural, honest way to test a
  floor-buy vs NO-grind strategy — the **same replay-ablation discipline** already used to
  prove the λ term earns its keep. Run "floor-buy ON vs OFF" over historical weather
  markets; measure realized EV against the survivorship-free population.
- **[pricing/quote.py](pricing/quote.py) (Avellaneda–Stoikov + jump premium).** Call it out
  plainly: this is **mostly the wrong tool** for lottery floor-buying. A/S quotes a two-sided
  spread around an inventory-managed reservation price; floor-buying is a **directional,
  hold-to-resolution** bet with no meaningful inventory-skew or continuous requoting. Don't
  force-fit it — a weather strategy needs an EV/Kelly sizer, not a market-making quoter.
- **[JURISDICTION.md](JURISDICTION.md).** The Polymarket ToS / US-person gate that already
  constrains PolyLambda's live mode applies identically here — any copytrade/execution path
  inherits that constraint.

---

## Proposed commit roadmap (documentation only — not executed)

The staged roadmap for the chosen **smart-money copytrade signal** direction lives in
**[WEATHER_COPYTRADE.md §5](WEATHER_COPYTRADE.md)**, with each commit's exact file touches.
In brief:

1. `feat(data): weather category + wallet-scoped fill reconstruction over HF order_filled`
2. `feat(recon): on-chain verification of the weather-trader leaderboard (PnL/win-rate)`
3. `feat(signal): floor-buy / NO-grind fingerprint detector → ranked smart-money feed`
4. `feat(webapp): Smart-Money Weather section (service + route + section)`
5. `feat(forwardtest): copytrade replay-ablation — would-copying-have-paid, survivorship-free`

The **independent forecast edge** (a GFS/ECMWF `P(temp ∈ bucket)` estimator + floor-buy/
NO-grind strategy arm in the engine) is a deeper, separate direction — deferred, and noted in
[WEATHER_COPYTRADE.md §7](WEATHER_COPYTRADE.md).

---

## Open questions / next steps

To move from "interesting list" to "tested edge":

1. **Wallet resolution** — map each handle to its address (the pipeline in `data/` can do
   this); without it, none of the metrics are verifiable and copytrading is impossible.
2. **Trade-level export** — reconstruct fills to reconcile PnL vs win rate vs gains/losses
   and measure *actual* per-bet EV, not headline snapshots.
3. **Forecast data source** — a GFS/ECMWF ensemble feed is the fair-value input; without an
   independent forecast there is no mispricing signal, only trend-following.
4. **Survivorship correction** — sample the *full* population of floor-buyers (winners and
   losers) to estimate the true base rate before believing the expectancy.
5. **ToS / US-person gate** — the [JURISDICTION.md](JURISDICTION.md) constraint gates any
   live execution or copytrade path; resolve before building an execution leg.

---

<details>
<summary><b>Full source profiles (preserved verbatim from the original paste)</b></summary>

*Note: the original included `!Screenshot ….png` image refs that point to nothing in this
repo; they are omitted. All text and metrics are retained.*

**1. ColdMath (AI Bot)** — Runs a custom *Clawdbot* (Claude-powered AI agent) scanning
real-time weather 24/7 incl. pilot aviation reports (METARs); detects tiny mispricings,
executes at $0.01–$0.02/contract. Joined Nov 2025. Bio: "Edge Compounds." Core: Tokyo ·
Chicago · Wellington · Lucknow · Global. **PnL $124.9K · 6,575 preds · 81.7% win · best
$12.4K · total gains $156.7K.** Strategy: automated floor-buying (YES/NO at 1–2¢ vs live
weather, hold to resolution, 24/7). Best: Tokyo 16°C Mar 20 $25→**+$12,427**; Chicago 54°F+
Mar 11 $24→**+$12,373**; Tokyo 15°C Mar 20 $16→**+$8,090**; Wellington 20°C Mar 19
$200→**+$7,702**; Lucknow 39°C Mar 7 $13→**+$6,837**.

**2. VibeTrader (Diversified)** — Highest raw PnL of any weather trader. Mixes city-temp
markets with high-vol event bets (e.g. Elon tweet counts); massive volume, aggressive
sizing, extreme-mispricing hunter on NYC/Miami daily highs. Core: New York · Miami ·
Multi-niche. **PnL $132.9K · 5,114 preds · 37.7% win · best $21.3K · total gains $328.4K.**
Strategy: high-volume mispricing hunter (low win rate OK because winners are massive,
$100–$300 in → $3K–$8K out). Best: NYC 43–44°F $307→**+$8,279**; Miami 80–81°F Mar 23
$110→**+$4,025**; NYC 65°F+ Oct $84→**+$3,934**.

**3. gopfan2 (OG Whale)** — Trading since Nov 2022; one of the earliest still-active
accounts. Core: Global · Multi-city. **Weather PnL $247.9K · 2,294 positions · 62.1% win ·
best $51.6K · net worth $81.1K** (88 still active). Strategy: large conviction bets
(thousands per position, avg hold ~14 days).

**4. BeefSlayer (Sniper)** — Master of the cheap entry; buys $6–$40, rides to $1,800–$4,600.
US-city specialist. X: @BeefSlayer_. Joined Sep 2025. Core: Seattle · Atlanta · New York ·
Chicago · Austin. **PnL $63.6K · 1,585 preds · 68.2% win · best $4.1K · total gains $73.2K.**
Strategy: penny sniper (enter 1–5¢, resolve to $1.00, risk <$100/position; edge = reading
NWS forecasts). Best: Seattle 52–53°F Mar 4 $536→**+$4,103**; Atlanta 38–39°F Jan
$6→**+$2,984**; NYC 34–35°F $40→**+$1,831**.

**5. HondaCivic (Precision)** — Enters at the absolute floor; turned $1 into $55K. X:
@0xMarchyel. Joined Jan 2026. Core: Hong Kong · New York · London · Buenos Aires · Seoul.
**PnL $55.4K · 3,828 preds · 84.2% win · best $15.1K · net worth $238.6K.** Best: Hong Kong
≤15°C $37→**+$15,144**; Buenos Aires ≥34°C $4.88→**+$1,215**; NYC 56–57°F $580→**+$1,690**.

**6. HenryTheAtmoPhD (Professor)** — Retired atmospheric-science professor; uses atmospheric
patterns, historical climate data, meteorology. Joined Feb 2025, avg hold 29 days. Core:
Seoul · New York · London. **PnL $55.8K · 3,472 preds · 36.5% win · best $4.9K · total gains
$99.2K.** Strategy: domain-expertise arbitrage (low win rate, huge winners). Best: Seoul
11°C Feb 22 $518→**+$4,944**; NYC 38–39°F $193→**+$3,601**; London 52–53°F Dec
$116→**+$2,786**.

**7. JoeTheMeteorologist (Daily Grinder)** — Pulls $1,500–$2,000 daily; controversial
(publicly denied manipulation allegations). Joined Jun 2025, volume >$1.1M; checks
GFS/ECMWF before entries. Core: Global · Multi-city. **PnL $112.1K · 3,025 preds · 15.8%
win · best $71.2K · volume $1.17M.** Strategy: model-driven conviction (extremely low win
rate, outsized winners; sizes big when models align).

**8. russell110320 (Global Climate)** — Global temperature/climate records: monthly anomaly
ranges, "hottest on record", YoY comparisons. Bigger bets ($5K–$32K), fewer trades. Core:
Global temp · Climate records · Anomaly ranges. **PnL $43.9K · 150 preds · 64.3% win · best
$10.3K · total gains $112.4K.** Best: Feb 2026 4th-or-lower-hottest-on-record
$32,475→**+$10,283**; global +1.05–1.09°C $19,911→**+$8,418**; global +1.20–1.24°C
$2,523→**+$8,319**.

**9. Railbird (Methodical)** — ~80% win across 5,200+ trades; buys NO on tight ranges, YES
only when clearly mispriced; GFS/ECMWF confirmation before sizing. Core: Multi-city · NO
specialist. **Weather PnL $23.8K · 5,937 positions · 75.4% win · best $1.2K · total gains
$57.4K.** Strategy: NO-heavy model confirmation (safe, consistent grinder).

**10. Maskache2 (Grinder)** — Asian markets (Seoul, Hong Kong, Wellington); 1–10¢ entries
paying 1,000–5,000%. Active daily. Core: Seoul · Hong Kong · Wellington · New York. **PnL
$34.1K · 1,941 preds · 33.5% win · best $7.6K · total gains $108.2K.** Best: Seoul 7°C Mar 9
$4,138→**+$7,639**; Seoul 12°C Feb 28 $2,545→**+$4,662**; Wellington 19°C Mar 23
$313→**+$2,701**.

**11. Poligarch (Volume King)** — Trades at scale across the entire weather market. **Weather
PnL $50.6K · 23,686 positions · 61.1% win · 2,450 active · net worth $39.1K.**

**12. NoonienSoong (Data-Driven)** — Bio: "I like Data." Highest win rate on the list; quiet,
methodical, extremely selective. Joined Feb 2025. **Weather PnL $29.7K · 2,683 positions ·
97.1% win · 72 active · net worth $33.1K · total gains $35.3K.**

**13. IWantYourMoney (Floor Buyer)** — One move, repeated 8,200+ times: find the $0.05 floor
on weather temp markets worldwide, buy, hold to resolution. Core: Mexico City · Chicago ·
London · Global. **8,200+ trades · avg entry $0.05 · avg return 20×.** Best: Mexico City
$74→**+$1,403**; Chicago $71→**+$1,291**; London $68→**+$885**.

**14. dpnd (Scale Operator)** — Ultra-long holds (15+ days). X: @dpnd_poly. $144K gains
offset by $116K losses — profits through scale. **Weather PnL $27.8K · 19,289 positions ·
48.2% win · 1,087 active · net worth $45.4K.**

**15. Capillatus (Cloud Nerd)** — Named after *cumulonimbus capillatus*; quiet, high win
rate. **Weather PnL $10.4K · 639 positions · 76.4% win · 138 active · net worth $15.2K ·
total gains $16.7K.**

**16. TheySeemeBuyingTheyHatin (High Win Rate)** — 98.8% win on weather; joined Feb 2026;
only $797 total losses. **Weather PnL $13.8K · 380 positions · 98.8% win · 76 active · total
gains $14.6K.**

**17. Dreamer3bcbcd6c (Automated)** — 99.4% win; likely bot-driven. Wealthiest wallet on the
list. **Weather PnL $9.8K · 3,485 positions · 99.4% win · 475 active · net worth $292K ·
total gains $16.6K.**

**18. 0xf2e346ab (Anonymous)** — No username, just a wallet. Rules: buys YES only below
10–15¢, NO only above 40–50¢; risks <$1/position. **Winnings $25.7K · 1,420 preds · <$1
risk/position.** Notable: $48→**+$1,020**; $127→**+$1,221**.

**19. NullHyper (Consistent)** — Steady, methodical; avg hold ~1.5 days. **Weather PnL $7.7K
· 1,410 positions · 81.2% win · 199 active · total gains $41.0K · total losses $33.3K.**

**20. cyberkurajber (Resilient)** — Lower win rate but profitable via high-conviction
winners; avg hold ~4 days. **Weather PnL $14.9K · 2,181 positions · 34.4% win · 619 active ·
total gains $39.5K · total losses $24.7K.**

</details>
