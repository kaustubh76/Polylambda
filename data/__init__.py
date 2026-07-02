"""
data/ — PolyLambda's historical backbone over the public HuggingFace dataset
`moose-code/polymarket-onchain-v1` (2.74B on-chain records, 1.17B CLOB trades since Sept 2020),
queried in place with DuckDB (no 127GB download).

Why this package exists: every PolyLambda function that needs history (sigma priors, recon
ground-truth, lambda base-rates, the replay-ablation edge proof) was a stub pointing at a local
Envio GraphQL that isn't running. This layer feeds those consumers from the HF dataset instead,
while the local Envio indexer is scoped down to the ONE thing HF lacks — the OOv2 dispute
lifecycle (see ../DECISIONS.md #13). The two sources join on `conditionId`.

Public surface:
  data.hf         — connection, DATA_SOURCE switch, path resolver, verified column registry
  data.fills      — order_filled → sigma.fetch_fills-shaped dicts (deriveFill in SQL)
  data.conditions — resolved conditions + payout vectors (recon ground truth)
  data.metadata   — market/category + tokenId↔conditionId
  data.base_rates — category denominators (HF) + dispute numerators (local indexer, injected)
  data.dossier    — the reproducible dataset analysis (DATASET.md numbers)
"""
