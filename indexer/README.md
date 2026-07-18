# PolyLambda indexer (Envio HyperIndex)

Indexes the Polymarket **resolution lifecycle** — UMA OptimisticOracleV2 proposals / disputes /
settles + CTF `ConditionPreparation` — on **Polygon (chain 137)** into Postgres + a GraphQL
(Hasura) endpoint. CLOB fills are **not** indexed here (they come from the HF dataset via
`data/fills.py`). The indexer exists to produce **dispute labels**, shipped as
`dataset_release/polymarket-oov2-disputes-v1/disputes.parquet`.

## Where the data actually lives

You probably don't need to run this. Dispute labels resolve in this order:

1. **Released parquet** (the default — complete, offline):
   `dataset_release/polymarket-oov2-disputes-v1/disputes.parquet` — 1,848 disputes to chain head, all
   adapters, 100% HF-joinable. `data.disputes.load_disputes()` reads the 1,794 in-window out of the box.
2. **Local indexer** (`DATA_SOURCE=graphql`, full fidelity) — this repo, live labels via Hasura.
3. **Hosted HyperIndex deploy** — fallback only, **not authoritative**: row-capped at 1000/page,
   rejects the admin-secret header, aggregates off (coverage-capped).

`resolve_indexer()` in `data/disputes.py` picks the endpoint automatically
(explicit URL → local Hasura → hosted deploy).

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
query { Dispute(limit: 20, order_by: {disputeTs: desc}) { id disputer round request { proposedOutcome market { id } } } }
query { Market(limit: 20) { id status finalOutcome outcomeSlotCount } }
```

(No `Fill` query — `Fill` is never written; the CTFExchange handlers were removed and fills come
from the HF dataset.)

Dispute/ResolutionRequest/Market rows appearing = the resolution lifecycle is landing. 🎉

## Before your first run

- **Leave `start_block` at the shipped full-history value** (`28000000` in `config.yaml`). The
  FULL 28M→head backfill is **required** for the NegRisk join — `ConditionPreparation` (at market
  creation) must land before `QuestionInitialized`, or NegRisk disputes can't attach. Raise
  `start_block` to a recent block only for a quick smoke run.
- **If you just need dispute labels, don't run the indexer at all** — use the released parquet
  (the `load_disputes()` default).
- Addresses are the verified ones from [`../DECISIONS.md`](../DECISIONS.md) — re-confirm on
  Polygonscan before any live use.

## Resolution lifecycle (proposal / dispute) — WIRED ✅

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

**Validated — the honest gap is closed:** the full **28M→head backfill is done**. Dispute
capture cross-checked **723/723** against the independent RPC/keccak path, and recon
`pass_rate` = **1.0** on the eligible set (`recon/check.py`). Released output:
`dataset_release/polymarket-oov2-disputes-v1/disputes.parquet` — **1,848 disputes** (1,794 in-window),
100% HF-joinable across all adapters (NegRisk via the tradeable-cid map in `data/negrisk_map.py`).

## Tests

```bash
pnpm install        # then, for the handler test only:
pnpm codegen
pnpm test           # vitest
```

- **`test/lib.test.ts`** — PURE tests (viem + vitest only, **no codegen needed**): `decodeOutcome`,
  `deriveFill` (BUY/SELL/price — kept as a pure helper even though no `Fill` entity is indexed),
  and `deriveConditionId` (determinism + distinctness).
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
