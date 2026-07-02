# PolyLambda indexer (Envio HyperIndex)

Indexes the Polymarket lifecycle + CLOB fills on **Polygon (chain 137)** into Postgres + a
GraphQL (Hasura) endpoint. This is **step 1** of the build — get the data flowing.

## Run it

Prereqs: **Node ≥ 22**, `pnpm`, **Docker running**.

```bash
cd indexer
pnpm install
pnpm codegen        # generates ./generated types from config.yaml + schema.graphql
pnpm dev            # spins up Postgres + Hasura via Docker, starts indexing
```

Then open **http://localhost:8080** (Hasura console, admin secret: `testing`) and query:

```graphql
query { Fill(limit: 20, order_by: {timestamp: desc}) { price size side tokenId timestamp } }
query { Market(limit: 20) { id status finalOutcome outcomeSlotCount } }
```

Rows appearing = the fill tape + market lifecycle are landing. 🎉

## Before your first run

- **Set a recent `start_block`** in `config.yaml` (look up current height on polygonscan.com).
  A recent block keeps the first backfill to minutes. Lower it later to index history for the
  λ historical-replay ablation.
- Addresses are the verified ones from [`../DECISIONS.md`](../DECISIONS.md) — re-confirm on
  Polygonscan before any live use.

## Step 2 — resolution lifecycle (proposal / dispute) — WIRED ✅

The **OptimisticOracleV2** + **UMA CTF Adapter** contracts are now active in `config.yaml` with
event signatures **verified from source** (Polymarket/uma-ctf-adapter + UMAprotocol/protocol),
and handlers are wired in `src/EventHandlers.ts`:

- `QuestionInitialized → Market.ancillaryData + ResolutionRequest(round 0)`
- `QuestionReset → new ResolutionRequest(round++), Market RESET` (two-strikes)
- `ProposePrice → ResolutionRequest.proposer/outcome, Market PROPOSED`
- `DisputePrice → Dispute, Market DISPUTED`
- `QuestionResolved / Settle → RESOLVED`

**Join** (verified): the adapter sets `questionID = keccak256(ancillaryData)` and
`prepareCondition(adapter, questionID, 2)`, so `conditionId = keccak256(adapter, questionID, 2)`.
Adapter events carry `questionID`; OO events carry `ancillaryData` (→ questionID) + `requester`
(= adapter). OO events for un-indexed / non-Polymarket requesters find no Market and are skipped.

**Still to do — the one honest validation gap:** replay a **known disputed market** end-to-end
and assert the entities populate (proposal → auto-reset → 2nd dispute → DVM). Resolution time is
**bimodal** (~2–4h happy path vs ~4–6d escalated). Save a disputed fixture (conditionId + tx) in
`../notes/day01-lifecycle.md`. Also re-confirm the deployed adapter's ABI on Polygonscan matches
the source (the address may differ from `main`).

## Tests

```bash
pnpm install        # then, for the handler test only:
pnpm codegen
pnpm test           # vitest
```

- **`test/lib.test.ts`** — PURE tests (viem + vitest only, **no codegen needed**): `decodeOutcome`,
  `deriveFill` (BUY/SELL/price), and `deriveConditionId` (determinism + distinctness).
- **`test/handlers.test.ts`** — end-to-end lifecycle via Envio V3 `createTestIndexer`:
  `QuestionInitialized → ProposePrice → DisputePrice → QuestionReset` and asserts the
  `Market` / `ResolutionRequest` / `Dispute` rows. This proves the join is consistent — the OO
  events (join via `keccak256(ancillaryData)` + requester) resolve to the SAME `conditionId` the
  adapter created, or the proposal/dispute wouldn't attach. If the installed alpha needs extra
  `block`/`transaction` fields in `simulate`, add them to the `meta()` helper.

Pure logic (`src/lib.ts`) is separated from I/O precisely so it can be tested without a chain or DB.

## Fallback / reference

If `pnpm install`/`codegen` complains about the Envio version, generate a fresh project with
`pnpx envio init` and drop in this `config.yaml`, `schema.graphql`, and `src/EventHandlers.ts`.
Reference implementation (adapt handler patterns): https://github.com/enviodev/polymarket-indexer
