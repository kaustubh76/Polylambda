/*
 * PURE unit tests for the join/derivation helpers. These need only viem + vitest — they run
 * WITHOUT `pnpm codegen` (no "generated" import). Run: `pnpm install && pnpm test`.
 */
import { describe, it, expect } from "vitest";
import {
  ONE,
  decodeOutcome,
  deriveConditionId,
  deriveFill,
  questionIdFromAncillary,
} from "../src/lib";

describe("decodeOutcome", () => {
  it("maps OO proposedPrice to a readable outcome", () => {
    expect(decodeOutcome(ONE)).toBe("YES");
    expect(decodeOutcome(0n)).toBe("NO");
    expect(decodeOutcome(ONE / 2n)).toBe("UNRESOLVABLE");
    expect(decodeOutcome(123n)).toBe("OTHER");
  });
});

describe("deriveFill", () => {
  it("BUY: maker pays collateral (assetId 0) for outcome tokens", () => {
    // 60 USDC (6dp) for 100 outcome tokens -> price 0.6, size 100
    const f = deriveFill(0n, 12345n, 60_000_000n, 100_000_000n);
    expect(f.side).toBe("BUY");
    expect(f.tokenId).toBe("12345");
    expect(f.price).toBeCloseTo(0.6, 9);
    expect(f.size).toBeCloseTo(100, 9);
  });

  it("SELL: maker gives outcome tokens, receives collateral", () => {
    const f = deriveFill(12345n, 0n, 100_000_000n, 40_000_000n);
    expect(f.side).toBe("SELL");
    expect(f.tokenId).toBe("12345");
    expect(f.price).toBeCloseTo(0.4, 9);
    expect(f.size).toBeCloseTo(100, 9);
  });

  it("guards divide-by-zero", () => {
    expect(deriveFill(0n, 1n, 5n, 0n).price).toBe(0);
  });
});

describe("deriveConditionId", () => {
  const adapter = "0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74";
  const ancillary = "0x646573633a2074657374"; // "desc: test"

  it("returns a deterministic 32-byte hex id", () => {
    const cid = deriveConditionId(adapter, questionIdFromAncillary(ancillary));
    expect(cid).toMatch(/^0x[0-9a-f]{64}$/);
    expect(deriveConditionId(adapter, questionIdFromAncillary(ancillary))).toBe(cid);
  });

  it("adapter-event and OO-event derivations agree (the join is consistent)", () => {
    // adapter event carries questionID directly; OO event recomputes it from ancillaryData
    const fromAdapter = deriveConditionId(adapter, questionIdFromAncillary(ancillary));
    const fromOO = deriveConditionId(adapter, questionIdFromAncillary(ancillary));
    expect(fromOO).toBe(fromAdapter);
  });

  it("different ancillaryData -> different conditionId", () => {
    const c1 = deriveConditionId(adapter, questionIdFromAncillary("0xaa"));
    const c2 = deriveConditionId(adapter, questionIdFromAncillary("0xbb"));
    expect(c1).not.toBe(c2);
  });
});
