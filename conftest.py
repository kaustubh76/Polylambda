# Presence of this file makes the repo root the pytest rootdir, so `from pricing.quote import ...`
# resolves when running `pytest` from the project root.
import pytest


@pytest.fixture(scope="session", autouse=True)
def _disable_live_rpc_tail():
    """Keep the whole test session hermetic: the webapp's background RPC dispute-tail scan
    (webapp.backend.live._refresh_tail_async → data.disputes.recent_disputes_rpc) does REAL Polygon
    network I/O and, as a daemon thread, would otherwise outlive the test that spawned it — leaking a
    slow scan across tests and racing their _rpc monkeypatch. Disable spawns for the session; the one
    test that exercises the mechanism (test_tail_scan_lifecycle_stops_and_resumes) re-arms locally and
    restores the disabled state. Best-effort: pure-data test runs that never import the webapp skip it."""
    try:
        from webapp.backend import live
    except Exception:  # noqa: BLE001 — webapp not importable in this run → nothing to disable
        yield
        return
    live.stop_tail()
    yield
    live.stop_tail()
