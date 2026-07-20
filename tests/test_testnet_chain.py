"""execution/testnet_chain.py — fleet registry parsing, signer guards, nonce-race retry."""
import json

import pytest

from execution import testnet_chain as tc

ADDR1 = "0x" + "a1" * 20
ADDR2 = "0x" + "b2" * 20
KEY = "0x" + "11" * 32  # throwaway test key, never funded


# ---------------------------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------------------------
def test_load_fleet_parses_registry(tmp_path):
    p = tmp_path / "markets.json"
    p.write_text(json.dumps({"abi": [{"name": "snapshot"}], "markets": [
        {"address": ADDR1, "deployed_block": 100, "category": "crypto",
         "tracks_cid": "0xcid", "end_date_ts": 123.0, "keeper_managed": True},
        {"address": ADDR2, "deployed_block": 200, "category": "politics",
         "keeper_managed": False, "label": "unmanaged"},
    ]}))
    markets, abi = tc.load_fleet(p)
    assert abi == [{"name": "snapshot"}]
    assert len(markets) == 2
    m1, m2 = markets
    assert m1.token_id == f"tn-{ADDR1[:10].lower()}"  # default token_id
    assert m1.tracks_cid == "0xcid" and m1.keeper_managed is True
    assert m2.keeper_managed is False and m2.label == "unmanaged"  # the keeper never signs these


def test_load_fleet_empty_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "MARKETS_JSON", tmp_path / "missing.json")
    monkeypatch.delenv("MARKETS_JSON", raising=False)
    assert tc.load_fleet() == ([], [])


def test_append_market_creates_fleet_only_registry(tmp_path):
    reg = tmp_path / "markets.json"
    entry = {"address": ADDR2, "deployed_block": 300, "category": "crypto",
             "tracks_cid": None, "end_date_ts": 0.0, "keeper_managed": True}
    doc = tc.append_market(entry, abi=[{"x": 1}], path=reg)
    # fresh registry contains ONLY the appended fleet entry — no legacy demo import
    assert [m["address"] for m in doc["markets"]] == [ADDR2]
    assert doc["markets"][0]["keeper_managed"] is True
    # duplicates refused, existing entries never rewritten
    with pytest.raises(ValueError, match="already in registry"):
        tc.append_market(entry, abi=[], path=reg)
    assert json.loads(reg.read_text()) == doc


# ---------------------------------------------------------------------------------------------
# signer
# ---------------------------------------------------------------------------------------------
class _Hex:
    def __init__(self, s="ab" * 32):
        self._s = s

    def hex(self):
        return self._s


class FakeEth:
    def __init__(self, chain_id=80002, fail_sends=0):
        self.chain_id = chain_id
        self._fail_sends = fail_sends
        self.sent = 0

    def get_transaction_count(self, addr):
        return 7

    def send_raw_transaction(self, raw):
        if self._fail_sends > 0:
            self._fail_sends -= 1
            raise ValueError("nonce too low: address already used")
        self.sent += 1
        return _Hex()

    def wait_for_transaction_receipt(self, h, timeout=120):
        return {"status": 1, "gasUsed": 50_000, "effectiveGasPrice": 30 * 10**9,
                "blockNumber": 4242}


class FakeW3:
    def __init__(self, **kw):
        self.eth = FakeEth(**kw)

    def to_wei(self, n, unit):
        assert unit == "gwei"
        return int(n) * 10**9


class FakeFn:
    def build_transaction(self, base):
        return {**base, "to": "0x" + "00" * 20, "gas": 21000, "data": b""}


def test_signer_refuses_without_key():
    s = tc.AmoySigner(FakeW3(), key="")
    with pytest.raises(RuntimeError, match="ENGINE_PRIVATE_KEY"):
        s.send(FakeFn())


def test_signer_refuses_wrong_chain():
    s = tc.AmoySigner(FakeW3(chain_id=137), key=KEY)
    with pytest.raises(RuntimeError, match="not Amoy"):
        s.send(FakeFn())


def test_signer_sends_and_accounts_gas():
    w3 = FakeW3()
    out = tc.AmoySigner(w3, key=KEY).send(FakeFn())
    assert out["tx"].startswith("0x") and out["block"] == 4242
    assert out["gas_pol"] == pytest.approx(50_000 * 30e9 / 1e18)
    assert w3.eth.sent == 1


def test_signer_retries_once_on_nonce_race():
    w3 = FakeW3(fail_sends=1)
    out = tc.AmoySigner(w3, key=KEY).send(FakeFn())
    assert out["tx"].startswith("0x") and w3.eth.sent == 1


def test_signer_gives_up_after_two_nonce_failures():
    w3 = FakeW3(fail_sends=2)
    with pytest.raises(ValueError, match="nonce too low"):
        tc.AmoySigner(w3, key=KEY).send(FakeFn())


def test_deploy_fleet_cid_picker_offline():
    """scripts/deploy_fleet.pick_tracked_cids — real cids per category from the released layer."""
    import sys
    sys.path.insert(0, "scripts")
    from deploy_fleet import pick_tracked_cids

    out = pick_tracked_cids(["politics", "crypto"])
    assert set(out) <= {"politics", "crypto"} and out, "at least one category resolved"
    assert all(c.startswith("0x") and len(c) == 66 for c in out.values())
