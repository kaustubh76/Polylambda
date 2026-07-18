# Deploying a persistent, at-head PolyLambda indexer (OPTIONAL)

> **Read this first — an indexer is no longer required.** The hosted Envio dev deploy
> (`indexer.dev.hyperindex.xyz/0638687`) is **gone**: the free tier ended. The dashboard's live panels
> were pivoted to a **keyless Polygon RPC** scan of OOv2 `DisputePrice` logs
> (`data.disputes.recent_disputes_rpc` → `webapp/backend/live.py`), which reaches chain head with no
> indexer and no paid service — including NegRisk conditionIds, recovered on-chain via the NegRisk
> operator's `QuestionPrepared` event. **Leave `INDEXER_GRAPHQL_URL` unset** unless you run your own:
> a stale value costs an 8s timeout on every status poll *and* disables the working RPC feed.

This doc remains for the case where you *want* a GraphQL indexer — e.g. to source `round` and the
resolution lifecycle directly rather than deriving them, or to run the `--matched` hazard evaluation
(the one job that genuinely still needs an indexer). Point `INDEXER_GRAPHQL_URL` at a **persistent,
continuously-syncing** deploy and the live surfaces switch to it automatically — no app code change.

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
