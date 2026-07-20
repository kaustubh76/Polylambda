# 13 — Testnet execution mode (the continuous engine)

**What it is:** `MODE=testnet` runs the REAL production trading loop — estimators → Avellaneda-
Stoikov quote → reward-aware exit gate, byte-identical `execution/loop.py` — against a **fleet of
PolyLambdaMarket contracts on Polygon Amoy**, with every order an **engine-signed transaction** and
every fill a decoded on-chain `Traded` event. It replaces paper mode as the operative forward-test
vehicle. Nothing is simulated: session logs stamp `simulated: false` and fills carry tx hashes.

The mainnet CLOB plane is untouched: `execution/clob.py` stays behind `_require_live_gate()`, and
`run_loop(mode="testnet")` *refuses* to construct a signer implicitly — the keeper builds and
injects the adapter explicitly.

## Module map

| Module | Role |
|---|---|
| `execution/testnet_chain.py` | Fleet registry (`webapp/backend/markets.json`), `AmoySigner` (chain-80002 guard, nonce lock + race retry, gas accounting), `ChainReader` |
| `execution/testnet_clob.py` | The adapter: 7-method duck protocol → `postQuote`/`Traded` mapping |
| `execution/risk.py` | `RiskGovernor`: kill-switch file, max-loss/day, tx+gas budgets, error breaker, JSONL ledger (restart-safe) |
| `execution/proposal_feed.py` | `ConfirmedProposalDetector`: keyless-RPC dispute tail, ≥30-block confirmation guard, manual trigger file |
| `execution/testnet_keeper.py` | The runtime: MarketState fleet from real estimators, session logging, `flagDispute` wiring, CLI + background thread |
| `scripts/deploy_fleet.py` | Deploy N markets, fund, initial estimator quote, append to the registry |

## The order-model → single-quote mapping

The contract has **no resting order book** — one two-sided engine quote per market, users take
against it. So:

- `place(BUY,bid,s)` + `place(SELL,ask,s)` (the loop places pairs back-to-back) buffer and
  **collapse into ONE `postQuote(bid, ask, maxTrade)`** at the next `step()`.
- One-sided quotes (inventory-cap gating) map the missing side to an economically dead price
  (`bid=tick` / `ask=1-tick`) — the contract requires `bid < ask`.
- `maxTrade = min(buy_size, sell_size, cap, escrow − totalYes)` — the **solvency bound**: a
  worst-case YES win stays redeemable 1:1, so escrowed collateral (~1 USDC/market) is the hard
  loss bound of any standing quote, even if the keeper dies (no on-chain quote expiry).
- `cancel()` is bookkeeping only. Fills: user `buyYes` = engine **SELL** at ask; user `sellYes` =
  engine **BUY** at bid; price recovered exactly as `usdc/size` from the event; fills are read only
  `confirmations` (3) blocks below head.

## Debounce math (the gas guard)

A buffered quote signs only if `|Δbid| ≥ 0.005` or `|Δask| ≥ 0.005` or maxTrade changed or the
standing quote is older than `max_quote_age_s` (900 s). At 60 s ticks × 2 markets, naive reposting
would be ~2,880 tx/day (~7 POL); the debounce bounds it to ~100–200 tx/day (~0.3–0.6 POL), and
`max_gas_pol_per_day` (0.6) is the RiskGovernor's hard stop. All knobs in `config/model.yaml`.

## Honesty notes

- **Amoy has no Liquidity Rewards program**, so `TESTNET_MICRO` sets all reward params to 0 →
  `forgone_rewards ≡ 0` and the exit gate honestly reduces to `E[jump loss] > spread cost`.
- **`reduce_fraction` is forced to 0.0**: the exit's taker-reduce has no counterparty on this
  contract (the engine can't trade against its own quote). The defense IS `flagDispute` (halts
  buys) + light re-quote; inventory/cash stay exactly reconcilable against `Traded` events.
- **Only proposal-triggered exits sign `flagDispute`.** λ-only exits (elevated hazard, no detected
  dispute) go defensive/light but must not burn the market — `flagDispute` is irreversible until
  `resolve`, and with zero rewards the λ gate fires readily.
- **No mock/demo mode.** The keeper only ever signs a `postQuote`/`flagDispute` built from the real
  estimators + real on-chain state; an estimator failure raises and signs nothing (never a fabricated
  value). `scripts/deploy_fleet.py` fails loud if the estimators are down rather than seeding a
  made-up quote, and the equity/risk mark never substitutes a placeholder. The legacy single-market
  public-demo (routes, `market.json`, the `LiveTestnet` dashboard section) has been removed — the
  registry and dashboard carry **keeper-managed fleet markets only**.

## Runbook

```bash
# 1. deploy a fleet (needs ENGINE_PRIVATE_KEY in .env, POL + test-USDC on the engine wallet)
.venv/bin/python scripts/deploy_fleet.py --n 2 --categories politics,crypto

# 2. run the keeper (local dev harness)
.venv/bin/python -m execution.testnet_keeper --ticks 10 --interval 60

# 3. continuous (Render): KEEPER_AUTOSTART=1 starts the background thread in the webapp process
#    (one process with the demo routes = the signer nonce lock is global). The 6h GH cron
#    (onchain-keepalive.yml) POSTs /api/testnet/keeper/run as a spun-down/crash watchdog.

# demo the full defense chain on demand (real Polymarket disputes are rare):
echo <tracked-conditionId> >> .data_cache/risk/DISPUTE_TRIGGERS
# → next tick: exit record → REAL flagDispute() on the Amoy twin → buys halt → light re-quotes

# kill-switch (cross-process; also POST /api/testnet/kill and the dashboard KILL button):
touch .data_cache/risk/KILL      # halt all signing within one tick
rm .data_cache/risk/KILL         # resume
```

Sessions land in `.data_cache/sessions/session-testnet-YYYYMMDD.jsonl` (one continuous file per
UTC day across bursts); `python -m forwardtest.ablation <path>` reads them unchanged. Risk ledger:
`.data_cache/risk/ledger-YYYYMMDD.jsonl`. Dashboard: the **Fleet & keeper** section (`/#fleet`)
shows fleet snapshots, keeper/risk status, engine balances with a POL low-water warning, and the
guarded KILL button.

## Known limits

- Tick cadence on the free Render tier is best-effort (spin-down between keepalives); the session
  log's wall-clock `t` gaps make it auditable, and the cron burst restores freshness.
- A dead keeper leaves a standing quote (no on-chain expiry) — bounded by `maxTrade` and escrow.
- `flagDispute` on a false-positive confirmed dispute permanently halts buys on that fleet market
  until `resolve` — acceptable on testnet; the 30-block guard filters reorg ghosts.
