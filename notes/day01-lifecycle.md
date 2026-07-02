# Day 01 — Resolution lifecycle + repo bootstrap

> Learning artifact (Phase 1). Prove you understand the lifecycle; then log the commit.

## UMA / Polymarket resolution lifecycle (draw from memory, then check)

proposal → bond → ~2h liveness → **dispute #1 → AUTO-RESET** (adapter `_reset`, new ~2h request,
resolves ~2–4h) → **dispute #2 → DVM** (commit 24h + reveal 24h = 48h base, 48–96h) → settle →
`ConditionResolution` sets the CTF payout vector.

- Time-to-resolution is **bimodal** (happy ~2–4h vs escalated ~4–6d).
- ⚠ A dispute does **NOT** lock trading — the CLOB stays open; only **redemption** freezes and
  exit liquidity thins (~5c haircut). (See ../DECISIONS.md #1.)

## Contract topology (chain 137) — verified in ../DECISIONS.md
ConditionalTokens · CTF Exchange · UMA CTF Adapter V2 · OptimisticOracleV2.
Join: `conditionId = keccak256(adapter, questionId, 2)`; UMA request = 4-tuple
`(adapter, YES_OR_NO_IDENTIFIER, requestTimestamp, ancillaryData)` (no opaque requestId).

## Today's build
- [x] Repo skeleton + scaffold (indexer + Python).
- [ ] Set a recent `start_block` in `indexer/config.yaml`.
- [ ] `pnpm install && pnpm codegen && pnpm dev`; query `Fill` / `Market` in Hasura.
- [ ] Pick 1 resolved + 1 disputed fixture market (save conditionId + tx hashes here):

```
resolved fixture:  conditionId=__  tx=__
disputed fixture:  conditionId=__  tx=__
```
