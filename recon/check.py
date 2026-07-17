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
    excluded_no_ground_truth: int = 0  # RESOLVED + supported adapter, but HF has no payout vector
    mismatches: list | None = None  # (conditionId, indexed, expected) for the first few failures

    @property
    def pass_rate(self) -> float:
        return 1.0 if self.eligible == 0 else self.matched / self.eligible


def _fetch_indexed_markets(graphql_url: str, *, page: int = 5000, secret: str | None = None) -> list[dict]:
    """Pull every Market the local indexer knows (paginated), with status/finalOutcome/oracle/resolvedAt.

    Uses the Hasura admin secret (env HASURA_ADMIN_SECRET, default 'testing') and pages via
    limit/offset so the full 70k+ Market set is returned, not a single truncated page.

    ORDER BY IS LOAD-BEARING, NOT COSMETIC. limit/offset over an unordered relation has no stable row
    order, so successive pages silently overlap and omit rows — the pull returns a different subset
    every run. Measured before the fix: four consecutive recon runs over the SAME stalled indexer
    reported eligible = 23,259 / 27,311 / 30,632 / 35,977. pass_rate stayed 1.0 (the rows that came
    back did match), which is exactly why this hid: the headline claim looked stable while its
    denominator wandered by 50%. `stats.json`'s published recon.eligible was one draw from that.
    """
    import json
    import os
    import urllib.request

    secret = os.environ.get("HASURA_ADMIN_SECRET", "testing") if secret is None else secret
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["x-hasura-admin-secret"] = secret
    out: list[dict] = []
    offset = 0
    while True:
        q = ("query { Market(limit: %d, offset: %d, order_by: {id: asc}) "
             "{ id status finalOutcome oracle resolvedAt } }" % (page, offset))
        req = urllib.request.Request(graphql_url, data=json.dumps({"query": q}).encode(), headers=headers)
        payload = None
        for attempt in range(3):   # per-page retry — a transient blip (IncompleteRead/reset) on one
            try:                   # page of a 70k-market paginated pull must not drop the whole run
                with urllib.request.urlopen(req, timeout=120) as r:
                    payload = json.loads(r.read())
                break
            except Exception:
                if attempt == 2:
                    raise
        if payload.get("errors"):
            raise RuntimeError(payload["errors"])
        rows = payload["data"]["Market"]
        if not rows:
            break
        out.extend(rows)
        offset += len(rows)
        if len(rows) < page:
            break
    return out


def run_recon(graphql_url: str, rpc_url: str = "", confirmation_depth: int = 128,
              *, chain_head_ts: int | None = None, log=print) -> ReconReport:
    """Compare each eligible indexed Market.finalOutcome to the HF on-chain payout vector.

    Eligibility: status RESOLVED, oracle in SUPPORTED_ADAPTERS, and (if chain_head_ts given)
    resolved older than the reorg window. Everything else is counted in an exclusion bucket.

    Endpoint: resolved via the shared `data.disputes.resolve_indexer` (explicit → local → hosted).
    A hosted hit is COVERAGE-CAPPED (1000 rows/page, aggregates off), so its pass_rate covers only
    the returned Market subset, not the full universe — logged, never silently over-claimed.
    """
    from data.conditions import hf_payout_map
    from data.disputes import HOSTED_GRAPHQL_URL, resolve_indexer

    url, secret = resolve_indexer(graphql_url)
    if url is None:
        raise RuntimeError("no indexer endpoint reachable (local Hasura and hosted HyperIndex both down)")
    hosted = url == HOSTED_GRAPHQL_URL
    if log and url != graphql_url:
        log(f"[recon] {graphql_url} unreachable -> {url}")
    if log and hosted:
        log("[recon] hosted HyperIndex is COVERAGE-CAPPED (1000 rows/page, aggregates off): "
            "pass_rate below covers only the returned Market subset, NOT the full universe")

    truth = hf_payout_map()  # {conditionId: "1,0"/"0,1"/... } — loaded once
    # hosted clamps limit to 1000 — a bigger page would end pagination after one short batch
    markets = _fetch_indexed_markets(url, page=1000 if hosted else 5000, secret=secret)

    eligible = matched = 0
    b_pending = b_dispute = b_reorg = b_adapter = b_noground = 0
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
        expected = truth.get(m["id"])
        if expected is None:
            # RESOLVED on a supported adapter, but HF carries no payout vector for THIS indexed
            # conditionId. For NegRisk this is expected and is NOT an HF-coverage gap: the indexer keys
            # the market by a PHANTOM conditionId (the 0x2f5e deriveConditionId fallback) and stores
            # finalOutcome there, whereas HF (and ConditionResolution) key the real TRADEABLE conditionId
            # — so there is no phantom-keyed payout to compare. NegRisk disputes still JOIN HF fine via
            # the tradeable cid (data/negrisk_map.py, used by the dataset export + replay); recon simply
            # can't validate the indexer's phantom-keyed finalOutcome against HF. Count as data-coverage,
            # never a mismatch. (Reconciling NegRisk finalOutcome would need the indexer to key the
            # tradeable conditionId — an indexer change, out of recon's scope.)
            b_noground += 1
            continue
        eligible += 1
        if m.get("finalOutcome") == expected:
            matched += 1
        elif len(mismatches) < 20:
            mismatches.append((m["id"], m.get("finalOutcome"), expected))

    return ReconReport(eligible, matched, b_pending, b_dispute, b_reorg, b_adapter, b_noground, mismatches)


if __name__ == "__main__":
    import os

    rep = run_recon(os.environ.get("GRAPHQL_URL", "http://localhost:8080/v1/graphql"),
                    os.environ.get("POLYGON_RPC_URL", ""))
    print(f"pass_rate={rep.pass_rate:.4f} on {rep.eligible} eligible "
          f"(matched {rep.matched}); excluded pending={rep.excluded_pending} "
          f"dispute={rep.excluded_in_dispute} reorg={rep.excluded_reorg_window} "
          f"adapter={rep.excluded_unsupported_adapter} no_ground_truth={rep.excluded_no_ground_truth}")
    for cid, got, want in (rep.mismatches or [])[:10]:
        print(f"  MISMATCH {cid}: indexed={got} expected={want}")
