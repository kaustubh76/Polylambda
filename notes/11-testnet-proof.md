# 11 · Testnet proof-of-life (2026-07-11)

> **Historical record.** This documents the original single-market on-chain proof (2026-07-11). That
> **public-demo market and its `/api/testnet/engine-quote|dispute|resolve` wire have since been
> removed** (2026-07-20) in favor of the continuous **keeper-managed fleet** — see
> [13-testnet-execution.md](13-testnet-execution.md), whose live smoke (deploy → keeper → fill →
> dispute-defense → kill-switch → reconcile) is the current proof. The lifecycle evidence below still
> holds for the contract itself; reproduce the contract lifecycle with `python scripts/e2e_onchain.py`.
>
> **Purpose.** Submission-ready evidence that the on-chain path is a **real Polygon Amoy
> implementation**, not paper validation: every claim below is a signed transaction or a live
> endpoint response captured on 2026-07-11, each independently checkable on Amoyscan or by
> curling the deployed app.

## 1. What was verified

| Surface | Result |
|---------|--------|
| Full on-chain market lifecycle (ephemeral market, 11 real signed txns) | **ALL ASSERTIONS PASSED** |
| Live demo market — engine-signed quote refresh from the real estimators | tx confirmed, `quoteTs` updated |
| Hosted app <https://polylambda-9lu2.onrender.com> — all 16 API endpoints | all HTTP 200, live data |
| Local backend (`uvicorn webapp.backend.main:app`) | same on-chain state as hosted |
| Offline suites | pytest **141/141** · indexer vitest 7/7 + `node --test` lifecycle 1/1 · frontend 20/20 + build |

Chain: **Polygon Amoy, chainId 80002**. Engine wallet `0xFc46DA4cbAbDca9f903863De571E03A39D9079aD`
(0.85 POL / 15.7 USDC at run time). Live demo market `0x1dBF7dA731e58C87B7e6644b719b84804F28b496`.

## 2. Full lifecycle e2e (`scripts/e2e_onchain.py`) — 11 real transactions

An **ephemeral** market was deployed (the live demo instance untouched) and driven through the whole
lifecycle with real signed transactions; every state transition and hardened guard was asserted.

Ephemeral market: [`0x2b602005Ef35C8C4aAaAddf871a410d4615b56AA`](https://amoy.polygonscan.com/address/0x2b602005Ef35C8C4aAaAddf871a410d4615b56AA)

| Step | Assertion proven | Tx |
|------|------------------|----|
| deploy | contract live, engine = deployer | [`0x5b5d…ee18`](https://amoy.polygonscan.com/tx/0x5b5d8709802fd62103ca68abaadfc6fc8e7a5af27082c9bdcc4a6e2ffd46ee18) |
| engine approve USDC | allowance set | [`0x0e46…f42f`](https://amoy.polygonscan.com/tx/0x0e46d300dedf3994fe3d4b96c7de4dcce5d245a80607027e3dd6180898f2f42f) |
| fund 1 USDC | `Collateral` event, escrow = 1.0 | [`0x844e…6439`](https://amoy.polygonscan.com/tx/0x844e13a7afc96f9b8ba6c375b9310bcf3ac59e2cdbc3fab746aa75fc69a6d439) |
| postQuote 0.60/0.64 | `QuotePosted`, `yesAsk == 640000` | [`0xfed4…7a40`](https://amoy.polygonscan.com/tx/0xfed4b86f2f201792c29cc2e2508e06f073d99fdf9b63e4df29eff8fd50c27a40) |
| fund throwaway user (POL) | user `0x5DAd…0105` gas-funded | [`0xb003…55e7`](https://amoy.polygonscan.com/tx/0xb0038f00ef404090083fb51e4526144d801594d537de2cc04acddf747acf55e7) |
| fund user 0.5 USDC | transfer confirmed | [`0xfa51…4630`](https://amoy.polygonscan.com/tx/0xfa51372b113891c3b8dc8e460ee77df1eba7aea82a97f257a7d624feaf094630) |
| user approve USDC | allowance set | [`0x162f…0245`](https://amoy.polygonscan.com/tx/0x162f25c64d5e02ba3d28493f0a4683d6596f1f407242eb445269c5ecbb90d245) |
| user buyYes 0.3 | `Traded`; shares 0.30; cost 0.192 USDC; escrow 1.192 | [`0x962f…8649`](https://amoy.polygonscan.com/tx/0x962fee900cc4661851927ce43e1ff6d3ccbc5d15be0299c6ce16644128768649) |
| guard: withdraw pre-resolve | static call reverts `"unresolved"` | *(eth_call, no tx)* |
| flagDispute (λ-defense) | `disputed == true` | [`0x8d1b…e018`](https://amoy.polygonscan.com/tx/0x8d1b5ba1bd2046d32a3e115924dfc5be9b80cda9449b67fa23f3765f7d1fe018) |
| guard: buyYes after dispute | static call reverts `"closed"` | *(eth_call, no tx)* |
| resolve YES | `resolved && yesWon` | [`0xdfd0…3b43`](https://amoy.polygonscan.com/tx/0xdfd018e2113dcb79cce3fa6adb8b160bb8b29770318b8ada26e215cdf4af3b43) |
| guard: double resolve | static call reverts `"resolved"` | *(eth_call, no tx)* |
| user redeem | `Redeemed` payout 0.30 USDC 1:1; shares zeroed | [`0xd5d6…17f3`](https://amoy.polygonscan.com/tx/0xd5d6a371dbe06e775e816dd9e4af6d90a97ad3a7fed39281397d3f495cec17f3) |

Full transcript: captured from `python scripts/e2e_onchain.py` (exit 0, `=== ALL ON-CHAIN
ASSERTIONS PASSED ===`).

## 3. Live demo market — engine quote refreshed through the hosted app

`POST https://polylambda-9lu2.onrender.com/api/testnet/engine-quote` made the **deployed backend** run the
real estimators (`services.score_market` → `estimate_lambda` + σ prior + `compute_quote`) and sign
`postQuote` with its engine wallet:

- tx [`0x89cf…7744`](https://amoy.polygonscan.com/tx/89cff77e4cd206cbfba3a32d9e6a9091cbcdd375cc7ad0f00ccaf0ad8d797744) — status 1, block 41953344, from `0xFc46…9AD`
- posted quote: **bid 0.5729 / ask 0.6629**, category politics, **λ_jump 0.0041 (41 bps), σ 0.0242 (242 bps)**
- on-chain `snapshot()` re-read afterwards: `quoteTs = 1783771742` (2026-07-11T12:09:02Z) — fresh.

This proves the hosted engine-signed write wire end-to-end: estimators → quote → signed tx → chain.

## 4. Hosted app endpoint sweep (all HTTP 200, 2026-07-11)

`health · overview · baserates · hazard · disputes · sigma · proposers · quote-curve · recon ·
live/status · live/disputes · testnet/status · testnet/market · testnet/events · testnet/position ·
ablation` — all 200 with live payloads. Notables:

- `/api/testnet/status` → `chain_id 80002`, `engine_ready: true`, market `0x1dBF…8b496`.
- `/api/testnet/events` → the paged-`eth_getLogs` activity feed returned the *fresh* `QuotePosted`
  at block 41953344 (the feed survives market aging).
- `/api/live/status` → reachable, ~58 ms latency. **(As captured on 2026-07-11 this was still the
  hosted Envio HyperIndex. That endpoint has since been retired: post-pivot the feed reports
  `source=rpc` — keyless Polygon RPC. Re-running this sweep today returns `source=rpc`, not Envio;
  everything else below reproduces unchanged.)**
- `/api/recon` → `pass_rate 1.0, eligible 27,238, matched 27,238` (matches `stats.json`; deterministic
  since the recon scan was ordered).
- SPA `GET /` serves the built dashboard (`PolyLambda — dispute-aware market making`).

A local `uvicorn webapp.backend.main:app` run served the identical on-chain state
(bid 0.5729 / ask 0.6629, same `quote_ts`), confirming hosted and local read the same chain.

## 5. How to re-verify (any time)

```bash
# on-chain lifecycle proof (deploys an ephemeral market; needs ENGINE_PRIVATE_KEY in .env)
python scripts/e2e_onchain.py

# hosted app
curl https://polylambda-9lu2.onrender.com/api/health
curl https://polylambda-9lu2.onrender.com/api/testnet/status

# live market snapshot straight from the chain (no backend)
python - <<'EOF'
from web3 import Web3; import json
w3 = Web3(Web3.HTTPProvider("https://polygon-amoy.drpc.org"))
art = json.load(open("webapp/backend/market.json"))
print(w3.eth.contract(address=art["address"], abi=art["abi"]).functions.snapshot().call())
EOF
```
