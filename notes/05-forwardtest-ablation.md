# 05 · Forward-test & the edge proof

> **Source of truth.** `forwardtest/{runner,session_log,ablation,replay_ablation}.py`; result narrative in
> `../METHODOLOGY.md` / `../DATASET.md` / `../LEDGER.md`. Mirrors `quant-implementation-full.excalidraw`
> Panels H (thesis test) + ⑤ EDGE PROOF (Panel L).

## 1. Two kinds of "test" — don't confuse them

| | `forwardtest/runner.py` (+ `ablation.py`) | `forwardtest/replay_ablation.py` |
|--|--|--|
| What | Live/paper forward-test loop, then a λ-ON vs λ-OFF reader | Historical counterfactual over real disputes + matched controls |
| Power | **Underpowered by design** (too few live disputes) — a sanity check | The **primary, powered edge proof** |
| Data | Synthetic (`PaperClob`) or real-read (`PaperLiveClob`) | HF fills ⋈ dispute labels on `conditionId`, net of rewards |
| Verdict | directional delta + explicit `underpowered` caveat | ranked arm P&L + Sharpe across a `lambda_star` grid |

## 2. Paper / paper-live harness (`forwardtest/runner.py`)

`run(mode, ..., source="synthetic"|"data", hazard=False)` builds `MarketState`s (synthetic universe or
real disputed-market rows with REAL λ + σ prior), runs `execution.loop.run_loop` for both arms
(`lambda_on` / `lambda_off`), and streams a JSONL session log.

- **P&L = cash + inventory·mark only.** The simulated liquidity-reward score (`sim_reward_score`) is
  reported **separately and never folded into P&L** (`runner.py:229` — "pnl excludes sim_reward_score by
  construction"; `execution/loop.py:122`). This keeps rewards from flattering the edge.
- Output → `.data_cache/sessions/session-{mode}-s{seed}-n{ticks}.jsonl`.
- `paper-live` fills use the queue-honest `ConservativeFillModel` (rests behind all same-price depth;
  every fill tagged `queue_model="conservative"`), so paper-live fills are pessimistic, not optimistic.

## 3. Session-log schema (`forwardtest/session_log.py`)

Every record: `{t, type, mode, simulated: true, ...}`. Record types:
`session_start · tick · quote · fill · exit · dispute_witnessed · session_end`. `exit` records carry
`forgone_rewards`, `haircut_paid`, and inventory before/after. `read()` is crash-tolerant (skips a torn
trailing line).

## 4. The primary edge proof — replay-ablation (`forwardtest/replay_ablation.py`)

For each historical disputed market (+ `control_ratio=3` matched non-disputed HF controls), replay the
quoting policy and score P&L **net of forgone rewards** across the arms:

- **Arm A — diffusion-only** (always hold): the naive constant-spread maker.
- **Arm B — `lambda_jump`** (reward-aware **surgical exit** via `execution.loop.should_exit`).
- **Arm C — `lambda_select`** (blanket **avoidance** of dispute-prone markets).
- **Arm B_hazard** — Arm B but with the structural hazard λ (`tests/test_replay_hazard.py` guards it).

Reported per `(arm, lambda_star)`: `AblationResult(pnl_net_of_rewards, sharpe, avoided_loss,
forgone_rewards, n_disputes, n_controls)`. A pre-registered `power_calc(...)` states the detectable effect
size up front. Grid: `[0.0005, 0.001, 0.002, 0.005, 0.01]`.

**Result — the ordering `lambda_jump > diffusion > lambda_select` at low λ\*, reproduced at every
scale.** Absolute PnL is **not comparable across runs** (it scales with how many sampled controls have
a joinable fill tape that run); the invariant is the ordering and the λ\*-curve shape:

| Run | at λ\*=0.0005 (pnl_net / sharpe) |
|-----|----------------------------------|
| 2022–23 slice (56 disputed + 223 controls; the `../METHODOLOGY.md` table) | `lambda_jump 1536.8 (0.183) > diffusion 1408.6 (0.167) > lambda_select 620.6 (0.112)` |
| NegRisk-2024 liquid slice (26 + 132) | `lambda_jump 1888.7 (0.375) > diffusion 1882.2 (0.373) > lambda_select 0.0` |
| Published full-scale (1,409 + 2,856; the dashboard serves this from `webapp/backend/constants.py:ABLATION_PUBLISHED`) | `lambda_jump 46,975 (0.334) > diffusion 40,065 (0.274) > lambda_select 0.0` |
| 2026-07-11 re-verification (1,409 + 741 with fills; **pinned** in `../forwardtest/results/replay_ablation_2026-07-11.json`) | `lambda_jump 27,668 (0.37) > diffusion 20,746 (0.26) > lambda_select 0.0` |

**Conclusion:** the edge is the **surgical jump-exit (B)**, not blanket market-selection avoidance (C) —
"blanket avoidance destroys it." If Arm B does not beat Arm A net of forgone rewards, the λ term is
theatre and should be dropped (Panel H gate).

> **Caveat for developers:** the latest full run **is pinned** in
> `../forwardtest/results/replay_ablation_2026-07-11.json` (+ raw log alongside), but there is still
> **no regression test** over these numbers — `tests/test_ablation.py` only exercises the live reader
> with synthetic fixtures. If you change the estimators or the exit gate, re-run
> `python -m forwardtest.replay_ablation`, pin a fresh dated JSON, and update `../METHODOLOGY.md` §4 by
> hand. Expect absolute PnL to move with control fill-tape coverage (`n_controls_with_fills` in the
> artifact); what must NOT change is the arm ordering at low λ\*.

## 5. The thesis-test gate (Panel H)

The Day-6 gate: **model-driven P&L positive AND the λ term beats a naive constant-spread maker**, net of
forgone rewards, on **historical replay** (live has too few disputes). No lookahead, absolute. If P&L sim
lacks fill/book data, the fallback is to validate (a) σ calibration and (b) the λ edge (already done),
then forward-test P&L.
