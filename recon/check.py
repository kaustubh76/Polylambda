"""
recon — reconciliation invariant, ELIGIBLE-SET version (see ../DECISIONS.md #10).

A flat "100% of all markets" gate is infeasible (async/bimodal resolution, reorgs, multi-adapter
joins). Instead:

  For every market that is SETTLED on-chain AND past a confirmation depth AND on a SUPPORTED
  adapter:  indexed finalOutcome == on-chain reportPayouts vector.

  Report exclusion buckets as first-class metrics (do NOT silently drop):
    pending · in-dispute · reorg-window · unsupported-adapter.

Ground truth: the HF dataset `condition.payoutNumerators` (data.conditions.hf_payout_map) — the exact
vector ConditionalTokens.ConditionResolution emits, i.e. the same thing the local indexer stores as
Market.finalOutcome. Both sides read the SAME on-chain event, so a mismatch is a real indexing bug,
not a representation artifact (the raw vectors match regardless of payout-denominator scaling). RPC
is used only for the recent reorg tail that the HF batch snapshot may not have captured yet.

Target = 100% on the ELIGIBLE set; track each bucket's size. Run this as a HARD GATE before any
estimator consumes indexed data (training sigma/lambda on silently-wrong data wastes days).
"""
from __future__ import annotations

from dataclasses import dataclass

# Supported adapters (DECISIONS.md §D). Markets prepared by any other oracle are excluded, not failed.
SUPPORTED_ADAPTERS = {
    "0x6a9d222616c90fca5754cd1333cfd9b7fb6a4f74",  # UMA CTF Adapter V2
    "0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d",  # Neg Risk UMA CTF Adapter
    "0x71392e133063cc0d16f40e1f9b60227404bc03f7",  # Legacy UMA CTF Adapter
}


@dataclass
class ReconReport:
    eligible: int
    matched: int
    excluded_pending: int
    excluded_in_dispute: int
    excluded_reorg_window: int
    excluded_unsupported_adapter: int
    mismatches: list | None = None  # (conditionId, indexed, expected) for the first few failures

    @property
    def pass_rate(self) -> float:
        return 1.0 if self.eligible == 0 else self.matched / self.eligible


def _fetch_indexed_markets(graphql_url: str) -> list[dict]:
    """Pull every Market the local indexer knows, with status/finalOutcome/oracle/resolvedAt."""
    import requests

    q = """query { Market { id status finalOutcome oracle resolvedAt } }"""
    r = requests.post(graphql_url, json={"query": q}, timeout=60)
    r.raise_for_status()
    return r.json()["data"]["Market"]


def run_recon(graphql_url: str, rpc_url: str = "", confirmation_depth: int = 128,
              *, chain_head_ts: int | None = None) -> ReconReport:
    """Compare each eligible indexed Market.finalOutcome to the HF on-chain payout vector.

    Eligibility: status RESOLVED, oracle in SUPPORTED_ADAPTERS, and (if chain_head_ts given)
    resolved older than the reorg window. Everything else is counted in an exclusion bucket.
    """
    from data.conditions import hf_payout_map

    truth = hf_payout_map()  # {conditionId: "1,0"/"0,1"/... } — loaded once
    markets = _fetch_indexed_markets(graphql_url)

    eligible = matched = 0
    b_pending = b_dispute = b_reorg = b_adapter = 0
    mismatches: list = []

    for m in markets:
        status = (m.get("status") or "").upper()
        if status in ("OPEN", "REQUESTED", "PROPOSED", "RESET"):
            b_pending += 1
            continue
        if status == "DISPUTED":
            b_dispute += 1
            continue
        if status != "RESOLVED":
            b_pending += 1
            continue
        if (m.get("oracle") or "").lower() not in SUPPORTED_ADAPTERS:
            b_adapter += 1
            continue
        resolved_at = int(m.get("resolvedAt") or 0)
        if chain_head_ts is not None and resolved_at and (chain_head_ts - resolved_at) < confirmation_depth:
            b_reorg += 1  # too fresh — HF snapshot may lag; would spot-check via rpc_url here
            continue
        eligible += 1
        expected = truth.get(m["id"])
        if expected is not None and m.get("finalOutcome") == expected:
            matched += 1
        elif len(mismatches) < 20:
            mismatches.append((m["id"], m.get("finalOutcome"), expected))

    return ReconReport(eligible, matched, b_pending, b_dispute, b_reorg, b_adapter, mismatches)


if __name__ == "__main__":
    import os

    rep = run_recon(os.environ.get("GRAPHQL_URL", "http://localhost:8080/v1/graphql"),
                    os.environ.get("POLYGON_RPC_URL", ""))
    print(f"pass_rate={rep.pass_rate:.4f} on {rep.eligible} eligible "
          f"(matched {rep.matched}); excluded pending={rep.excluded_pending} "
          f"dispute={rep.excluded_in_dispute} reorg={rep.excluded_reorg_window} "
          f"adapter={rep.excluded_unsupported_adapter}")
    for cid, got, want in (rep.mismatches or [])[:10]:
        print(f"  MISMATCH {cid}: indexed={got} expected={want}")
