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
