"""execution/proposal_feed.py — TTL caching, manual trigger file, watch-set filtering."""
import time

from execution.proposal_feed import ConfirmedProposalDetector
from execution.testnet_chain import FleetMarket

ADDR = "0x" + "cc" * 20


def _fleet():
    return [FleetMarket(address=ADDR, deployed_block=1, category="politics",
                        tracks_cid="0xcid-tracked")]


def _wait_refresh(det, timeout=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with det._lock:
            if not det._refreshing and det._until > 0:
                return
        time.sleep(0.01)


def test_detects_confirmed_dispute_for_tracked_cid(tmp_path):
    det = ConfirmedProposalDetector(_fleet(), ttl_s=600.0,
                                    fetch=lambda: [{"conditionId": "0xcid-tracked"}],
                                    manual_path=str(tmp_path / "T"))
    det("0xcid-tracked")                        # first call kicks off the async refresh; its own
    _wait_refresh(det)                          # answer may be stale-False or fresh-True (a race)
    assert det("0xcid-tracked") is True         # served from the refreshed cache


def test_untracked_cid_never_fires_and_never_scans(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return [{"conditionId": "0xother"}]

    det = ConfirmedProposalDetector(_fleet(), fetch=fetch, manual_path=str(tmp_path / "T"))
    assert det("0xnot-watched") is False
    assert calls == []                           # unwatched cids don't trigger the heavy scan


def test_ttl_serves_stale_cache_without_rescanning(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return [{"conditionId": "0xcid-tracked"}]

    now = [1000.0]
    det = ConfirmedProposalDetector(_fleet(), ttl_s=600.0, fetch=fetch,
                                    manual_path=str(tmp_path / "T"), clock=lambda: now[0])
    det("0xcid-tracked")
    _wait_refresh(det)
    for _ in range(5):
        assert det("0xcid-tracked") is True
    assert len(calls) == 1                       # inside TTL: no re-scan
    now[0] += 601.0
    det("0xcid-tracked")
    _wait_refresh(det)
    assert len(calls) == 2                       # TTL expiry: exactly one more scan


def test_failed_scan_serves_stale_and_reports_error(tmp_path):
    state = {"fail": False}

    def fetch():
        if state["fail"]:
            raise RuntimeError("rpc down")
        return [{"conditionId": "0xcid-tracked"}]

    now = [1000.0]
    det = ConfirmedProposalDetector(_fleet(), ttl_s=600.0, fetch=fetch,
                                    manual_path=str(tmp_path / "T"), clock=lambda: now[0])
    det("0xcid-tracked")
    _wait_refresh(det)
    assert det("0xcid-tracked") is True
    state["fail"] = True
    now[0] += 601.0
    det("0xcid-tracked")
    _wait_refresh(det)
    assert det("0xcid-tracked") is True          # stale cache still served
    assert "rpc down" in det.status()["error"]


def test_manual_trigger_file_fires_immediately(tmp_path):
    trigger = tmp_path / "DISPUTE_TRIGGERS"
    det = ConfirmedProposalDetector(_fleet(), fetch=lambda: [],
                                    manual_path=str(trigger))
    assert det("0xcid-tracked") is False
    trigger.write_text("0xcid-tracked\n")
    assert det("0xcid-tracked") is True          # no scan needed, no TTL wait
    assert det("0xother") is False
