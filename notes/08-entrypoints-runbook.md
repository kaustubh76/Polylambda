# 08 · Entry points & runbook

> **Source of truth.** The `if __name__ == "__main__"` / `main()` blocks in each module; `Dockerfile`
> CMD; `fly.toml` / `render.yaml`. There is **no** `setup.py` / console_scripts / Makefile — everything
> runs via `python -m <module>`, `uvicorn`, or the deploy image.

## 1. The engine / research pipeline

| Task | Command |
|------|---------|
| **Forward-test (paper)** | `python -m forwardtest.runner --mode paper --ticks 20 --markets 4 --source synthetic` |
| Forward-test on real disputed markets | `python -m forwardtest.runner --mode paper --source data --hazard` |
| Paper-live (real book/tape, simulated fills) | `python -m forwardtest.runner --mode paper-live --source data` |
| **Primary edge proof (replay-ablation)** | `python -m forwardtest.replay_ablation` (env `GRAPHQL_URL`, `DATA_SOURCE`) |
| Live λ-ON vs λ-OFF reader (sanity) | `python -m forwardtest.ablation <session.jsonl>` |
| Train the structural hazard model | `python -m estimators.hazard [--matched] [--graphql-url URL] [--path P]` |
| Quote sanity print | `python pricing/quote.py` |

Session logs land in `.data_cache/sessions/session-{mode}-s{seed}-n{ticks}.jsonl`; the trained hazard
model in `.data_cache/hazard_model.json`.

## 2. Data backbone

| Task | Command |
|------|---------|
| Rebuild dispute labels (keyless RPC, no Docker) | `python -m data.disputes` |
| Rebuild the NegRisk conditionId map | `python -m data.negrisk_map` |
| Reproduce the DATASET.md numbers | `python -m data.dossier [--full]` |
| Re-export the released dispute dataset | `python -m data.export_disputes` |
| Recalibrate `kappa_loss` | `python -m data.calibrate` |
| **Recon gate** (indexed outcome == HF payout) | `python -m recon.check` (env `GRAPHQL_URL`, `POLYGON_RPC_URL`) |

## 3. Webapp (FastAPI + React)

| Task | Command |
|------|---------|
| Build the frontend cache artifacts | `python -m webapp.backend.precompute` |
| Run the backend (serves API + built SPA) | `uvicorn webapp.backend.main:app --host 0.0.0.0 --port 8000` |
| Frontend dev server (Vite) | `cd webapp/frontend && npm run dev` (proxies `/api` to `:8000`) |
| Frontend build | `cd webapp/frontend && npm run build` → `dist/` |

Docker CMD: `uvicorn webapp.backend.main:app --host 0.0.0.0 --port ${PORT:-8000}` (2-stage image builds
the SPA then runs uvicorn). Health check: `GET /api/health`.

## 4. On-chain testnet (Polygon Amoy)

Order matters — generate & fund the wallet first, then deploy.

| Task | Command |
|------|---------|
| 1. Generate the burner engine wallet (writes `.env`) | `python scripts/gen_engine_wallet.py` |
| 2. Fund the printed address with POL (faucet) | *manual* |
| 3. Deploy a keeper-managed fleet (writes `markets.json`) | `python scripts/deploy_fleet.py --n 2` |
| 4. Run the testnet keeper (continuous engine) | `python -m execution.testnet_keeper --ticks 10 --interval 60` |
| 5. Full on-chain lifecycle e2e (ephemeral market) | `python scripts/e2e_onchain.py` |

Needs `ENGINE_PRIVATE_KEY` (from step 1), `AMOY_RPC_URL`, optionally `AMOY_GAS_GWEI` (default 30),
`ENGINE_COLLATERAL_USDC`, `ENGINE_MAX_TRADE`.

## 5. Deploy

- **Fly.io:** `fly deploy` (uses `fly.toml` + `Dockerfile`); set the secret once with
  `fly secrets set ENGINE_PRIVATE_KEY=…`.
- **Render:** push — the `render.yaml` Blueprint autodeploys; set `ENGINE_PRIVATE_KEY` (marked
  `sync:false`) in the dashboard. Without it, reads + user-signed trades still work; only engine controls
  go offline.

## 6. Tests

```
pytest -q                 # the full offline Python suite (no network, no DuckDB)
cd indexer && npm test    # legacy/optional Envio indexer: vitest (lib parity) + node --test (lifecycle)
cd webapp/frontend && npm test   # frontend Vitest (export/format/testnet/urlState + smoke)
```

See [09-testing.md](09-testing.md) for what each file covers.
