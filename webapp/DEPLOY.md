# Deploying the PolyLambda dashboard

The whole app (FastAPI backend + React SPA) ships as **one Docker image** that serves the SPA and the
`/api` routes from a single `uvicorn` process. Paper-mode only — no secrets, no live-trade path.

The image is self-contained: the frontend is built inside it, the engine + backend are copied in, and
the small model/prior artifacts are bundled from `webapp/deploy/cache/` (the 470MB+ `.data_cache/` is
**not** shipped — the disputes explorer reads the committed `dataset_release/` parquet).

## Build & run locally

```bash
docker build -t polylambda .
docker run --rm -p 8000:8000 polylambda      # → http://localhost:8000
```

`$PORT` is honored if set (Railway/Render/Cloud Run inject it); it defaults to `8000`.

## Deploy to a host

Everything needed is committed (`Dockerfile`, `render.yaml`, `fly.toml`) — pick one:

### Railway
- Dashboard: **New Project → Deploy from GitHub repo** → it autodetects the `Dockerfile` and deploys.
  Railway injects `$PORT` automatically. Generate a domain under the service's **Settings → Networking**.
- CLI: `railway up` from the repo root.

### Render
- Dashboard: **New → Blueprint** → connect this repo → Render reads `render.yaml` and builds the image.
- Or **New → Web Service → Docker** and point it at the repo. Health check: `/api/health`.

### Fly.io
```bash
fly launch --copy-config --dockerfile Dockerfile   # first time (pick a unique app name)
fly deploy
```
`fly.toml` sets `internal_port = 8000`, HTTPS, and 1GB RAM.

### Google Cloud Run
```bash
gcloud run deploy polylambda --source . --port 8000 --allow-unauthenticated --memory 1Gi
```

## Notes

- **Memory:** ~256–512MB idle; 512MB–1GB is comfortable (numpy/pandas load lazily on first use).
- **Mode:** defaults to `MODE=paper`. Live trading stays jurisdiction-gated and out of scope; the
  container never installs `web3`/`polymarket-client`, so the write path can't even be constructed.
- **Live indexer:** the "Live dispute stream" panel queries a hosted Envio HyperIndex over GraphQL
  (stdlib `urllib`, no dep) — needs only outbound HTTPS, which every host allows. It defaults to the
  public dev deploy; point `INDEXER_GRAPHQL_URL` at your own production indexer to swap it. If the
  endpoint is unreachable the panel shows "offline" and the rest of the dashboard is unaffected.
- **Refreshing artifacts:** if you retrain the hazard model or rebuild the σ prior, re-snapshot with
  `cp .data_cache/hazard_model*.json .data_cache/sigma_prior.json webapp/deploy/cache/ && cp .data_cache/webapp/*.json webapp/deploy/cache/webapp/` and rebuild.
- **Live engine paths (recon / ablation):** `/api/recon/live` and `/api/ablation?live=1` attempt the
  real engine when `INDEXER_GRAPHQL_URL` is set (it is, by default) and **fall back to the published
  artifact with a truthful `source`/`live_error`** otherwise. Each runs in a worker thread under a
  hard deadline (`LIVE_TIMEOUT_S` in `routes.py`) so a slow call never blocks the single uvicorn
  worker. The slim `requirements-deploy.txt` omits the HF/sklearn replay deps, so `?live=1` reports
  "replay deps not installed" and serves the published curve — to ship the richer 4-arm result, run
  `python -m webapp.backend.precompute --ablation` where the full deps exist and commit
  `webapp/deploy/cache/webapp/ablation_full.json`. Live recon also wants an RPC: set `AMOY_RPC_URL`
  (already in the deploy configs) or any of `POLYGON_RPC` / `RPC_URL` / `POLYGON_RPC_URL`.
- **Real-market paper session:** the "real markets" toggle (`source=data`) runs fully offline over the
  shipped `disputes.parquet` — no env needed; it's timeout-guarded like the other heavy calls.
