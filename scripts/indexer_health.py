#!/usr/bin/env python3
"""indexer_health — is the hosted Envio indexer actually AT HEAD, or a stopped deploy?

The exact failure this catches: a reachable-but-stalled dev deploy whose head froze days ago while
the dashboard still shows "LIVE". Reports head age + whether block_height is advancing past
latest_processed_block (a running indexer keeps block_height ahead of processed; a stopped one has
them pinned equal). Exit code is non-zero when the head is staler than --max-age-min, so this doubles
as a CI/cron gate.

Usage:
  python scripts/indexer_health.py                       # uses INDEXER_GRAPHQL_URL / the dev default
  python scripts/indexer_health.py --url <graphql> --max-age-min 30
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

DEFAULT_URL = (os.environ.get("INDEXER_GRAPHQL_URL")
               or os.environ.get("HOSTED_GRAPHQL_URL")
               or "https://indexer.dev.hyperindex.xyz/0638687/v1/graphql")


def _gql(url: str, query: str, timeout: float = 10.0) -> dict:
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (operator-supplied url)
        payload = json.loads(resp.read())
    if payload.get("errors"):
        raise RuntimeError(str(payload["errors"][0].get("message", "graphql error")))
    return payload.get("data") or {}


def check(url: str) -> dict:
    head = _gql(url, "{ Dispute(limit: 1, order_by: {disputeTs: desc}) { id disputeTs } }")
    d = (head.get("Dispute") or [])
    head_ts = int(d[0]["disputeTs"]) if d else None
    meta = {}
    try:  # chain_metadata is Envio-standard; absent on some schemas → best-effort
        m = _gql(url, "{ chain_metadata { chain_id block_height latest_processed_block } }")
        rows = m.get("chain_metadata") or []
        meta = rows[0] if rows else {}
    except Exception:
        pass
    age = (int(time.time()) - head_ts) if head_ts else None
    bh, lpb = meta.get("block_height"), meta.get("latest_processed_block")
    return {
        "url": url, "head_ts": head_ts, "head_age_seconds": age,
        "head_age_days": round(age / 86400, 2) if age is not None else None,
        "block_height": bh, "latest_processed_block": lpb,
        # a running indexer keeps block_height climbing ahead of processed; equal ⇒ likely stalled
        "advancing": (bh is not None and lpb is not None and int(bh) > int(lpb)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--max-age-min", type=float, default=30.0,
                    help="fail (exit 1) if the head is older than this many minutes")
    args = ap.parse_args(argv)
    try:
        r = check(args.url)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"url": args.url, "reachable": False, "error": str(e)[:200]}, indent=2))
        return 2
    print(json.dumps({"reachable": True, **r}, indent=2))
    age = r["head_age_seconds"]
    if age is None:
        print("WARN: no disputes indexed yet", file=sys.stderr)
        return 1
    if age > args.max_age_min * 60:
        print(f"STALE: head is {r['head_age_days']}d old (> {args.max_age_min}min); "
              f"advancing={r['advancing']} — the indexer has likely stopped.", file=sys.stderr)
        return 1
    print("OK: indexer is at head.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
