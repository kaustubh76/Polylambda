# Deploying a persistent, at-head PolyLambda indexer

The dashboard's live panels (the "LIVE dispute stream", the request-time merge that keeps the
disputes explorer current, and the powered `?live=1` ablation) all read one GraphQL endpoint:
`INDEXER_GRAPHQL_URL`. Point it at a **persistent, continuously-syncing** Envio HyperIndex deploy and
every live surface self-heals — no app code change.

> **Why this doc exists.** The public default endpoint
> `https://indexer.dev.hyperindex.xyz/0638687/v1/graphql` is an ephemeral **dev** deploy. It stopped
> ingesting (head froze ~14 days back: `block_height == latest_processed_block`), which is exactly why
> the UI's freshness badge now reads "stale · Nd behind" instead of a false "LIVE". A dev deploy is
> fine for a demo of the schema; it is **not** a source of record and is not kept at head.

## What the app expects

- A GraphQL endpoint exposing the entities in [`schema.graphql`](schema.graphql) — at minimum
  `Dispute { id round disputeTs disputer request { ... market { ... } } }`, ordered by `disputeTs`.
- `chain_metadata { block_height latest_processed_block }` (Envio-standard) so
  [`scripts/indexer_health.py`](../scripts/indexer_health.py) can tell "running" from "stalled".

## Option A — Envio hosted (recommended for always-on)

1. `cd indexer && pnpm install` (or `npm install`).
2. `pnpm envio codegen` to generate the `generated/` client from `config.yaml` + `schema.graphql`.
3. Deploy to Envio's hosted platform (`envio` CLI / the Envio dashboard). Use a **production**
   deployment, not a `dev.hyperindex.xyz` preview, so it stays synced.
4. Provide a fast data source. The backfill is chain 137 from `start_block: 28000000` → head:
   - **With an Envio HyperSync token** (fast path): backfill is minutes-to-hours. Set the token in the
     Envio deployment config.
   - **Keyless public RPCs** (the `rpc:` block in `config.yaml`): honest but SLOW — the config comments
     warn it can stall on the 28M→head backfill. Use only for a scoped/short-range test.
5. Copy the deployment's GraphQL URL into `INDEXER_GRAPHQL_URL` on Render/Fly (`render.yaml`,
   `fly.toml`) and as a repo secret for [`refresh-data.yml`](../.github/workflows/refresh-data.yml).

## Option B — self-hosted (`envio start` on a always-on host)

1. `pnpm envio codegen`
2. Run `pnpm envio start` under a process manager (systemd / a Fly machine / a small VM) with a
   persistent volume for the local Postgres Envio provisions. Do **not** run it as a one-shot job —
   it must stay up to keep polling new blocks.
3. Expose its GraphQL port behind HTTPS and set `INDEXER_GRAPHQL_URL` to it.

## Verify it's actually at head

```bash
python scripts/indexer_health.py --url "$INDEXER_GRAPHQL_URL" --max-age-min 30
```

Exit 0 = at head. Exit 1 = stale (prints head age + whether `block_height` is advancing past
`latest_processed_block`). Wire this into cron/CI to get paged the next time a deploy stalls instead
of discovering it in the UI. Once it returns 0, the dashboard badge flips to **LIVE** and the disputes
explorer starts merging fresh rows automatically (`webapp/backend/services.py::_merged_disputes_df`).

## After it's live

- The scheduled [`refresh-data.yml`](../.github/workflows/refresh-data.yml) will regenerate
  `dataset_release/.../disputes.parquet` from this indexer (via `data/export_disputes.py`), retrain the
  hazard cards, and rebuild the caches — turning the request-time live-merge (bounded by head
  freshness) into a durable, enriched snapshot.
- `POST /api/admin/refresh` busts the running app's caches so a fresh snapshot is served without a
  redeploy.
