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
    # HF `condition` contains everything EXCEPT the NegRisk conditionId (the structural gap)
    monkeypatch.setattr(dz, "query", lambda sql, params=None: [("0xcv2",), ("0xcleg",), ("0xcoth",)])

    rows = dz.load_disputes_from_indexer("http://x")
    by_cid = {r["conditionId"]: r for r in rows}
    assert by_cid["0xcv2"]["adapter"] == "v2"
    assert by_cid["0xcnr"]["adapter"] == "negrisk"
    assert by_cid["0xcleg"]["adapter"] == "legacy"
    assert by_cid["0xcoth"]["adapter"] == OTHER.lower()      # unmapped -> raw address, not "unknown"
    assert by_cid["0xcv2"]["hf_joinable"] is True
    assert by_cid["0xcnr"]["hf_joinable"] is False           # NegRisk absent from HF
    # joinable_only drops the NegRisk label row
    joinable = dz.load_disputes_from_indexer("http://x", joinable_only=True)
    assert {r["conditionId"] for r in joinable} == {"0xcv2", "0xcleg", "0xcoth"}


# --- recon: NegRisk phantom-keyed markets have no HF-comparable payout → no_ground_truth (not a mismatch) ---

def test_recon_no_ground_truth_bucket(monkeypatch):
    import data.conditions as dc
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
    monkeypatch.setattr(rc, "_fetch_indexed_markets", lambda url, **kw: markets)
    monkeypatch.setattr(dc, "hf_payout_map", lambda: {"A": "1,0", "E": "0,1"})  # B (phantom) deliberately absent

    rep = rc.run_recon("http://x")
    assert rep.eligible == 2 and rep.matched == 1          # A,E eligible; only A matches
    assert rep.pass_rate == 0.5
    assert rep.excluded_no_ground_truth == 1               # Bphantom: NegRisk phantom key -> data-gap bucket
    assert rep.excluded_pending == 1                       # C
    assert rep.excluded_unsupported_adapter == 1           # D
    assert any(m[0] == "E" for m in (rep.mismatches or []))
