---
license: cc-by-4.0
task_categories:
  - tabular-classification
tags:
  - polymarket
  - prediction-markets
  - uma
  - optimistic-oracle
  - disputes
  - polygon
pretty_name: Polymarket OOv2 Disputes v1
---

# Polymarket OptimisticOracleV2 Disputes (v1)

The **missing dispute layer** for Polymarket on-chain analysis. The excellent public dataset
[`moose-code/polymarket-onchain-v1`](https://huggingface.co/datasets/moose-code/polymarket-onchain-v1)
(2.74B on-chain records, 1.17B CLOB fills, conditions, redemptions, positions) is the trading/resolution
backbone — but it does **not** index the UMA **OptimisticOracleV2** proposal/dispute lifecycle. This
companion dataset fills exactly that gap: every on-chain `DisputePrice` on a Polymarket UMA CTF adapter,
linked to its Gnosis CTF `conditionId` so it joins the `moose-code` tables directly.

- **1848 disputes** across adapters (v2=725 · negrisk=1013 · 0x157ce2d672854c848c9b79c49a8cc6cc89176a49=110), 2022-12-30 → 2026-07-16.
- **1794 of them fall inside the `moose-code` snapshot
  window** (`disputeTs` ≤ block 85,948,287 / 2026-04-24). Rows past that head are flagged
  `post_hf_cutoff` and carry **no price context** — the fill tape they would be measured against is
  frozen at the cutoff. **Compute base rates on the in-window rows only**: the denominator (resolved
  markets) is an HF snapshot, so a market disputed after the head is counted as disputed while never
  counting as resolved. See `post_hf_cutoff` below.
- **1848 (100.0%) are HF-joinable** — across **all** adapters. The
  released `conditionId` is the effective `moose-code` join key: for NegRisk (multi-outcome) markets that
  is the **tradeable** conditionId recovered from the NegRiskOperator (see the NegRisk map below), not the
  UMA-side id; for V2/Legacy it is the native conditionId. Every joinable row carries a derived `category`
  and joins the fill tape directly.

## Provenance
Produced by PolyLambda from **Polygon (chain 137) on-chain logs**, via either of two interchangeable
sources — a direct **keyless RPC scan** of the OOv2 `DisputePrice` logs (`data/disputes.py`, the default
and how this release is regenerated today), or the scoped **Envio indexer** (`indexer/`) when one is
running. Both resolve the same facts:
- `ConditionalTokens.ConditionPreparation` → the authoritative `questionId → conditionId` map (works for
  **every** adapter, including NegRisk — it is read from chain, never derived);
- `UmaCtfAdapter.QuestionInitialized` (V2 + NegRisk + Legacy) → `(adapter, requestTimestamp) → conditionId`;
- `OptimisticOracleV2.{ProposePrice, DisputePrice, Settle}` → the proposal/dispute/settle events, whose
  `conditionId` is resolved via the lookup above (no keccak derivation — which fails for NegRisk).

The RPC path reaches the same `conditionId` without an indexer: it decodes each `DisputePrice` log,
derives the UMA `questionId` as `keccak(ancillaryData)`, and — for NegRisk — bridges to the tradeable
`conditionId` through the NegRiskOperator `QuestionPrepared` event (see the NegRisk map below).

Reconciliation: the resolved `finalOutcome` matches `moose-code` `condition.payoutNumerators`
at **pass_rate = 1.0000 on 27,238 eligible V2/Legacy markets**. (Deterministic over the HF-aligned
universe — reproducible run-to-run since the recon scan was ordered; the reconciliation check requires an
indexer, so it is not recomputed on an RPC-only regeneration.)

## Schema (`disputes.parquet`)
| column | type | notes |
|---|---|---|
| `conditionId` | string | Gnosis CTF conditionId — the effective join key to `moose-code` `condition.id` / `market_data.condition`. For NegRisk this is the **tradeable** conditionId (oracle = NegRiskAdapter), recovered on-chain; for V2/Legacy it is the native conditionId. |
| `questionId` | string | UMA question id (the OOv2 request id; for NegRisk it maps to the tradeable conditionId via the Operator — see below) |
| `adapter` | string | `v2` · `negrisk` · `legacy` (the UMA CTF adapter that owns the request) |
| `hf_joinable` | bool | `true` iff `conditionId` ∈ `moose-code` `condition` (all adapters; false only when the NegRisk map could not resolve a market, or the market post-dates the HF cutoff) |
| `category` | string | coarse keyword-derived category (crypto/politics/sports/…); null when not joinable |
| `disputeTs` | int64 | TRUE dispute block timestamp (epoch seconds) — resolved from the dispute tx's block |
| `disputeDate` | string | `YYYY-MM-DD` (from `disputeTs`) |
| `requestTimestamp` | int64 | the UMA OO *price-request* timestamp the dispute references (can precede the dispute tx by hours; this is what the raw `DisputePrice` event carries) |
| `round` | int | reset round (0 = first request; bumps on each two-strikes reset) |
| `disputer` / `proposer` | string | on-chain addresses |
| `proposedOutcome` | string | the disputed proposal: `YES` / `NO` / `UNRESOLVABLE` / `OTHER` |
| `preDisputePrice` / `postDisputePrice` / `realizedJumpLogit` | double | optional fill-tape price context (joinable only; null unless exported `--with-price-context`, and **always null when `post_hf_cutoff`** — the fill tape ends at the cutoff) |
| `post_hf_cutoff` | bool | `true` iff the dispute happened **after** the `moose-code` snapshot head (block 85,948,287 / 2026-04-24). These rows extend the dispute timeline to the present, but they are **not** base-rate eligible: the resolved-market denominator is frozen at the cutoff, so counting them inflates the rate (they land in `n_markets` but not `n_resolved`). Filter them out for any rate/hazard work; keep them for recency. |

## Join recipe (DuckDB)
```sql
INSTALL httpfs; LOAD httpfs;
SELECT d.conditionId, d.adapter, d.category, d.disputeDate, m.marketSlug
FROM 'disputes.parquet' d
JOIN 'hf://datasets/moose-code/polymarket-onchain-v1/market_data.parquet' m
  ON m.condition = d.conditionId
WHERE d.hf_joinable;   -- joins ALL adapters, incl. NegRisk (conditionId is already the tradeable key)
```

## The NegRisk map (the piece no public dataset ships)
Polymarket's **multi-outcome (NegRisk)** markets resolve through the UMA OOv2 under a UMA `questionId`,
but they **trade** under a *different* `conditionId` whose oracle is the NegRiskAdapter
`0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`. That tradeable conditionId is what `moose-code` keys on —
so joining a NegRisk dispute to the fill tape requires bridging UMA `questionId` → tradeable
`conditionId`. This dataset does that bridge for you: the released `conditionId` is already the tradeable
key, recovered from chain via the **NegRiskOperator** (`0x71523d0f655B41E805Cec45b17163f528B59B820`)
`QuestionPrepared` event, then `conditionId = keccak(NegRiskAdapter ++ questionId_d91e ++ uint256(2))`.

Validated end-to-end on independent disputes across crypto/F1/politics/weather/NFL — each recovered
`conditionId` both joins `moose-code` `market_data` **and** agrees with the on-chain
`ConditionPreparation`. Rows are `hf_joinable=false` only when the market post-dates the `moose-code`
cutoff (block 85,948,287 / 2026-04-24) or the Operator mapping could not be resolved.

> Earlier versions of this card described NegRisk as structurally absent from `moose-code`. That was an
> artifact of joining on the indexer's UMA-side (phantom) conditionId; the tradeable conditions are fully
> present, and this release now joins them.

## License
`CC-BY-4.0`, matching the upstream `moose-code/polymarket-onchain-v1`. Attribution: PolyLambda +
the `enviodev/polymarket-indexer` lineage.
