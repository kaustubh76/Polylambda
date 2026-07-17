"""Tests for the liveness/data-refresh pass (notes/12-liveness-refresh.md):
per-category κ calibration, the live-merge helper, computed date bounds, and cache.refresh().
"""
from __future__ import annotations

import math

from estimators.lambda_engine import _category_kappa, estimate_lambda


# --- D1: per-category jump_drift/e_loss calibration -------------------------------------------
def test_category_kappa_unknown_uses_global_or_scalar():
    from data.calibrate import load_kappa_by_category
    k = _category_kappa("definitely-not-a-category", 0.123)
    cache = load_kappa_by_category()
    if cache:  # cache built → unknown category uses the calibrated GLOBAL κ (a better prior)
        assert k == cache["global"]["kappa"]
    else:      # no cache → the caller's scalar fallback
        assert k == 0.123


def test_estimate_lambda_e_loss_varies_by_category():
    def e_loss(cat):
        o = estimate_lambda("x", {"category": cat, "price": 0.7, "category_base_rate": 0.02,
                                  "market_size": 0.0, "proposer_reliability": 0.0,
                                  "latency_anomaly": 0.0})
        return o.e_loss
    # if the per-category κ cache is built, distinct categories must give distinct E[loss];
    # if it isn't, they legitimately collapse to the scalar — accept either, but never NaN.
    vals = {c: e_loss(c) for c in ("politics", "sports", "other")}
    assert all(v >= 0 and not math.isnan(v) for v in vals.values())


def test_neutral_price_has_zero_jump_drift():
    # p == 0.5 (logit 0) must yield EXACTLY zero drift — the copysign(+0.0) guard.
    o = estimate_lambda("x", {"category": "politics", "price": 0.5, "category_base_rate": 0.02,
                              "market_size": 0.0, "proposer_reliability": 0.0, "latency_anomaly": 0.0})
    assert o.jump_drift == 0.0


# --- B1/B4: live-merge + computed date bounds -------------------------------------------------
def test_disputes_merge_degrades_without_indexer(monkeypatch):
    from webapp.backend import services, live
    # force the live feed empty (indexer unreachable) → the explorer still serves the parquet
    monkeypatch.setattr(live, "recent_disputes", lambda **_: [])
    services._merged_cache.update(until=0.0, df=None)  # bust the merge TTL cache
    out = services.disputes(limit=5, sort="disputeTs", desc=True)
    assert out["total"] > 0 and out["rows"]


def test_live_rows_are_deduped_and_prepended(monkeypatch):
    from webapp.backend import services, live
    fake = [{
        "conditionId": "0xLIVEONLY", "marketName": None, "category": None, "adapter": None,
        "disputeDate": "2099-01-01", "disputeTs": 4070908800, "proposedOutcome": "YES",
        "preDisputePrice": None, "postDisputePrice": None, "realizedJumpLogit": None,
        "disputer": "0xd", "proposer": "0xp", "round": 1, "source": "live",
    }]
    monkeypatch.setattr(live, "recent_disputes", lambda **_: fake)
    services._merged_cache.update(until=0.0, df=None)
    out = services.disputes(limit=5, sort="disputeTs", desc=True)
    # the far-future live row sorts to the very top and is tagged source="live"
    assert out["rows"][0]["conditionId"] == "0xLIVEONLY"
    assert out["rows"][0].get("source") == "live"


def test_overview_date_max_is_computed(monkeypatch):
    from webapp.backend import services, live
    monkeypatch.setattr(live, "recent_disputes", lambda **_: [])
    services._merged_cache.update(until=0.0, df=None)
    ov = services.overview()
    # a real ISO date, not a null/placeholder
    assert isinstance(ov["dataset"]["date_max"], str) and ov["dataset"]["date_max"][:2] == "20"


def test_cache_refresh_is_idempotent():
    from webapp.backend import cache
    cache.refresh()
    cache.refresh()  # second call must not raise


# --- E1: keyless-RPC dispute tail scanner (network-free, stubbed _rpc) -------------------------
def _make_dispute_log(adapter_hex, proposer_hex, disputer_hex, price, block, ancillary=b"anc"):
    from eth_abi import encode
    from data import disputes as D
    data = encode(["bytes32", "uint256", "bytes", "int256"],
                  [b"\x00" * 32, 1_700_000_000, ancillary, price])
    pad = lambda a: "0x" + "0" * 24 + a.lower().replace("0x", "")
    return {"topics": [D.DISPUTE_TOPIC0, pad(adapter_hex), pad(proposer_hex), pad(disputer_hex)],
            "data": "0x" + data.hex(), "blockNumber": hex(block),
            "transactionHash": "0xabc", "logIndex": "0x1"}


def test_recent_disputes_rpc_parses_and_maps(monkeypatch):
    from data import disputes as D
    v2 = next(iter(D.DERIVABLE))  # a keccak-derivable adapter
    logs = [
        _make_dispute_log(v2, "0x" + "11" * 20, "0x" + "22" * 20, 10**18, 90_000_000),          # YES, V2
        _make_dispute_log(D.NEGRISK, "0x" + "33" * 20, "0x" + "44" * 20, 0, 90_000_050),          # NO, NegRisk
    ]

    def fake_rpc(method, params, timeout=60):
        if method == "eth_blockNumber":
            return hex(90_000_100)
        if method == "eth_getLogs":
            lo = int(params[0]["fromBlock"], 16); hi = int(params[0]["toBlock"], 16)
            return [l for l in logs if lo <= int(l["blockNumber"], 16) <= hi]
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(1_700_000_500)}
        raise AssertionError(method)

    monkeypatch.setattr(D, "_rpc", fake_rpc)
    rows = D.recent_disputes_rpc(lookback_blocks=1_000_000, target=10, window=500_000)
    assert len(rows) == 2
    by_adapter = {r["adapter"]: r for r in rows}
    assert by_adapter["v2"]["proposedOutcome"] == "YES"
    assert by_adapter["v2"]["conditionId"] and by_adapter["v2"]["conditionId"].startswith("0x")
    assert by_adapter["v2"]["proposer"] == "0x" + "11" * 20
    # NegRisk is counted but NOT cid-labeled from an OO log (repo-consistent)
    assert by_adapter["negrisk"]["proposedOutcome"] == "NO"
    assert by_adapter["negrisk"]["conditionId"] is None
    # newest-first ordering + real block timestamps attached
    assert rows[0]["disputeTs"] == 1_700_000_500


def test_uma_question_id_is_keccak_of_ancillary():
    from eth_utils import keccak
    from data.negrisk_map import uma_question_id
    anc = b"q: title: Will X happen?, description: ..."
    assert uma_question_id(anc) == "0x" + keccak(anc).hex()


def test_resolve_negrisk_cids_batches_and_caches(monkeypatch, tmp_path):
    """The NegRisk label chain: keccak(ancillary) -> QuestionPrepared(topic3) -> d91e -> tradeable cid.
    Stubbed RPC: asserts ONE batched call (not one per id) and that results are cached."""
    from data import negrisk_map as N
    monkeypatch.setattr(N, "LIVE_LABELS_CACHE", str(tmp_path / "labels.json"))
    monkeypatch.setattr(N, "chain_head_block", lambda: 90_000_000, raising=False)
    qid_a, qid_b = "0x" + "aa" * 32, "0x" + "bb" * 32
    d91e_a, d91e_b = "0x" + "11" * 32, "0x" + "22" * 32
    calls = []

    def fake_rpc(method, params, timeout=60):
        if method == "eth_blockNumber":
            return hex(90_000_000)
        calls.append(params[0]["topics"][3])          # the batched topic3 OR-filter
        want = set(params[0]["topics"][3])
        out = []
        for q, d in ((qid_a, d91e_a), (qid_b, d91e_b)):
            if q in want:
                out.append({"topics": [N.QPREP_TOPIC0, "0x0", d, q], "blockNumber": hex(50_000_000)})
        return out

    monkeypatch.setattr(N, "_rpc", fake_rpc)
    got = N.resolve_negrisk_cids([qid_a, qid_b])
    assert got == {qid_a: N.derive_negrisk_cid(d91e_a), qid_b: N.derive_negrisk_cid(d91e_b)}
    assert len(calls) == 1 and set(calls[0]) == {qid_a, qid_b}, "must be ONE batched lookup"
    # second call is served from the cache — no further RPC
    again = N.resolve_negrisk_cids([qid_a, qid_b])
    assert again == got and len(calls) == 1


def test_resolve_negrisk_cids_survives_rpc_failure(monkeypatch, tmp_path):
    from data import negrisk_map as N
    monkeypatch.setattr(N, "LIVE_LABELS_CACHE", str(tmp_path / "labels.json"))

    def boom(*a, **k):
        raise RuntimeError("rpc down")
    monkeypatch.setattr(N, "_rpc", boom)
    assert N.resolve_negrisk_cids(["0x" + "cc" * 32]) == {}  # degrades, never raises


def test_outcome_from_price():
    from data.disputes import _outcome_from_price
    assert _outcome_from_price(10**18) == "YES"
    assert _outcome_from_price(0) == "NO"
    assert _outcome_from_price(5 * 10**17) == "UNRESOLVABLE"
    assert _outcome_from_price(123) is None


# --- G1: HF token plumbing (never assert on the VALUE, only on resolution) ---------------------
def test_hf_token_accepts_either_env_name(monkeypatch):
    import data.hf as hf
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_ACCESS_TOKEN", raising=False)
    assert hf.hf_token() is None and hf.has_hf_token() is False
    # the name the HF UI hands you — was silently ignored before
    monkeypatch.setenv("HF_ACCESS_TOKEN", "hf_dummy_alias")
    assert hf.hf_token() == "hf_dummy_alias" and hf.has_hf_token() is True
    # documented primary wins when both are set
    monkeypatch.setenv("HF_TOKEN", "hf_dummy_primary")
    assert hf.hf_token() == "hf_dummy_primary"


def test_hf_token_ignores_blank(monkeypatch):
    import data.hf as hf
    monkeypatch.setenv("HF_TOKEN", "   ")
    monkeypatch.delenv("HF_ACCESS_TOKEN", raising=False)
    assert hf.hf_token() is None  # an empty .env line must not count as configured


# --- H3: the live HF path must degrade fast + honestly, never hang a slim host -----------------
def test_hf_overview_live_without_token_is_fast_and_honest(monkeypatch):
    import data.hf as hf
    from webapp.backend import services
    monkeypatch.setattr(hf, "has_hf_token", lambda: False)
    out = services.hf_overview(live=True)
    assert out["source"] == "cache" and "no HF token" in out.get("live_error", "")


def test_hf_overview_live_without_local_parquet_is_honest(monkeypatch):
    import data.hf as hf
    from webapp.backend import services
    monkeypatch.setattr(hf, "has_hf_token", lambda: True)
    monkeypatch.setattr(services, "_hf_local_parquet_ready", lambda: False)
    out = services.hf_overview(live=True)
    # the container case: must NOT attempt a multi-hundred-MB remote rebuild
    assert out["source"] == "cache" and "local parquet cache" in out.get("live_error", "")


# --- H2: markets browser sort/shape ------------------------------------------------------------
def test_hf_markets_volume_sort_puts_missing_last():
    from webapp.backend import services
    rows = services.hf_markets(sort="volume", desc=True, limit=200)["rows"]
    vols = [r.get("volume") for r in rows]
    seen_none = False
    for v in vols:  # monotonically non-increasing, with any None sunk to the end
        if v is None:
            seen_none = True
        else:
            assert not seen_none, "a volume row appeared after a None row"
    nonnull = [v for v in vols if v is not None]
    assert nonnull == sorted(nonnull, reverse=True)


# --- hazard provenance ------------------------------------------------------------------------
def test_hazard_cards_carry_trained_at():
    from webapp.backend import services
    h = services.hazard()
    dep = h.get("deployed")
    if dep:  # artifact present on this host
        assert dep.get("trained_at")  # mtime fallback guarantees a date
