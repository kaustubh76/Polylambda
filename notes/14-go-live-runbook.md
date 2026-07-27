# 14 — Go-live runbook (read-only monitor → signing engine)

**What it is:** the deployed dashboard is a faithful **read-only monitor** of the Amoy fleet; the
keeper thread that actually *signs* `postQuote`/`flagDispute` does not run until three operator gates
are satisfied on the running service. This note is the one-time checklist to flip it live, and — the
important part — how to **prove** each gate with `scripts/preflight_golive.py` so go-live can't fail
silently. It does not re-explain the engine (see [13](13-testnet-execution.md)), autostart wiring
(`webapp/backend/main.py:66-71`), the faucet step ([08](08-entrypoints-runbook.md#L52)), or the
roadmap framing ([ROADMAP.md](../ROADMAP.md)); it ties them together and gates them behind a checker.

## The preflight checker

`scripts/preflight_golive.py` is **read-only** — it never signs and never prints the private key
(only the derived engine address). It reuses the production `engine_key()`, `make_w3()`,
`ChainReader.balances()`, `load_fleet()` and the `min_pol_balance` floor from `config/model.yaml`.

```bash
# local: the real wallet/chain/fleet from your machine (reads .env + Amoy RPC)
.venv/bin/python scripts/preflight_golive.py
# remote: the ACTUAL deployed service, via GET {url}/api/testnet/keeper (no shell needed)
.venv/bin/python scripts/preflight_golive.py --remote https://polylambda-9lu2.onrender.com
```

Each prints per-gate `OK`/`FAIL`/`WARN` and exits `0` only when every gate passes. `WARN` rows are
advisory (e.g. `KEEPER_AUTOSTART` is meaningless on your laptop — the **running service** is the
source of truth, so trust `--remote` for it).

## The three gates

| # | Gate | Where to set | How it fails if missing |
|---|---|---|---|
| 1 | `ENGINE_PRIVATE_KEY` | Render dashboard **secret** (`render.yaml` has it `sync:false`) | `engine_ready:false` → keeper runs read-only, signs nothing |
| 2 | **Engine wallet funded** | Amoy **faucet** → wallet `0xFc46DA4cbAbDca9f903863De571E03A39D9079aD` | POL < `min_pol_balance` (0.02) → governor halts *"engine out of gas"*; 0 test-USDC → nothing to escrow/quote |
| 3 | `KEEPER_AUTOSTART=1` | Render dashboard **env** | keeper thread never starts on boot → `autostart:false, running:false, n_markets:0` |

**Gate 3 is the non-obvious one:** `render.yaml:17` already declares `KEEPER_AUTOSTART: "1"`, but the
running service still reports `autostart:false` — the blueprint is not applied to the live instance
(same drift as `plan: starter`). It must be set on the **service's Environment tab in the Render
dashboard** (or the blueprint re-synced), not just left in `render.yaml`.

As of this writing the live `--remote` check shows gate 1 **already done** (`engine_ready:true`) and
gates 2 + 3 outstanding (wallet at ~0.012 POL < 0.02 floor; `autostart:false`). So go-live is:
**faucet the wallet, then flip `KEEPER_AUTOSTART=1` in the dashboard.**

## Runbook

```bash
# 0. PROVE what's missing (this is the whole point — no guessing)
.venv/bin/python scripts/preflight_golive.py --remote https://polylambda-9lu2.onrender.com

# 1. Gate 1 — ENGINE_PRIVATE_KEY: Render dashboard → service → Environment → add the secret
#    (value from the gitignored .env; NEVER commit it). Verify: remote check → engine_ready OK.

# 2. Gate 2 — fund the engine wallet on Amoy (POL for gas + a little test-USDC for collateral):
#    faucet POL to 0xFc46DA4cbAbDca9f903863De571E03A39D9079aD  (e.g. alchemy.com/faucets/polygon-amoy)
#    test-USDC token: 0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582  (see .env.example / notes/06)
#    Verify:  .venv/bin/python scripts/preflight_golive.py   → "engine POL >= floor" OK

# 3. Gate 3 — KEEPER_AUTOSTART: Render dashboard → Environment → set KEEPER_AUTOSTART=1 → redeploy.
#    (render.yaml already declares it, but the live service must have it applied.)

# 4. CONFIRM go-live — both must PASS (exit 0):
.venv/bin/python scripts/preflight_golive.py
.venv/bin/python scripts/preflight_golive.py --remote https://polylambda-9lu2.onrender.com
#    Then GET /api/testnet/keeper shows running:true · autostart:true · n_markets~6 · engine.pol>=floor,
#    and the dashboard λ-edge badge flips from "last on-chain session" → green "live".
```

## Halt / rollback

- `touch .data_cache/risk/KILL` — the cross-process kill-switch silences all signing within one tick
  (ticks keep running); `rm` it to resume. Also `POST /api/testnet/kill` / the dashboard KILL button.
- The `RiskGovernor` (`execution/risk.py`) **auto-halts** on low gas (< `min_pol_balance`), the daily
  loss cap, tx/gas budgets, or the error breaker — a low-fuel or misbehaving engine stops itself.
- To stop autostarting entirely, unset `KEEPER_AUTOSTART` in the dashboard and redeploy.

## Honesty & security notes

- The checker is strictly read-only: it derives the address via `AmoySigner(w3).address` and reads
  balances; it never calls `.send()` and never emits the key. A cold-starting `--remote` host is
  retried and reported as "waking", never a traceback.
- Secrets (`ENGINE_PRIVATE_KEY`, `HF_ACCESS_TOKEN`, Render API key) live **only** in the gitignored
  `.env` (mode 0600) and the Render dashboard — verified never committed to git history. `.env.example`
  is the committed template. Never paste the private key into a doc, commit, or the runbook.

## Known limits

- Free-tier spin-down: even live, tick cadence is best-effort between keepalives; the `*/15` GH cron
  (`onchain-keepalive.yml`) POSTs `/api/testnet/keeper/run` as an idempotent self-heal. For an
  always-on engine, move the service off the free plan.
- `engine.pol`/`engine.usdc` only appear in `/api/testnet/keeper` once the keeper has built its clob
  (after the first tick); before that the remote check reports the balance as "not reported" (FAIL),
  which is correct — a not-yet-running engine is not go-live.
