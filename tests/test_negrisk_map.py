"""
Offline tests for data/negrisk_map.py. Network scans are not exercised here; these lock the pure
derivation and the resilient chunk-splitter that the NegRisk↔HF join depends on.
"""
import pytest

pytest.importorskip("eth_utils")

from data.negrisk_map import (
    CANARY_TRADEABLE_CID,
    CANARY_UMA_QID,
    NEGRISK_ADAPTER,
    NEGRISK_ADAPTER_V2,
    NEGRISK_V2_START_BLOCK,
    QPREP_TOPIC0,
    _adapter_for_block,
    derive_negrisk_cid,
)


def test_derive_negrisk_cid_matches_onchain_pair():
    # The one pair validated end-to-end (HF market_data join + ConditionPreparation agreement):
    # UMA questionId 0x7ccc… -> d91e question id topic2 -> tradeable conditionId 0xca92….
    qid_d91e = "0xea795ebc33b30185f3bf95f194afa970ae7aea64ea288ff636776b17ddf7b902"
    assert derive_negrisk_cid(qid_d91e) == CANARY_TRADEABLE_CID


def test_derive_negrisk_cid_accepts_unprefixed_and_is_deterministic():
    q = "ea795ebc33b30185f3bf95f194afa970ae7aea64ea288ff636776b17ddf7b902"
    assert derive_negrisk_cid(q) == derive_negrisk_cid("0x" + q) == CANARY_TRADEABLE_CID
    # a different d91e question id must not collide onto the canary cid
    assert derive_negrisk_cid("0x" + "00" * 32) != CANARY_TRADEABLE_CID
    assert len(derive_negrisk_cid(q)) == 66


def test_canary_constants_shape():
    assert CANARY_UMA_QID.startswith("0x") and len(CANARY_UMA_QID) == 66
    assert QPREP_TOPIC0 == "0xcdc45423ec79c60a3fe3de57272e598d71a4ec88822e822ac8e134184a8435aa"


def test_get_logs_resilient_splits_on_error(monkeypatch):
    import data.negrisk_map as nm

    calls = []

    def fake_rpc(method, params, timeout=60):
        frm = int(params[0]["fromBlock"], 16)
        to = int(params[0]["toBlock"], 16)
        calls.append((frm, to))
        # fail on any wide range; succeed (empty) once the splitter narrows below 25k
        if to - frm > 25_000:
            raise RuntimeError({"code": -32603, "message": "Internal server error"})
        return []

    monkeypatch.setattr(nm, "_rpc", fake_rpc)
    monkeypatch.setattr(nm.time, "sleep", lambda *_: None)
    out = nm._get_logs_resilient(0, 100_000, log=None)
    assert out == []
    # it must have recursively narrowed to <=25k windows and covered the whole span
    assert any(to - frm <= 25_000 for frm, to in calls)
    covered = sorted(c for c in calls if c[1] - c[0] <= 25_000)
    assert covered[0][0] == 0 and covered[-1][1] == 100_000


# --- CLOB-v2 adapter era-switch (DECISIONS.md §C.16) --------------------------------------------

def test_derive_defaults_to_v1_historical_key():
    # The default adapter is the v1 NegRiskAdapter — the immutable historical derivation key. Passing
    # it explicitly must reproduce the default so the historical build stays byte-identical.
    qid_d91e = "0xea795ebc33b30185f3bf95f194afa970ae7aea64ea288ff636776b17ddf7b902"
    assert (derive_negrisk_cid(qid_d91e, NEGRISK_ADAPTER)
            == derive_negrisk_cid(qid_d91e) == CANARY_TRADEABLE_CID)


def test_v2_adapter_derivation_differs_from_v1():
    # CLOB-v2-era markets prepare under a different adapter → a different (non-colliding) tradeable cid.
    qid_d91e = "0xea795ebc33b30185f3bf95f194afa970ae7aea64ea288ff636776b17ddf7b902"
    v2 = derive_negrisk_cid(qid_d91e, NEGRISK_ADAPTER_V2)
    assert v2 != CANARY_TRADEABLE_CID and len(v2) == 66


def test_adapter_for_block_switches_at_cutover():
    assert _adapter_for_block(NEGRISK_V2_START_BLOCK - 1) == NEGRISK_ADAPTER      # pre-cutover → v1
    assert _adapter_for_block(NEGRISK_V2_START_BLOCK) == NEGRISK_ADAPTER_V2        # at/after → v2
    assert _adapter_for_block(NEGRISK_V2_START_BLOCK + 10) == NEGRISK_ADAPTER_V2


def test_historical_build_window_precedes_v2_cutover():
    # build_negrisk_map() ends at HF_CUTOFF_BLOCK; it must sit strictly below the v2 cutover so every
    # historical question derives under v1 — the released artifact + canary stay byte-identical.
    import data.negrisk_map as nm

    assert nm.MAP_END_BLOCK < NEGRISK_V2_START_BLOCK
