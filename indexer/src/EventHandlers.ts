/*
 * PolyLambda event handlers (Envio HyperIndex).
 *
 * STEP 1 — market lifecycle + fill tape:
 *   ConditionPreparation -> Market (OPEN)
 *   ConditionResolution  -> Market.finalOutcome (RESOLVED)   [feeds reconciliation]
 *   TokenRegistered      -> TokenMap (tokenId -> conditionId)
 *   OrderFilled          -> Fill                              [feeds sigma / fair value]
 *
 * STEP 2 — resolution lifecycle (proposal / dispute / settle):
 *   UmaCtfAdapter.QuestionInitialized -> Market.ancillaryData + ResolutionRequest(round 0)
 *   UmaCtfAdapter.QuestionReset       -> new ResolutionRequest (round++), Market RESET   (two-strikes)
 *   UmaCtfAdapter.QuestionResolved    -> Market RESOLVED
 *   OptimisticOracleV2.ProposePrice   -> ResolutionRequest.proposer/outcome, Market PROPOSED
 *   OptimisticOracleV2.DisputePrice   -> Dispute, Market DISPUTED
 *   OptimisticOracleV2.Settle         -> ResolutionRequest RESOLVED
 *
 * JOIN (adapter-agnostic LOOKUP — supports V2 AND NegRisk):
 *   ConditionPreparation emits the conditionId DIRECTLY (its conditionId IS the HF one) → QuestionIndex
 *     maps questionId -> conditionId for every oracle.
 *   QuestionInitialized (V2 + NegRisk) resolves conditionId via QuestionIndex[questionID] (keccak
 *     fallback for V2 if ConditionPreparation hasn't landed yet) and writes RequestIndex keyed by
 *     (adapter, requestTimestamp).
 *   OO ProposePrice/DisputePrice/Settle carry (requester=adapter, timestamp=requestTimestamp) → they
 *     read conditionId from RequestIndex (keccak fallback for V2). NegRisk conditionIds are NOT
 *     keccak-derivable (NegRiskIdLib), so the lookup is what makes NegRisk disputes joinable.
 * OO events for non-Polymarket requesters find no RequestIndex/Market and are skipped.
 */
import { ConditionalTokens, UmaCtfAdapter, OptimisticOracleV2 } from "generated";
import { deriveConditionId, questionIdFromAncillary, decodeOutcome } from "./lib";
// NB: CTFExchange (OrderFilled/TokenRegistered) is intentionally not indexed here — the fill tape
// and tokenId↔conditionId map come from the HF dataset (data/*). deriveFill stays in ./lib (unit-
// tested) and is re-expressed as DuckDB SQL in data/fills.py. Re-add both if you re-enable the tape.

// ===========================================================================
// STEP 1 — market lifecycle
// ===========================================================================
ConditionalTokens.ConditionPreparation.handler(async ({ event, context }) => {
  const existing = await context.Market.get(event.params.conditionId);
  context.Market.set({
    id: event.params.conditionId,
    questionId: event.params.questionId,
    ancillaryData: existing?.ancillaryData,
    oracle: event.params.oracle,
    outcomeSlotCount: Number(event.params.outcomeSlotCount),
    status: existing?.status ?? "OPEN",
    currentRound: existing?.currentRound ?? 0,
    finalOutcome: existing?.finalOutcome,
    preparedAt: existing?.preparedAt ?? BigInt(event.block.timestamp),
    resolvedAt: existing?.resolvedAt,
  });
  // Authoritative questionId -> conditionId, direct from chain (works for EVERY adapter incl. NegRisk).
  context.QuestionIndex.set({ id: event.params.questionId, conditionId: event.params.conditionId });
});

ConditionalTokens.ConditionResolution.handler(async ({ event, context }) => {
  const existing = await context.Market.get(event.params.conditionId);
  context.Market.set({
    id: event.params.conditionId,
    questionId: existing?.questionId ?? event.params.questionId,
    ancillaryData: existing?.ancillaryData,
    oracle: existing?.oracle ?? event.params.oracle,
    outcomeSlotCount: existing?.outcomeSlotCount ?? Number(event.params.outcomeSlotCount),
    status: "RESOLVED",
    currentRound: existing?.currentRound ?? 0,
    // payout vector, e.g. "1,0" (YES) or "0,1" (NO). recon compares this to expected.
    finalOutcome: event.params.payoutNumerators.map((x) => x.toString()).join(","),
    preparedAt: existing?.preparedAt,
    resolvedAt: BigInt(event.block.timestamp),
  });
});

// --- CLOB fill tape (TokenRegistered + OrderFilled) — REMOVED, sourced from the HF dataset. ---
// The Fill and TokenMap entities are now populated by data/* (DuckDB over order_filled + market_data),
// not by local indexing. See ../config.yaml for the rationale. To re-enable the live head tape,
// restore the CTFExchange contract in config.yaml and uncomment the handlers below + the import.
//
// CTFExchange.TokenRegistered.handler(async ({ event, context }) => { ... });
// CTFExchange.OrderFilled.handler(async ({ event, context }) => { ... deriveFill(...) ... });

// ===========================================================================
// STEP 2 — resolution lifecycle
// ===========================================================================
UmaCtfAdapter.QuestionInitialized.handler(async ({ event, context }) => {
  // conditionId from the authoritative QuestionIndex (ConditionPreparation) — works for NegRisk;
  // fall back to the keccak derivation for V2 if ConditionPreparation hasn't been seen yet.
  const qi = await context.QuestionIndex.get(event.params.questionID);
  const conditionId = qi?.conditionId ?? deriveConditionId(event.srcAddress, event.params.questionID);
  const m = await context.Market.get(conditionId);
  context.Market.set({
    id: conditionId,
    questionId: m?.questionId ?? event.params.questionID,
    ancillaryData: event.params.ancillaryData,
    oracle: event.srcAddress,
    outcomeSlotCount: m?.outcomeSlotCount ?? 2,
    status: m?.status === "RESOLVED" ? "RESOLVED" : "REQUESTED",
    currentRound: m?.currentRound ?? 0,
    finalOutcome: m?.finalOutcome,
    preparedAt: m?.preparedAt ?? BigInt(event.block.timestamp),
    resolvedAt: m?.resolvedAt,
  });
  context.ResolutionRequest.set({
    id: `${conditionId}-0`,
    market_id: conditionId,
    requestTimestamp: event.params.requestTimestamp,
    round: 0,
    proposer: undefined,
    bond: event.params.proposalBond,
    proposedPrice: undefined,
    proposedOutcome: undefined,
    status: "REQUESTED",
  });
  // (adapter, requestTimestamp) -> conditionId, so the OO events (requester=adapter, timestamp) link back.
  context.RequestIndex.set({
    id: `${event.srcAddress.toLowerCase()}-${event.params.requestTimestamp}`,
    conditionId,
  });
});

UmaCtfAdapter.QuestionReset.handler(async ({ event, context }) => {
  const conditionId = deriveConditionId(event.srcAddress, event.params.questionID);
  const m = await context.Market.get(conditionId);
  if (!m) return;
  const newRound = m.currentRound + 1;
  context.Market.set({ ...m, currentRound: newRound, status: "RESET" });
  context.ResolutionRequest.set({
    id: `${conditionId}-${newRound}`,
    market_id: conditionId,
    requestTimestamp: BigInt(event.block.timestamp), // reset event carries no requestTimestamp
    round: newRound,
    proposer: undefined,
    bond: undefined,
    proposedPrice: undefined,
    proposedOutcome: undefined,
    status: "REQUESTED",
  });
});

UmaCtfAdapter.QuestionResolved.handler(async ({ event, context }) => {
  const conditionId = deriveConditionId(event.srcAddress, event.params.questionID);
  const m = await context.Market.get(conditionId);
  if (!m) return;
  context.Market.set({ ...m, status: "RESOLVED", resolvedAt: BigInt(event.block.timestamp) });
  const rr = await context.ResolutionRequest.get(`${conditionId}-${m.currentRound}`);
  if (rr) context.ResolutionRequest.set({ ...rr, status: "RESOLVED" });
});

OptimisticOracleV2.ProposePrice.handler(async ({ event, context }) => {
  const ri = await context.RequestIndex.get(`${event.params.requester.toLowerCase()}-${event.params.timestamp}`);
  const conditionId = ri?.conditionId ?? deriveConditionId(event.params.requester, questionIdFromAncillary(event.params.ancillaryData));
  const m = await context.Market.get(conditionId);
  if (!m) return; // not one of our (indexed) markets
  const rrId = `${conditionId}-${m.currentRound}`;
  const rr = await context.ResolutionRequest.get(rrId);
  context.ResolutionRequest.set({
    id: rrId,
    market_id: conditionId,
    requestTimestamp: rr?.requestTimestamp ?? event.params.timestamp,
    round: m.currentRound,
    proposer: event.params.proposer,
    bond: rr?.bond,
    proposedPrice: event.params.proposedPrice.toString(),
    proposedOutcome: decodeOutcome(event.params.proposedPrice),
    status: "PROPOSED",
  });
  context.Market.set({ ...m, status: "PROPOSED" });
});

OptimisticOracleV2.DisputePrice.handler(async ({ event, context }) => {
  const ri = await context.RequestIndex.get(`${event.params.requester.toLowerCase()}-${event.params.timestamp}`);
  const conditionId = ri?.conditionId ?? deriveConditionId(event.params.requester, questionIdFromAncillary(event.params.ancillaryData));
  const m = await context.Market.get(conditionId);
  if (!m) return;
  const rrId = `${conditionId}-${m.currentRound}`;
  context.Dispute.set({
    id: `${event.transaction.hash}-${event.logIndex}`,
    request_id: rrId,
    disputer: event.params.disputer,
    disputeTs: event.params.timestamp,
    round: m.currentRound,
  });
  const rr = await context.ResolutionRequest.get(rrId);
  if (rr) context.ResolutionRequest.set({ ...rr, status: "DISPUTED" });
  context.Market.set({ ...m, status: "DISPUTED" });
});

OptimisticOracleV2.Settle.handler(async ({ event, context }) => {
  const ri = await context.RequestIndex.get(`${event.params.requester.toLowerCase()}-${event.params.timestamp}`);
  const conditionId = ri?.conditionId ?? deriveConditionId(event.params.requester, questionIdFromAncillary(event.params.ancillaryData));
  const m = await context.Market.get(conditionId);
  if (!m) return;
  const rr = await context.ResolutionRequest.get(`${conditionId}-${m.currentRound}`);
  if (rr) context.ResolutionRequest.set({ ...rr, status: "RESOLVED" });
});
