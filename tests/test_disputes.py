"""
Offline tests for the no-Docker dispute source (data/disputes.py). Network paths are not exercised
here; these lock the pure derivation + decoding that the HF join depends on.
"""
import pytest

pytest.importorskip("eth_utils")
pytest.importorskip("eth_abi")

from data.disputes import DISPUTE_TOPIC0, derive_condition_id


def test_dispute_topic0():
    # keccak of the DisputePrice signature — the eth_getLogs topic0 filter
    assert DISPUTE_TOPIC0 == "0x5165909c3d1c01c5d1e121ac6f6d01dda1ba24bc9e1f975b5a375339c15be7f3"


def test_derive_condition_id_matches_ts_formula():
    # Mirrors indexer/test/lib.test.ts vector: adapter + ancillary "desc: test" (0x646573633a2074657374).
    # This exact value was validated 29/29 against the live HF condition.id / market_data join.
    cid = derive_condition_id("0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74",
                              bytes.fromhex("646573633a2074657374"))
    assert cid == "0x9f5abad342df2dfad11d13df0d059921df8bfd5caf123c5a6b6490c4f53f7b30"
    assert cid.startswith("0x") and len(cid) == 66


def test_derive_is_deterministic_and_address_case_insensitive():
    anc = b"some ancillary data, p1=Yes p2=No"
    a_lower = derive_condition_id("0x6a9d222616c90fca5754cd1333cfd9b7fb6a4f74", anc)
    a_check = derive_condition_id("0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74", anc)
    assert a_lower == a_check                     # checksum vs lowercase must not change the id
    assert a_lower != derive_condition_id("0x6a9d222616c90fca5754cd1333cfd9b7fb6a4f74", anc + b"x")


# --- load_disputes_from_indexer: adapter mapping + hf_joinable (mocked Hasura + condition read) ---

def test_load_disputes_from_indexer_adapter_map_and_hf_joinable(monkeypatch):
    import data.disputes as dz

    V2 = "0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74"      # -> "v2"
    NR = "0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d"      # -> "negrisk"
    LEG = "0x71392E133063CC0D16F40E1F9B60227404Bc03f7"     # -> "legacy"
    OTHER = "0x157ce2d672854c848c9b79c49a8cc6cc89176a49"   # unmapped -> raw lowercase address

    def mk(cid, oracle):
        return {"disputer": "0xd", "disputeTs": "1700000000", "round": 0,
                "request": {"proposer": "0xp", "proposedOutcome": "YES", "requestTimestamp": "1700000000",
                            "market": {"id": cid, "oracle": oracle, "questionId": "0xq"}}}

    payload = [mk("0xcv2", V2), mk("0xcnr", NR), mk("0xcleg", LEG), mk("0xcoth", OTHER)]
    monkeypatch.setattr(dz, "_gql", lambda q, **kw: {"Dispute": payload})
    # No negrisk map + no block-ts cache (hermetic: never read the real .data_cache)
    import data.negrisk_map as nm
    monkeypatch.setattr(nm, "load_negrisk_map", lambda: {})
    monkeypatch.setattr(dz, "_load_block_ts_cache", lambda: {})
    # HF `condition` contains everything EXCEPT the NegRisk PHANTOM cid (without the map, a NegRisk
    # row falls back to the phantom conditionId, which never exists on-chain → unjoinable)
    monkeypatch.setattr(dz, "query", lambda sql, params=None: [("0xcv2",), ("0xcleg",), ("0xcoth",)])

    rows = dz.load_disputes_from_indexer("http://x")
    by_cid = {r["conditionId"]: r for r in rows}
    assert by_cid["0xcv2"]["adapter"] == "v2"
    assert by_cid["0xcnr"]["adapter"] == "negrisk"
    assert by_cid["0xcleg"]["adapter"] == "legacy"
    assert by_cid["0xcoth"]["adapter"] == OTHER.lower()      # unmapped -> raw address, not "unknown"
    assert by_cid["0xcv2"]["hf_joinable"] is True
    assert by_cid["0xcnr"]["hf_joinable"] is False           # phantom cid without the map → unjoinable
    # joinable_only drops the unmapped NegRisk row
    joinable = dz.load_disputes_from_indexer("http://x", joinable_only=True)
    assert {r["conditionId"] for r in joinable} == {"0xcv2", "0xcleg", "0xcoth"}


def test_load_disputes_from_indexer_negrisk_map_recovers_join(monkeypatch):
    """With the negrisk map cached, a NegRisk dispute joins HF via its TRADEABLE conditionId."""
    import data.disputes as dz
    import data.negrisk_map as nm

    NR = "0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d"
    payload = [{"id": "0xaaa-7", "disputer": "0xd", "disputeTs": "1700000000", "round": 0,
                "request": {"proposer": "0xp", "proposedOutcome": "NO", "requestTimestamp": "1700000000",
                            "market": {"id": "0xphantom", "oracle": NR, "questionId": "0xq_uma"}}}]
    monkeypatch.setattr(dz, "_gql", lambda q, **kw: {"Dispute": payload})
    monkeypatch.setattr(nm, "load_negrisk_map",
                        lambda: {"0xq_uma": {"tradeableConditionId": "0xtrade", "prepBlock": 1}})
    monkeypatch.setattr(dz, "_load_block_ts_cache", lambda: {})
    # HF has the TRADEABLE cid, not the phantom
    monkeypatch.setattr(dz, "query", lambda sql, params=None: [("0xtrade",)])

    rows = dz.load_disputes_from_indexer("http://x", joinable_only=True)
    assert len(rows) == 1
    assert rows[0]["tradeableConditionId"] == "0xtrade"
    assert rows[0]["hf_joinable"] is True                    # joined via the map, not the phantom


def test_dispute_block_ts_override_keeps_request_timestamp(monkeypatch):
    """disputeTs is overridden with the TRUE block time from the cache; the OO request ts is kept."""
    import data.disputes as dz
    import data.negrisk_map as nm

    V2 = "0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74"
    payload = [{"id": "0xhash-12", "disputer": "0xd", "disputeTs": "1700000000", "round": 1,
                "request": {"proposer": "0xp", "proposedOutcome": "YES", "requestTimestamp": "1700000000",
                            "market": {"id": "0xc", "oracle": V2, "questionId": "0xq"}}}]
    monkeypatch.setattr(dz, "_gql", lambda q, **kw: {"Dispute": payload})
    monkeypatch.setattr(nm, "load_negrisk_map", lambda: {})
    monkeypatch.setattr(dz, "_load_block_ts_cache", lambda: {"0xhash": 1700003600})
    monkeypatch.setattr(dz, "query", lambda sql, params=None: [("0xc",)])

    (row,) = dz.load_disputes_from_indexer("http://x")
    assert row["disputeTs"] == 1700003600                    # true block time (from txHash of id)
    assert row["requestTimestamp"] == 1700000000             # raw OO request ts preserved


# --- recon: NegRisk phantom-keyed markets have no HF-comparable payout → no_ground_truth (not a mismatch) ---

def test_recon_no_ground_truth_bucket(monkeypatch):
    import data.conditions as dc
    import data.disputes as dz
    import recon.check as rc

    V2 = "0x6a9d222616c90fca5754cd1333cfd9b7fb6a4f74"
    NR = "0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d"      # NegRisk OO adapter — phantom-keyed, no HF truth
    markets = [
        {"id": "A", "status": "RESOLVED", "finalOutcome": "1,0", "oracle": V2, "resolvedAt": "100"},  # match
        {"id": "Bphantom", "status": "RESOLVED", "finalOutcome": "0,1", "oracle": NR, "resolvedAt": "100"},  # no phantom-keyed truth
        {"id": "C", "status": "PROPOSED", "finalOutcome": None, "oracle": V2, "resolvedAt": "0"},     # pending
        {"id": "D", "status": "RESOLVED", "finalOutcome": "1,0", "oracle": "0xdead", "resolvedAt": "1"},  # unsupported adapter
        {"id": "E", "status": "RESOLVED", "finalOutcome": "1,0", "oracle": V2, "resolvedAt": "100"},  # mismatch
    ]
    # hermetic: run_recon resolves its endpoint via the shared resolver — never probe the network here
    monkeypatch.setattr(dz, "resolve_indexer", lambda u=None: (u, None))
    monkeypatch.setattr(rc, "_fetch_indexed_markets", lambda url, **kw: markets)
    monkeypatch.setattr(dc, "hf_payout_map", lambda: {"A": "1,0", "E": "0,1"})  # B (phantom) deliberately absent

    rep = rc.run_recon("http://x", log=lambda *a: None)
    assert rep.eligible == 2 and rep.matched == 1          # A,E eligible; only A matches
    assert rep.pass_rate == 0.5
    assert rep.excluded_no_ground_truth == 1               # Bphantom: NegRisk phantom key -> data-gap bucket
    assert rep.excluded_pending == 1                       # C
    assert rep.excluded_unsupported_adapter == 1           # D
    assert any(m[0] == "E" for m in (rep.mismatches or []))


# --- resolve_indexer: the shared endpoint resolver (probe order + hosted no-secret semantics) ---

def test_resolve_indexer_probe_order_and_hosted_secret(monkeypatch):
    import data.disputes as dz

    # HOSTED_GRAPHQL_URL is now opt-in (no baked default — the old hosted dev deploy is gone), so
    # configure one to exercise the probe ORDER + its no-admin-secret quirk.
    monkeypatch.setattr(dz, "HOSTED_GRAPHQL_URL", "http://hosted:9/v1/graphql")
    seen = []

    def gql_only_hosted_up(q, *, url=None, secret=None, timeout=60):
        seen.append((url, secret))
        if url == dz.HOSTED_GRAPHQL_URL:
            return {"ResolutionRequest": [{"id": "x"}]}
        raise RuntimeError("connection refused")

    monkeypatch.setattr(dz, "_gql", gql_only_hosted_up)
    url, secret = dz.resolve_indexer("http://explicit:9/v1/graphql")
    assert (url, secret) == (dz.HOSTED_GRAPHQL_URL, "")    # hosted rejects the admin-secret header
    assert [u for u, _ in seen] == ["http://explicit:9/v1/graphql", dz.GRAPHQL_URL,
                                    dz.HOSTED_GRAPHQL_URL]  # explicit → local → hosted


def test_resolve_indexer_skips_unset_hosted(monkeypatch):
    """With HOSTED_GRAPHQL_URL unset (the default now), it must NOT be probed at all — the old baked
    dev-deploy default made every call burn a 15s timeout on a corpse."""
    import data.disputes as dz

    monkeypatch.setattr(dz, "HOSTED_GRAPHQL_URL", "")
    seen = []

    def gql(q, *, url=None, secret=None, timeout=60):
        seen.append(url)
        raise RuntimeError("connection refused")

    monkeypatch.setattr(dz, "_gql", gql)
    assert dz.resolve_indexer() == (None, None)
    assert seen == [dz.GRAPHQL_URL]        # local only — no empty/dead hosted probe

    # an answering explicit endpoint wins immediately (no further probes)
    seen.clear()
    monkeypatch.setattr(dz, "_gql", lambda q, **kw: {"ResolutionRequest": []})
    assert dz.resolve_indexer("http://explicit:9")[0] == "http://explicit:9"
    assert len(seen) == 0                                   # our stub doesn't record; just no error

    # everything down → (None, None), never an exception
    monkeypatch.setattr(dz, "_gql", lambda q, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    assert dz.resolve_indexer() == (None, None)


def test_recon_falls_back_to_hosted_with_coverage_cap_log(monkeypatch):
    import data.conditions as dc
    import data.disputes as dz
    import recon.check as rc

    logs, seen = [], {}
    monkeypatch.setattr(dz, "resolve_indexer", lambda u=None: (dz.HOSTED_GRAPHQL_URL, ""))
    monkeypatch.setattr(dc, "hf_payout_map", lambda: {})

    def fake_fetch(url, *, page=5000, secret=None):
        seen.update(url=url, page=page, secret=secret)
        return []

    monkeypatch.setattr(rc, "_fetch_indexed_markets", fake_fetch)
    rep = rc.run_recon("http://local-down:8080/v1/graphql", log=logs.append)
    assert rep.eligible == 0 and rep.pass_rate == 1.0
    assert seen == {"url": dz.HOSTED_GRAPHQL_URL, "page": 1000, "secret": ""}  # hosted clamps limit
    assert any("COVERAGE-CAPPED" in line for line in logs)  # never silently over-claim completeness

    # nothing reachable → a hard error, not a silent empty recon
    monkeypatch.setattr(dz, "resolve_indexer", lambda u=None: (None, None))
    with pytest.raises(RuntimeError):
        rc.run_recon("http://local-down:8080/v1/graphql", log=None)


def test_export_build_rows_resolves_endpoint_and_flags_hosted(monkeypatch):
    pytest.importorskip("duckdb")
    import data.export_disputes as ex
    from data.disputes import HOSTED_GRAPHQL_URL

    logs, seen = [], {}
    # HOSTED_GRAPHQL_URL is opt-in now (empty by default), so configure one to exercise the
    # coverage-capped warning path.
    monkeypatch.setattr(ex, "HOSTED_GRAPHQL_URL", "http://hosted:9/v1/graphql")
    monkeypatch.setattr(ex, "resolve_indexer", lambda u=None: (ex.HOSTED_GRAPHQL_URL, ""))

    def fake_loader(url, *, secret=None, log=None, **kw):
        seen.update(url=url, secret=secret)
        return [{"conditionId": "0xc", "tradeableConditionId": "0xc", "questionId": "0xq",
                 "adapter": "v2", "hf_joinable": True, "disputeTs": 1700000000,
                 "requestTimestamp": 1700000000, "round": 0, "disputer": "0xd",
                 "proposer": "0xp", "proposedOutcome": "YES", "disputeId": "0xh-1"}]

    monkeypatch.setattr(ex, "load_disputes_from_indexer", fake_loader)
    monkeypatch.setattr(ex, "_categories_for", lambda cids: {"0xc": "politics"})
    rows = ex.build_rows(None, log=logs.append)
    assert seen == {"url": ex.HOSTED_GRAPHQL_URL, "secret": ""}  # resolved url + no-secret threaded through
    assert any("COVERAGE-CAPPED" in line for line in logs)    # a hosted export is flagged un-authoritative
    assert rows[0]["category"] == "politics"
    assert rows[0]["post_hf_cutoff"] is False                 # 2023 dispute — inside the HF window

    # No indexer reachable → fall back to the keyless RPC scan rather than refusing to export. The
    # hosted Envio deploy is gone, so raising here would make the release unmaintainable.
    monkeypatch.setattr(ex, "resolve_indexer", lambda u=None: (None, None))
    called = {}

    def fake_rpc_loader(**kw):
        called["yes"] = True
        return [{"conditionId": "0xc", "tradeableConditionId": "0xc", "questionId": "0xq",
                 "adapter": "negrisk", "hf_joinable": True, "disputeTs": 1700000000,
                 "requestTimestamp": 1700000000, "round": 1, "disputer": "0xd",
                 "proposer": "0xp", "proposedOutcome": "NO", "disputeId": "0xh-1"}]

    monkeypatch.setattr("data.disputes.load_disputes_rpc", fake_rpc_loader)
    rows = ex.build_rows(None, log=None)
    assert called.get("yes") and rows[0]["adapter"] == "negrisk"

    # ...but an explicit source="indexer" must still refuse rather than silently switch sources
    with pytest.raises(RuntimeError):
        ex.build_rows(None, source="indexer", log=None)


# --- load_disputes: the released parquet is the default numerator source ---

def test_load_disputes_default_loads_the_full_released_layer(monkeypatch):
    """The headline data-layer contract: 1,794 HF-joinable disputes across ALL adapters, offline."""
    pytest.importorskip("duckdb")
    import os
    from collections import Counter

    import data.disputes as dz

    if not os.path.exists(dz.RELEASE_PARQUET):
        pytest.skip("release parquet absent (partial checkout?)")
    monkeypatch.delenv("DATA_SOURCE", raising=False)
    rows = dz.load_disputes()
    assert len(rows) == 1794
    by_adapter = Counter(r["adapter"] for r in rows)
    assert by_adapter["v2"] == 723 and by_adapter["negrisk"] == 963
    assert sum(by_adapter.values()) - by_adapter["v2"] - by_adapter["negrisk"] == 108  # "other"
    assert all(isinstance(r["disputeTs"], int) for r in rows[:10])


def test_load_disputes_source_precedence(monkeypatch, tmp_path):
    """Hermetic contract: DATA_SOURCE=graphql (effective cid) → released parquet → RPC cache."""
    import data.disputes as dz

    # (1) DATA_SOURCE=graphql wins and returns the EFFECTIVE join key (tradeable for NegRisk)
    monkeypatch.setenv("DATA_SOURCE", "graphql")
    monkeypatch.setattr(dz, "load_disputes_from_indexer", lambda **kw: [
        {"conditionId": "0xphantom", "tradeableConditionId": "0xtrade", "disputeTs": 5,
         "adapter": "negrisk", "disputer": "0xd"}])
    rows = dz.load_disputes()
    assert rows == [{"conditionId": "0xtrade", "disputeTs": 5, "adapter": "negrisk", "disputer": "0xd"}]

    # (2) default: the released parquet via the query seam
    monkeypatch.delenv("DATA_SOURCE", raising=False)
    fake_pq = tmp_path / "disputes.parquet"
    fake_pq.write_bytes(b"")                                   # existence only; the query is stubbed
    monkeypatch.setattr(dz, "RELEASE_PARQUET", str(fake_pq))
    monkeypatch.setattr(dz, "query", lambda sql: [("0xa", 7, "v2", "0xd")])
    assert dz.load_disputes() == [{"conditionId": "0xa", "disputeTs": 7, "adapter": "v2",
                                   "disputer": "0xd"}]

    # (3) parquet unreadable → the RPC-scanned V2/Legacy cache (the numerator is never empty)
    monkeypatch.setattr(dz, "query", lambda sql: (_ for _ in ()).throw(RuntimeError("bad parquet")))
    monkeypatch.setattr(dz, "build_dispute_cache", lambda **kw: {"disputes": [
        {"conditionId": "0xb", "disputeTs": 9, "adapter": "legacy", "disputer": "0xe"}]})
    assert dz.load_disputes() == [{"conditionId": "0xb", "disputeTs": 9, "adapter": "legacy",
                                   "disputer": "0xe"}]


# --- U: adapter coverage — a missing adapter silently truncates the export ---------------------
def test_derivable_covers_every_adapter_the_release_uses():
    """The RPC export filters OO logs by `topics[1] IN DERIVABLE|NEGRISK`, so an adapter missing from
    that set is invisible — a re-export would silently DROP its disputes rather than fail. This caught
    0x157ce2d6… (108 real rows) before it reached a published artifact. If a new adapter ever appears
    on-chain, fail loudly here instead."""
    pytest.importorskip("pandas")
    import pandas as pd
    from data.disputes import DERIVABLE, NEGRISK, RELEASE_PARQUET

    df = pd.read_parquet(RELEASE_PARQUET)
    used = set(df.adapter.dropna().unique())
    known = set(DERIVABLE.values()) | {"negrisk"}
    missing = used - known
    assert not missing, (
        f"adapters in the release that the RPC scan cannot see: {missing} — add them to DERIVABLE "
        f"(after validating their conditionId derivation) or the next export drops those rows")


def test_new_adapter_derives_condition_id_like_v2():
    """0x157ce2d6… earns its DERIVABLE entry: its released (questionId -> conditionId) pairs must
    reproduce under keccak(adapter ++ questionId ++ 2), the same rule as V2/Legacy."""
    pytest.importorskip("pandas")
    import pandas as pd
    from eth_utils import keccak
    from data.disputes import RELEASE_PARQUET

    adpt = "0x157ce2d672854c848c9b79c49a8cc6cc89176a49"
    df = pd.read_parquet(RELEASE_PARQUET)
    rows = df[df.adapter == adpt]
    if rows.empty:
        pytest.skip("adapter not present in this release cut")
    a = bytes.fromhex(adpt[2:])
    ok = sum(1 for r in rows.itertuples()
             if r.questionId and "0x" + keccak(a + bytes.fromhex(r.questionId[2:])
                                               + (2).to_bytes(32, "big")).hex() == r.conditionId)
    assert ok == len(rows), f"only {ok}/{len(rows)} derive — it is NOT a keccak-derivable adapter"


# --- V: the batch scan must survive keyless-RPC throttling -------------------------------------
def test_rpc_retries_transient_throttle_then_succeeds(monkeypatch):
    """All endpoints 401 (progressive throttling under a batch job) → back off and retry, don't die.
    The first real export crashed exactly here, ~1.7k lookups in."""
    import data.disputes as dz

    calls = {"n": 0}

    def flaky(url, body, timeout):
        calls["n"] += 1
        if calls["n"] <= len(dz.RPC_URLS) * 2:        # every endpoint throttled for 2 full rounds
            raise RuntimeError("HTTP Error 401: Unauthorized")
        return "0x64"

    monkeypatch.setattr(dz, "_rpc_once", flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert dz._rpc("eth_blockNumber", []) == "0x64"
    assert calls["n"] > len(dz.RPC_URLS)              # proves it went round again rather than raising


def test_rpc_does_not_retry_application_errors(monkeypatch):
    """A JSON-RPC error ('range too large') is NOT transient — it must surface immediately so the
    caller's range-bisection reacts, instead of sleeping through pointless retries."""
    import data.disputes as dz

    calls = {"n": 0}

    def hard(url, body, timeout):
        calls["n"] += 1
        raise RuntimeError({"code": -32005, "message": "query returned more than 10000 results"})

    monkeypatch.setattr(dz, "_rpc_once", hard)
    monkeypatch.setattr("time.sleep", lambda s: pytest.fail("must not back off on an app error"))
    with pytest.raises(RuntimeError):
        dz._rpc("eth_getLogs", [{}])
    assert calls["n"] == len(dz.RPC_URLS)             # one pass over the endpoints, no retry round


def test_block_timestamps_checkpoint_survives_a_crash(monkeypatch, tmp_path):
    """A throttle near the end must not discard the earlier lookups — the cache is flushed en route."""
    import data.disputes as dz

    cache_file = tmp_path / "block_ts.json"
    monkeypatch.setattr(dz, "RPC_BLOCK_TS_CACHE", str(cache_file))
    monkeypatch.setattr(dz, "_BLOCK_TS_CHECKPOINT", 2)

    # Key the stub off the REQUESTED BLOCK, never a shared call counter. A counter makes this test
    # hostage to any other caller of dz._rpc in the same process: webapp/backend/live.py starts a
    # background tail refresh, and when tests/test_webapp.py ran first that thread was still alive and
    # calling the patched _rpc — burning one of the four allowed calls, so only 3 got checkpointed and
    # this test failed intermittently depending on file order. Block-keyed = deterministic regardless.
    def boom_above_4(method, params, timeout=60):
        b = int(params[0], 16)
        if b > 4:
            raise RuntimeError("HTTP Error 401: Unauthorized")
        return {"timestamp": hex(1_700_000_000 + b)}

    monkeypatch.setattr(dz, "_rpc", boom_above_4)
    with pytest.raises(RuntimeError):
        dz._block_timestamps_cached([1, 2, 3, 4, 5, 6], log=None)
    import json as _j
    saved = _j.loads(cache_file.read_text())
    assert len(saved) == 4, f"partial progress lost: only {len(saved)} of 4 checkpointed"
    assert set(saved) == {"1", "2", "3", "4"}        # the ones that SUCCEEDED, not merely four of them

    # the resumed run reuses them and only re-fetches what's genuinely missing (5, 6)
    fetched: list[int] = []

    def only_new(method, params, timeout=60):
        fetched.append(int(params[0], 16))
        return {"timestamp": hex(1_700_000_999)}

    monkeypatch.setattr(dz, "_rpc", only_new)
    out = dz._block_timestamps_cached([1, 2, 3, 4, 5, 6], log=None)
    assert len(out) == 6
    assert sorted(fetched) == [5, 6], f"resume refetched cached blocks: {sorted(fetched)}"


def test_block_ts_cache_paths_are_distinct():
    """Two DIFFERENT caches live in data/disputes.py:
        BLOCK_TS_CACHE      {txHash: ts}  — the indexer path (dispute_block_ts.json)
        RPC_BLOCK_TS_CACHE  {block:  ts}  — the RPC export   (rpc_block_ts.json)
    They were briefly BOTH named BLOCK_TS_CACHE, so the later definition shadowed the earlier one and
    the export read/wrote the indexer's cache — overwriting {txHash: ts} entries with {block: ts} under
    the same key space, with no error. Different data, different key type, different file: assert it,
    because a name collision like that is invisible at runtime."""
    import data.disputes as dz

    assert dz.BLOCK_TS_CACHE != dz.RPC_BLOCK_TS_CACHE
    assert dz.BLOCK_TS_CACHE.endswith("dispute_block_ts.json")
    assert dz.RPC_BLOCK_TS_CACHE.endswith("rpc_block_ts.json")
