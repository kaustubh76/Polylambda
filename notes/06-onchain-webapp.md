# 06 · On-chain market + webapp surface

> **⚠️ Superseded in part (2026-07-20).** The single-market **public-demo surface described below has
> been removed**: the `/api/testnet/{status,market,position,events,engine-quote,dispute,resolve}`
> routes, `webapp/backend/market.json`, `chain.py`'s demo write/read functions, the `LiveTestnet`
> dashboard section, and the `MARKET_ADDRESS` env. The on-chain surface is now the **keeper-managed
> fleet only** — deploy via `scripts/deploy_fleet.py` (→ `markets.json`), run via
> `execution/testnet_keeper.py`, read via `chain.py:fleet()` (`/api/testnet/fleet`), shown in the
> **Fleet & keeper** dashboard section. See [13-testnet-execution.md](13-testnet-execution.md) for the
> current architecture. The sections below are retained as historical context for the demo era.
>
> **Source of truth.** `contracts/PolyLambdaMarket.sol`, `execution/testnet_*.py`,
> `webapp/backend/*.py`, `webapp/backend/markets.json`, `webapp/frontend/src/*`, `scripts/*.py`,
> `fly.toml` / `render.yaml` / `Dockerfile` / `.env.example`.

## 1. Current deployment (Polygon Amoy, chainId 80002)

From `webapp/backend/market.json` (the artifact the deploy script writes, loaded by `chain.py`; also set
as env in `fly.toml` / `render.yaml`):

| Thing | Address |
|-------|---------|
| **PolyLambdaMarket** | `0x1dBF7dA731e58C87B7e6644b719b84804F28b496` (deployed block 41839995) |
| **Engine wallet** (MM + admin) | `0xFc46DA4cbAbDca9f903863De571E03A39D9079aD` |
| **Test USDC** (Circle Amoy) | `0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582` |

> This is a **testnet demo**. Mainnet CLOB trading stays hard-gated (`execution/clob._require_live_gate`).
> The on-chain path is deliberately *separate* and testnet-guarded by `chain_id == 80002`.

## 2. The contract (`contracts/PolyLambdaMarket.sol`)

A minimal on-chain binary (YES-outcome) market where the **PolyLambda engine wallet is the market maker**.
Units: prices are 6-decimal fractions of 1 USDC (`0..1e6`); 1 YES share redeems 1 USDC on a YES win.

- Roles: `address public immutable engine` (= deployer), enforced by `modifier onlyEngine`.
- State: live quote (`category`, `lambdaBps`, `sigmaBps`, `yesBid`, `yesAsk`, `maxTrade`, `quoteTs`),
  lifecycle flags (`disputed`, `resolved`, `yesWon`), positions (`yesShares`, `totalYes`).
- **Engine-only** functions: `fund(amount)`, `postQuote(bid, ask, maxTrade, cat, lam, sig)`,
  `flagDispute()` (the λ-defense — halts new buys), `resolve(yesWon)` (one-shot), `withdraw(amount)`
  (guarded `require(resolved)` so escrow backing live holders can't be drained early).
- **User** functions: `buyYes(size)` (pays `size·ask/1e6` USDC, capped by `maxTrade`, blocked once
  disputed/resolved), `sellYes(size)`, `redeem()` (1 share → 1 USDC iff `yesWon`).
- View: `snapshot()` returns everything the BE+FE render.
- Events: `QuotePosted · Traded · Disputed · Resolved · Redeemed · Collateral` — these drive the on-chain
  activity feed.

## 3. Two on-chain write paths (the key architecture point)

1. **User-signed (client-side).** `approve / buyYes / sellYes / redeem` are signed in the user's own
   wallet via **viem** (`webapp/frontend/src/lib/wallet.tsx`) directly against the contract. **No server
   keys.** The backend only *reads* the resulting position/events. Hard gas limits are set (approve 90k,
   market 350k) to dodge Amoy's gas cap + MetaMask over-estimation.
2. **Engine-signed (server-side).** `postQuote / flagDispute / resolve` are signed by the **backend
   engine wallet** in `webapp/backend/chain.py:_send()` using `ENGINE_PRIVATE_KEY`. Testnet-guarded
   (refuses unless the key is set **and** `chain_id == 80002`); a nonce lock serializes engine txns; POA
   middleware + low explicit EIP-1559 fees (Amoy base fee ≈ 0).

**The wire to the engine:** `chain.post_quote()` → `services.score_market()` runs the **real estimators**
(`estimate_lambda`, σ via `category_price_prior`, `pricing.quote.compute_quote`) → converts to 6-dec
bid/ask + λ/σ in bps → signs `market.postQuote(...)`. So on-chain quotes are literally produced by the
same engine as the paper research dashboard.

## 4. The webapp

**One Docker image, one process.** A FastAPI backend (`uvicorn webapp.backend.main:app`) that also serves
the built React/Vite SPA from the same origin. Paper-mode only — the gated CLOB write path is never
imported.

### Backend (`webapp/backend/`)
- `main.py` — app assembly; mounts `/assets` + a catch-all SPA route; lifespan warms caches and installs
  "offline DI" so estimators run network-free.
- `routes.py` — `APIRouter(prefix="/api")`. Two families:
  - **Research/paper:** `/overview`, `/baserates`, `/lambda/score`, `/session/run`, `/ablation`,
    `/hazard`, `/disputes`, `/recon`(+`/recon/live`), `/sigma`, `/proposers`, `/quote-curve`, and the
    indexer `/live/status` + `/live/disputes`.
  - **Testnet on-chain:** reads `GET /testnet/{status,market,position,events}`; engine-signed writes
    `POST /testnet/{engine-quote,dispute,resolve}`; `/api/health` for deploy health checks.
- `chain.py` — the Amoy chain layer (reads via cached web3; `events()` pages `eth_getLogs` backward in
  ~9k-block chunks so the feed survives log-window caps; uses drpc because official `rpc-amoy` rejects log
  ranges).
- `services.py` — the engine bridge (`score_market()` imports the real estimators/pricing/loop).
- `live.py` — the live OOv2 dispute feed, **source-agnostic**: Envio GraphQL *only if*
  `INDEXER_GRAPHQL_URL`/`HOSTED_GRAPHQL_URL` is set **and** reachable **and** fresh → **keyless Polygon
  RPC (the default)** → offline. There is no baked-in endpoint (the old free dev deploy ended), so an
  unset env goes straight to RPC. Liveness is gated on the **chain head**, not the latest dispute
  (sparse disputes = LIVE-but-quiet, reported honestly). 3s status micro-cache; the heavy RPC tail scan
  runs on a background daemon thread behind a long TTL so it never blocks a request.
- `precompute.py` — builds `.data_cache/webapp/*.json` cache artifacts.

### Frontend (`webapp/frontend/`, React + Vite + TS + Tailwind + framer-motion + viem)
- `src/App.tsx` — single-page, hash-anchored sections; providers `Theme / Wallet / Toast / LiveStatus`;
  header LivePill (indexer latency), PendingIndicator (in-flight tx), AccountMenu (connect / switch Amoy),
  ⌘K palette.
- `src/sections/` — `Hero`, **`LiveTestnet`** (the on-chain trading UI), `BaseRates`, `ScoreMarket`,
  `PaperSession`, `Ablation`, `HazardCard`, `Disputes`, **`LiveIndexer`** (the live dispute feed —
  keyless-RPC-sourced by default; the copy is source-aware), `Recon`,
  `SigmaSurface`.
- `src/lib/wallet.tsx` — viem + injected-provider context (public reads + user-signed writes).
- `src/lib/testnet.ts` — Amoy constants, test-USDC, faucet URLs, the user-side `MARKET_ABI`
  (engine-only fns deliberately excluded), EIP-3085 add-chain params.
- `src/api/client.ts` — typed fetch client, `BASE='/api'` (same origin).

## 5. Scripts (`scripts/`)

- `gen_engine_wallet.py` — generates the Amoy burner engine wallet, writes `ENGINE_PRIVATE_KEY` +
  `ENGINE_ADDRESS` to the gitignored `.env` (chmod 600), prints only the address to fund. Idempotent.
- `deploy_market.py` — compiles (solcx 0.8.24) + deploys `PolyLambdaMarket` to Amoy, funds a little
  collateral, posts an initial quote (`bid 0.60 / ask 0.64, λ 183bps, σ 470bps, category "politics"`),
  and writes `webapp/backend/market.json`. POA middleware; asserts `chain_id == 80002`; low explicit gas
  (`AMOY_GAS_GWEI` default 30). Needs `ENGINE_PRIVATE_KEY`, funded engine address (POL).
- `e2e_onchain.py` — deploys an **ephemeral** market (the live demo is untouched) and drives
  `fund → postQuote → user approve+buyYes → guard reverts → flagDispute → resolve → redeem` with real
  signed txns, asserting every transition. Prints Amoyscan links.

## 6. Deploy targets

**Live deployment (the submission demo): <https://polylambda-9lu2.onrender.com>** (Render). Verified
2026-07-11: `/api/health` ok; `/api/testnet/status` reports chain 80002, `engine_ready: true`, the
market address above, and the live on-chain quote.

> **Cold-start note (free tier).** The instance sleeps after ~15 min idle; during wake-up Render's
> gateway returns **502** until uvicorn binds. Hardening (2026-07-11): the backend lifespan warms
> caches in a background thread so the port binds immediately; the frontend retries one-shot GETs
> through 502/503/504 with backoff (`client.ts req()`, POSTs never retried) and the three pollers
> (`usePoll`) skip overlapping ticks and stretch to ~4× interval while the backend is unreachable;
> `.github/workflows/keepalive.yml` pings `/api/health` every 10 min to keep the instance warm
> (a red Actions run = the demo is down).

Both targets use the **same Docker image** (2-stage: node:20 builds the SPA → python:3.12 runs uvicorn on
port 8000; health check `GET /api/health`).

- **`fly.toml`** — app `polylambda`, region `iad`, `internal_port=8000`. `[env]`: `MODE=paper`,
  `POLYGON_RPC_URL` (tenderly — the live dispute plane), `AMOY_RPC_URL=https://polygon-amoy.drpc.org`,
  `AMOY_USDC_ADDRESS`, `MARKET_ADDRESS=0x1dBF…8b496`. `ENGINE_PRIVATE_KEY` via `fly secrets set`.
- **`render.yaml`** — Render Blueprint, same non-secret env + `ENGINE_PRIVATE_KEY` with `sync:false`.
  Without the key: reads + user-signed trades still work; only engine controls (re-quote/dispute/resolve)
  go offline.

> **`INDEXER_GRAPHQL_URL` is deliberately NOT set** in `fly.toml`, `render.yaml`, or the `Dockerfile`
> (commit `c7359e9` — "stop baking the dead Envio URL into the image env"). Unset means the live feed
> goes straight to keyless RPC. A **stale** indexer URL is worse than an empty one: it costs a reachability
> probe before falling back. Don't re-bake one.
