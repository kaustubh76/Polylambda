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

- **1794 disputes** across adapters (v2=723 · negrisk=963 · 0x157ce2d672854c848c9b79c49a8cc6cc89176a49=108), 2022-12-28 → 2026-04-09.
- **1794 (100.0%) are HF-joinable** — across **all** adapters. The
  released `conditionId` is the effective `moose-code` join key: for NegRisk (multi-outcome) markets that
  is the **tradeable** conditionId recovered from the NegRiskOperator (see the NegRisk map below), not the
  UMA-side id; for V2/Legacy it is the native conditionId. Every joinable row carries a derived `category`
  and joins the fill tape directly.

## Provenance
Produced by the PolyLambda scoped Envio indexer (`indexer/`, chain 137 / Polygon), which reads:
- `ConditionalTokens.ConditionPreparation` → the authoritative `questionId → conditionId` map (works for
  **every** adapter, including NegRisk — it is read from chain, never derived);
- `UmaCtfAdapter.QuestionInitialized` (V2 + NegRisk + Legacy) → `(adapter, requestTimestamp) → conditionId`;
- `OptimisticOracleV2.{ProposePrice, DisputePrice, Settle}` → the proposal/dispute/settle events, whose
  `conditionId` is resolved via the lookup above (no keccak derivation — which fails for NegRisk).

Reconciliation: the indexer's resolved `finalOutcome` matches `moose-code` `condition.payoutNumerators`
at **pass_rate = 1.0000 on 29,349 eligible V2/Legacy markets**.

## Schema (`disputes.parquet`)
| column | type | notes |
|---|---|---|
| `conditionId` | string | Gnosis CTF conditionId — the effective join key to `moose-code` `condition.id` / `market_data.condition`. For NegRisk this is the **tradeable** conditionId (oracle = NegRiskAdapter), recovered on-chain; for V2/Legacy it is the native conditionId. |
| `questionId` | string | UMA question id (the OOv2 request id; for NegRisk it maps to the tradeable conditionId via the Operator — see below) |
| `adapter` | string | `v2` · `negrisk` · `legacy` (the UMA CTF adapter that owns the request) |
| `hf_joinable` | bool | `true` iff `conditionId` ∈ `moose-code` `condition` (all adapters; false only when the NegRisk map could not resolve a market, or the market post-dates the HF cutoff) |
| `category` | string | coarse keyword-derived category (crypto/politics/sports/…); null when not joinable |
| `disputeTs` | int64 | dispute timestamp (epoch seconds) |
| `disputeDate` | string | `YYYY-MM-DD` |
| `round` | int | reset round (0 = first request; bumps on each two-strikes reset) |
| `disputer` / `proposer` | string | on-chain addresses |
| `proposedOutcome` | string | the disputed proposal: `YES` / `NO` / `UNRESOLVABLE` / `OTHER` |
| `preDisputePrice` / `postDisputePrice` / `realizedJumpLogit` | double | optional fill-tape price context (joinable only; null unless exported `--with-price-context`) |

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
