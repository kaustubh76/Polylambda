# PolyLambda — Go-Live Roadmap (paper → live, Builders Program)

> **Status:** planned 2026-07-11. Companion to [BUSINESS_PLAN.md](BUSINESS_PLAN.md).
> Jurisdiction is **resolved** (non-US operating entity — see [JURISDICTION.md](JURISDICTION.md)
> resolution log), so the live leg is open. This document sequences the work; every phase has a
> numeric exit gate. When in doubt on facts, [DECISIONS.md](DECISIONS.md) wins.

---

## The single most important architectural fact

The live **write path exists and is gated** (`execution/clob.py`: `place_order` / `cancel_orders` /
`wrap_usdce_to_pusd`, each calling `_require_live_gate()` first) — but there is **no live loop
adapter**. `execution/loop.py:273` raises for `mode="live"`, and `forwardtest/runner.py` refuses
`MODE=live`. The paper adapters (`execution/paper.py`) *simulate* fills; nothing polls **real**
fills or reconciles **real** open-order state.

**Going live is ~30% un-gating and ~70% building the live execution/risk/ops layer that paper
mode never needed.**

| Already built — needs only un-gating / verification | Genuinely missing — must be built |
|---|---|
| Write path (`place_order`/`cancel_orders`/`wrap_usdce_to_pusd`), fixture-tested | `LiveClob` loop adapter (real fills, real order state) |
| Builder Code wiring (`clob.py:262` attaches `BUILDER_CODE` to every order) | Authenticated reads: open orders, own fills, user WebSocket |
| Reserve-before-send notional cap vs `MAX_CAPITAL_USDC` | Order-state reconciliation (loop trusts local `state.order_ids`) |
| Reward-aware exit gate + inventory cap (`execution/loop.py`) | Kill-switch, max-loss/day, **persisted** capital ledger |
| Session-log schema (`forwardtest/session_log.py`) | Live market selection + capital allocation |
| Live dispute feed (`webapp/backend/live.py`, keyless Polygon RPC scan) | Real-time proposal detector wired into the loop (reorg-guarded) |
| Frozen params (`config/model.yaml`, λ\*=0.002, κ_loss=0.76) | Live session logging (`simulated: True` is hardcoded) + live P&L dashboard |
| 141 pytest green; replay-ablation edge proof | Key custody / secrets ops for an unattended hot wallet |

---

## Phase 0 — Network truth & credential bring-up

**Objective:** turn fixture-tested shapes into verified-against-mainnet shapes; stand up auth and
the Builder Code. Blocking: live shape verification was SNI-blocked from the dev network
(`clob.py` header note, 2026-07-05) — run this phase from the operating entity's unblocked host.

- Re-verify every normalizer against live endpoints (`read_book`, `get_market_microstructure`,
  `read_trades`) using the curls documented in the `clob.py` header. Confirm string→float shapes
  and the `clobRewards` %-vs-fraction guard.
- Re-confirm on Polygonscan: `COLLATERAL_ONRAMP` (`0x93070…B8ee`) and `USDC_E` (`0x2791…4174`)
  plus the minimal `wrap` ABI (`clob.py:38-39`, DECISIONS.md §D — note the Jun-2026 adapter
  compromise caveat there; verify addresses are current).
- Verify the pinned SDK `polymarket-client==0.1.0b13` really exposes the `_SdkOrderAdapter`
  surface (`SecureClient.create`, `place_limit_order(builder_code=...)`,
  `cancel_orders(order_ids=...)`). A beta rename is a one-class fix — confirm now, not mid-canary.
- L1→L2 auth: derive CLOB creds from `WALLET_PRIVATE_KEY`; confirm whether explicit
  `CLOB_API_KEY/SECRET/PASSPHRASE` are needed.
- **Register the Builder Code** (polymarket.com/settings?tab=builder), set fee tiers
  (≤100bps taker / 50bps maker), set `BUILDER_CODE` env.

**Files:** `execution/clob.py` (verify; adjust normalizers only if live shapes differ),
`JURISDICTION.md` + `DECISIONS.md` (log rows).
**Risks:** beta SDK surface drift; wrong onramp address = burned funds; builder code not actually
attributed = no revenue.
**Exit gate:** one manual post-only order places → appears in open orders → cancels cleanly; a
filled test order's `OrderFilled.builder` field carries our code **verified on-chain**.

---

## Phase 1 — Live read completion (own fills, open orders, user WS)

**Objective:** give the live loop the reads paper never needed. Paper simulates fills from the
public tape; live must observe **its own** fills and resting orders.

- Add gated authenticated reads to `execution/clob.py`, same seam/style as the write path:
  - `get_open_orders(token_id=None)` — authoritative resting-order state
  - `read_user_fills(since_ts)` — own matched trades (price, size, side, fee, order_id)
  - `open_user_ws(token_ids, on_fill, on_order)` — authenticated user channel with
    reconnect/backoff (pattern already in `cancel_orders`)
- Fixtures for the new shapes in `tests/test_clob.py`.

**Risks:** WS auth/heartbeat handling; fill dedup across WS+REST; maker-rebate vs taker-fee
accounting needed for true P&L.
**Exit gate:** against the Phase-0 account, fills/open-orders state is correct and the WS delivers
a fill event <1s after a REST-confirmed fill.

---

## Phase 2 — The `LiveClob` adapter (highest-risk build)

**Objective:** the real-execution twin of `PaperLiveClob`, so `run_loop` works unchanged. The loop
is already pure over an injected `clob` — this is the seam.

- New `execution/live_clob.py`: duck-typed interface (`get_book/get_micro/place/cancel/step/tape`).
  Public reads reused as-is; `place/cancel` → the real gated write path; `step(now_ts)` polls
  `read_user_fills` + drains the WS queue and returns real fills in the loop's fill-dict shape
  (tag `queue_model="live"`).
- **Order-state reconciliation:** each `step`, reconcile `get_open_orders` against locally-tracked
  ids so cancel/replace never orphans a resting order. New logic — paper never needed it.
- Add the `mode=="live"` construction branch in `execution/loop.py:run_loop` (replacing the
  `RuntimeError` at line 273); allow `MODE=live` in `forwardtest/runner.py`.
- New `tests/test_live_clob.py` (reconciliation, cancel-then-fill race, ambiguous-place recovery).

**Risks (highest of the whole roadmap):** order/fill state divergence → double-quoting or phantom
flatness; ambiguous place failures (`clob.py` deliberately keeps the notional reservation — the
adapter must then reconcile the maybe-resting order); cancel-then-fill races.
**Exit gate:** 1-hour live run, ONE market, `MAX_CAPITAL_USDC` = a few dollars: locally
reconstructed inventory/cash matches `read_user_fills` **and** on-chain `OrderFilled` exactly;
zero orphaned orders after shutdown.

---

## Phase 3 — Live risk controls (all net-new)

**Objective:** what makes an unattended hot bot safe.

- New `execution/risk.py` — a `RiskGovernor` consulted by `loop.tick` before any `clob.place`:
  - **Persisted capital ledger** (SQLite/JSONL): replaces the in-memory, gross, monotonic
    `_live_notional_spent` (`clob.py:230`) which resets on restart and never releases on cancel.
  - **Max-loss/day**: realized+unrealized P&L per UTC day (aligned to reward epochs,
    Sun 00:00–Sat 23:59 UTC); halt on breach. P&L definition = `cash + inventory·mid`
    (as in `forwardtest/runner.py`).
  - **Kill-switch**: file/flag/endpoint → cancel-all, flatten via the existing exit machinery,
    refuse new quotes. Must at minimum cancel-all even if flatten fails.
  - **Portfolio-level gross/net cap** (per-market inventory cap already exists in `loop.py`).
  - **Latency/error circuit breaker**: repeated 5xx/timeouts/WS drops → widen or pull quotes.
- Config knobs in `config/model.yaml` + `config/loader.py` (extend the existing env-override
  pattern): `max_daily_loss_usd`, `portfolio_gross_cap`, `kill_switch_path`,
  `max_consecutive_errors`.

**Risks:** a kill-switch that itself needs the network; daily-loss drift vs on-chain truth.
**Exit gate (fault-injection tests):** 5xx storm trips the breaker; simulated loss trips
daily-loss and flattens; kill-switch cancels+flattens within one tick; the ledger survives a
mid-session restart.

---

## Phase 4 — Market selection, allocation & real-time proposal detector

**Objective:** choose *what* to quote; react to dispute proposals in real time.

- New `execution/selection.py`: rank the live universe by λ_select / hazard (jump risk,
  `estimators/lambda_engine.py` + `estimators/hazard.py`) vs reward attractiveness
  (`rewards_daily_rate_usd`, `max_incentive_spread`, `reward_min_size` — already surfaced by
  `get_market_microstructure`). Prefer liquid mid∈[0.10,0.90] (the reward band); underweight
  high-λ_select names. Generalizes `runner.select_real_markets` from disputed-parquet to live.
- Capital allocation: split `MAX_CAPITAL_USDC` across selected markets weighted by
  reward-rate ÷ jump-risk; per-market caps feed the `RiskGovernor`.
- **Real-time proposal detector:** wire `webapp/backend/live.py` (keyless Polygon RPC dispute-tail
  scan) as the injected `proposal_detector` in `run_loop` — with the **reorg-confirmation guard** the loop
  docstring demands. This turns exit-on-risk from an always-False stub into the defining live
  behavior.

**Risks:** false/reorged proposal → needless exits forfeiting rewards (the gate already nets
forgone rewards, but spurious triggers still churn); capital over-concentration.
**Exit gate:** dry-run replay of a historical dispute window through the live detector path:
exits fire only after reorg confirmation and only when `E[jump loss] > forgone rewards + spread`.

---

## Phase 5 — Live session logging, monitoring & P&L dashboard

**Objective:** observability + the grant-facing live proof.

- Parameterize `forwardtest/session_log.py` (currently hardcodes `simulated: True` — the schema
  comment already anticipates live mode owning that flag). Live records real `order_id`, fees,
  builder-attributed volume.
- Lightweight monitoring exporter: heartbeat, open-order count, daily P&L, error rate, WS status;
  alerts on kill-switch / breaker / daily-loss.
- Webapp: read-only `/api/live/session` route (`webapp/backend/routes.py`) reading the live
  session log + on-chain attribution, and a `webapp/frontend/src/sections/LiveSession.tsx`
  section (modeled on `PaperSession.tsx`/`Ablation.tsx`): attributed volume, builder-fee revenue,
  weekly reward-epoch estimate, realized P&L, uptime, exits fired. **The dashboard stays
  write-free** — it reads the log the bot writes; it never imports the write path. Never fold a
  simulated reward score into live P&L (preserve `runner.py`'s discipline).

**Exit gate:** dashboard shows a live canary session whose attribution matches on-chain
`OrderFilled` events.

---

## Phase 6 — Staged rollout (metrics-gated)

| Stage | Capital | Scope | Gate to advance |
|---|---|---|---|
| 1. Paper-live shadow (runnable today) | $0 | `runner.py --mode paper-live`, 9–10 days | loop stable; dry-run reconciliation zero-divergence; sane exit firing on the real dispute feed |
| 2. Tiny canary | $20–50 | 1–3 liquid markets, 1 week | P&L within tolerance of paper-live prediction; zero orphaned orders; builder attribution confirmed on-chain; uptime ≥ target; kill-switch trips only from real risk, never bugs |
| 3. Scaled canary | $200–1k | 5–15 markets via the selector | net P&L ≥ neutral after fees; reward capture within model; daily-loss never breached; clean WS reconnects all week |
| 4. Production | grows | selector-driven | grow capital only while caps hold and attributed volume trends up |

**Guard:** do not let an underpowered canary week override the historical replay.
`forwardtest/ablation.py` (`MIN_DISPUTES_FOR_SIGNAL = 10`) stays a directional sanity check;
the replay-ablation remains the edge proof.

---

## Phase 7 — Grant-readiness package

Per DECISIONS.md #12 the program is continuous & permissionless — grants are **traction-gated**.
The submission stands on four legs, three of which already exist:

1. **Live attributed volume** — real `OrderFilled` events carrying our `builder` code
   (the *new* asset, from Phases 2–6).
2. **Edge proof** — `forwardtest/replay_ablation.py` over the 1,794 in-window disputes with the
   λ\*-sensitivity curve (publish the curve, not a tuned point — `config/model.yaml` mandates this).
3. **Public good** — the released dataset `dataset_release/polymarket-oov2-disputes-v1/`
   (1,848 disputes to chain head, 100% HF-joinable, CC-BY-4.0).
4. **Live product** — the quant terminal with the Phase-5 live panel, the keyless-RPC live dispute
   feed, and the testnet lifecycle proof (`contracts/PolyLambdaMarket.sol`,
   `scripts/e2e_onchain.py`).

**Exit gate:** a submission README linking all four + the reproducible green test suite, and the
grant application filed.

---

## Ranked go-live risks

1. **Order/fill state divergence** (Phase 2) — the loop trusts local `state.order_ids`; live must
   reconcile or it double-quotes / mis-tracks inventory.
2. **Ambiguous write failures leaving resting orders** — the reservation is kept fail-closed by
   design; the adapter must reconcile the maybe-resting order.
3. **Hot-key custody** — `WALLET_PRIVATE_KEY` in env is the bankroll's attack surface; use a
   dedicated low-balance operating wallet, funded just-in-time.
4. **In-memory monotonic capital cap** — unsafe as the only control; Phase 3 replaces it.
5. **Reward forfeiture from false exit triggers** — the reorg guard (Phase 4) is essential.
6. **Unverified mainnet shapes** — everything is fixture-tested due to the SNI block; Phase 0 is
   non-negotiable before real funds.
