/*
 * Integration test for the resolution-lifecycle handlers, using Envio V3's createTestIndexer
 * (`MockDb` was removed in V3). Requires `pnpm install && pnpm codegen` first (imports "generated"
 * indirectly via handler registration). Run: `pnpm test`.
 *
 * It proves the whole join works end-to-end: QuestionInitialized (adapter, questionID) creates a
 * Market at conditionId = keccak256(adapter, questionID, 2); the OO events (which recompute
 * questionID = keccak256(ancillaryData) and use requester = adapter) resolve to the SAME Market —
 * if the two derivation paths disagreed, the proposal/dispute would not attach and the asserts
 * would fail.
 *
 * NOTE (alpha API): simulate accepts per-event metadata under the same block/transaction/
 * srcAddress/logIndex shape real events expose. If the installed Envio alpha requires extra
 * block/transaction fields, add them to `meta()` below. `lib.test.ts` is the always-runnable pure test.
 */
import { describe, it, expect } from "vitest";
// createTestIndexer is emitted by `pnpm codegen` into "generated" (the `envio` package itself does
// not export it at the pinned 3.0.0-alpha.21 — importing it from "envio" yields undefined).
import { createTestIndexer } from "generated";
import "../src/EventHandlers"; // register handlers
import { ONE, deriveConditionId, questionIdFromAncillary } from "../src/lib";

const ADAPTER = "0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74";
const OO = "0xeE3Afe347D5C74317041E2618C49534dAf887c24";
const PROPOSER = "0x1111111111111111111111111111111111111111";
const DISPUTER = "0x2222222222222222222222222222222222222222";
const IDENTIFIER = "0x5945535f4f525f4e4f5f5155455259000000000000000000000000000000000"; // "YES_OR_NO_QUERY"-ish
const ANCILLARY = "0x646573633a20576869636820746f6b656e2077696e733f"; // arbitrary bytes
const TXH = "0x" + "ab".repeat(32);

const QID = questionIdFromAncillary(ANCILLARY);
const CID = deriveConditionId(ADAPTER, QID);

// alpha.21 validates simulated blocks against config.yaml's start_block (28M) and advances the
// chain cursor between process() calls — so each event gets the next block past the start.
let blockNo = 28_000_000;

function meta(over: Record<string, unknown> = {}) {
  blockNo += 1;
  return {
    srcAddress: ADAPTER,
    logIndex: 0,
    block: { number: blockNo, timestamp: 1_700_000_000 },
    transaction: { hash: TXH, from: PROPOSER, to: ADAPTER },
    ...over,
  };
}

describe("resolution lifecycle", () => {
  it("QuestionInitialized -> ProposePrice -> DisputePrice -> QuestionReset", async () => {
    const indexer = createTestIndexer();

    // 1) initialize the question
    await indexer.process({
      chains: {
        137: {
          simulate: [
            {
              contract: "UmaCtfAdapter",
              event: "QuestionInitialized",
              params: {
                questionID: QID,
                requestTimestamp: 100n,
                creator: ADAPTER,
                ancillaryData: ANCILLARY,
                rewardToken: OO,
                reward: 0n,
                proposalBond: 750_000_000n,
              },
              ...meta({ srcAddress: ADAPTER }),
            },
          ],
        },
      },
    });

    let m = await indexer.Market.getOrThrow(CID);
    expect(m.status).toBe("REQUESTED");
    expect(m.ancillaryData).toBe(ANCILLARY);
    let r0 = await indexer.ResolutionRequest.getOrThrow(`${CID}-0`);
    expect(r0.status).toBe("REQUESTED");
    expect(r0.bond).toBe(750_000_000n);

    // 2) proposal (OO event joins via requester + ancillaryData)
    await indexer.process({
      chains: {
        137: {
          simulate: [
            {
              contract: "OptimisticOracleV2",
              event: "ProposePrice",
              params: {
                requester: ADAPTER,
                proposer: PROPOSER,
                identifier: IDENTIFIER,
                timestamp: 100n,
                ancillaryData: ANCILLARY,
                proposedPrice: ONE, // YES
                expirationTimestamp: 200n,
                currency: OO,
              },
              ...meta({ srcAddress: OO, logIndex: 1 }),
            },
          ],
        },
      },
    });

    m = await indexer.Market.getOrThrow(CID);
    expect(m.status).toBe("PROPOSED");
    r0 = await indexer.ResolutionRequest.getOrThrow(`${CID}-0`);
    expect(r0.status).toBe("PROPOSED");
    expect(r0.proposedOutcome).toBe("YES");
    expect(r0.proposer?.toLowerCase()).toBe(PROPOSER.toLowerCase());

    // 3) dispute
    await indexer.process({
      chains: {
        137: {
          simulate: [
            {
              contract: "OptimisticOracleV2",
              event: "DisputePrice",
              params: {
                requester: ADAPTER,
                proposer: PROPOSER,
                disputer: DISPUTER,
                identifier: IDENTIFIER,
                timestamp: 100n,
                ancillaryData: ANCILLARY,
                proposedPrice: ONE,
              },
              ...meta({ srcAddress: OO, logIndex: 2, transaction: { hash: TXH, from: DISPUTER, to: OO } }),
            },
          ],
        },
      },
    });

    m = await indexer.Market.getOrThrow(CID);
    expect(m.status).toBe("DISPUTED");
    const disputes = await indexer.Dispute.getAll();
    expect(disputes.length).toBe(1);
    expect(disputes[0].round).toBe(0);

    // 4) first dispute auto-resets -> round 1 (the two-strikes structure)
    await indexer.process({
      chains: {
        137: {
          simulate: [
            {
              contract: "UmaCtfAdapter",
              event: "QuestionReset",
              params: { questionID: QID },
              ...meta({ srcAddress: ADAPTER, logIndex: 3 }),
            },
          ],
        },
      },
    });

    m = await indexer.Market.getOrThrow(CID);
    expect(m.status).toBe("RESET");
    expect(m.currentRound).toBe(1);
    const r1 = await indexer.ResolutionRequest.getOrThrow(`${CID}-1`);
    expect(r1.status).toBe("REQUESTED");
  });
});
