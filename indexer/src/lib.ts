/*
 * Pure helpers shared by the handlers — no Envio "generated" imports, so they can be unit-tested
 * directly with just viem + vitest (no codegen needed).
 */
import { keccak256, encodePacked } from "viem";

export const ONE = 10n ** 18n; // OO proposedPrice scale (1e18 = YES)

/**
 * conditionId = keccak256(abi.encodePacked(oracle, questionId, outcomeSlotCount)) with
 * outcomeSlotCount = 2 for binary (Gnosis CTF getConditionId). For Polymarket, oracle = the
 * UMA CTF Adapter address that called prepareCondition.
 */
export function deriveConditionId(oracle: string, questionId: string): string {
  return keccak256(
    encodePacked(
      ["address", "bytes32", "uint256"],
      [oracle as `0x${string}`, questionId as `0x${string}`, 2n]
    )
  );
}

/** The adapter sets questionID = keccak256(ancillaryData); OO events carry ancillaryData. */
export function questionIdFromAncillary(ancillaryData: string): string {
  return keccak256(ancillaryData as `0x${string}`);
}

/** Decode an OO int256 proposedPrice into a readable outcome. */
export function decodeOutcome(p: bigint): string {
  if (p === ONE) return "YES";
  if (p === 0n) return "NO";
  if (p === ONE / 2n) return "UNRESOLVABLE";
  return "OTHER";
}

/**
 * Derive Fill fields from a CTF Exchange OrderFilled: one leg is collateral (assetId 0), the
 * other an outcome token. BUY = maker paid collateral for outcome tokens; price = collateral /
 * outcomeTokens (in (0,1)); size = outcome-token amount in human units (6 decimals).
 */
export function deriveFill(
  makerAssetId: bigint,
  takerAssetId: bigint,
  makerAmountFilled: bigint,
  takerAmountFilled: bigint
): { tokenId: string; price: number; size: number; side: string } {
  let tokenId: bigint, outcomeAmt: bigint, collateralAmt: bigint, side: string;
  if (makerAssetId === 0n) {
    collateralAmt = makerAmountFilled;
    outcomeAmt = takerAmountFilled;
    tokenId = takerAssetId;
    side = "BUY";
  } else {
    collateralAmt = takerAmountFilled;
    outcomeAmt = makerAmountFilled;
    tokenId = makerAssetId;
    side = "SELL";
  }
  const price = outcomeAmt === 0n ? 0 : Number(collateralAmt) / Number(outcomeAmt);
  const size = Number(outcomeAmt) / 1e6;
  return { tokenId: tokenId.toString(), price, size, side };
}
