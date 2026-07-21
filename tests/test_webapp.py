"""End-to-end tests for the dashboard backend — hits the real engine through the FastAPI app.

Fully offline & deterministic: paper mode + shipped artifacts + the offline DI installed in the
app lifespan. Mirrors the honesty invariants the rest of the suite enforces (e.g. paper P&L excludes
the sim reward score; every session record is `simulated: true`).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webapp.backend.main import app


@pytest.fixture(scope="module")
def client():
    # `with` triggers the lifespan → installs the offline DI + warms caches.
    with TestClient(app) as c:
        yield c


def test_overview_tiles(client):
    d = client.get("/api/overview").json()
    assert d["mode"] == "paper"
    assert len(d["tiles"]) == 4
    # the SHIPPED total (the layer runs to chain head), not the λ-eligible count — they diverge by
    # design since the base rates are pinned to the frozen HF window. The tile names both.
    assert d["dataset"]["total_disputes"] == 1848
    disputes_tile = next(t for t in d["tiles"] if t["label"] == "OOv2 disputes indexed")
    assert disputes_tile["value"] == 1848
    assert "1,794 in λ window" in disputes_tile["sub"], "the tile must not imply λ used all 1,848"
    assert "dX" in d["jump_diffusion"]


def test_baserates_ordered_and_ci_bracketed(client):
    d = client.get("/api/baserates").json()
    rows = d["rows"]
    assert len(rows) >= 6
    # every rate sits inside its own Wilson interval (epsilon on BOTH bounds: a 0.0 rate can yield a
    # tiny-positive ci_low ~1e-20 from float rounding, which a bare `ci_low <= rate` would fail)
    for r in rows:
        assert r["ci_low"] - 1e-9 <= r["rate"] <= r["ci_high"] + 1e-9
        assert r["resolved"] > 0
    # sorted descending by rate
    assert rows == sorted(rows, key=lambda r: r["rate"], reverse=True)
    # politics is far more dispute-prone than crypto (the signal)
    by = {r["category"]: r["rate"] for r in rows}
    assert by["politics"] > by["crypto"] * 5


def test_score_returns_real_lambda_and_valid_quote(client):
    body = {"category": "politics", "fill_count": 800, "price": 0.62, "inventory": 60, "horizon_days": 5}
    d = client.post("/api/lambda/score", json=body).json()
    lam = d["lambda"]
    for k in ("lambda_select", "lambda_jump", "jump_drift", "e_loss", "ci_low", "ci_high"):
        assert k in lam
    # lambda_select is the category base rate; ci brackets it
    assert lam["ci_low"] <= lam["lambda_select"] <= lam["ci_high"] + 1e-9
    q = d["quote"]
    assert 0 < q["bid"] < q["mid"] < q["ask"] < 1       # a valid two-sided quote
    assert d["exit_gate"]["would_exit"] in (True, False)


def test_score_exit_gate_flat_when_no_inventory(client):
    d = client.post("/api/lambda/score", json={"category": "politics", "fill_count": 500,
                                               "price": 0.6, "inventory": 0, "horizon_days": 5}).json()
    assert d["exit_gate"]["would_exit"] is False
    assert "flat" in d["exit_gate"]["reason"].lower()


def test_dispute_defense_scenario_protects_capital(client):
    d = client.post("/api/session/run", json={"scenario": "dispute_defense"}).json()
    assert d["simulated"] is True
    on = d["series"]["lambda_on"]
    off = d["series"]["lambda_off"]
    assert len(on) == len(off) >= 8
    # identical hold before the dispute (tick 0)
    assert on[0]["equity"] == pytest.approx(off[0]["equity"], abs=1e-6)
    s = d["summary"]
    # the λ-ON arm ends with a strictly smaller loss (capital protected) and the exit gate fired
    assert s["on_final_equity"] > s["off_final_equity"]
    assert s["protected"] > 0
    assert s["n_exits"] >= 1


def test_live_quoting_session_is_simulated_and_pnl_honest(client):
    d = client.post("/api/session/run", json={"scenario": "live_quoting", "n_ticks": 20, "n_markets": 4}).json()
    assert d["simulated"] is True
    totals = d["summary"]["per_arm_totals"]
    assert set(totals) == {"lambda_on", "lambda_off"}
    # honesty invariant (matches tests/test_runner): reported P&L == cash + inventory·mark and the
    # accrued sim reward score is tracked SEPARATELY, never folded into P&L.
    for arm in totals.values():
        assert arm["pnl"] == pytest.approx(arm["equity_mark"], abs=1e-6)
        assert arm["sim_reward_score"] >= 0.0


def test_ablation_shape(client):
    d = client.get("/api/ablation").json()
    assert d["meta"]["n_disputes"] == 1409
    arms = {a["arm"] for a in d["arms"]}
    assert {"lambda_jump", "diffusion_only", "lambda_select"} <= arms
    # lambda_jump beats diffusion at the tightest threshold (the edge)
    jump = next(a for a in d["arms"] if a["arm"] == "lambda_jump")
    diff = next(a for a in d["arms"] if a["arm"] == "diffusion_only")
    assert jump["points"][0]["pnl_net_of_rewards"] > diff["points"][0]["pnl_net_of_rewards"]


def test_hazard_deployed_vs_matched_null(client):
    d = client.get("/api/hazard").json()
    assert d["deployed"]["holdout_auc"] > 0.65          # deployed discriminates
    assert d["matched_eval"]["holdout_auc"] < 0.6       # the proposer null collapses to ~coin-flip
    assert d["deployed"]["discriminates"] is True


def test_disputes_filter_and_names(client):
    d = client.get("/api/disputes", params={"category": "politics", "limit": 5}).json()
    assert d["total"] > 100
    assert all(r["category"] == "politics" for r in d["rows"])
    assert "adapter" in d["facets"] and "year" in d["facets"]


def test_recon_and_sigma(client):
    r = client.get("/api/recon").json()
    assert r["recon"]["pass_rate"] == 1.0
    assert r["hf_joinable_pct"] == 100.0
    s = client.get("/api/sigma").json()
    assert s["n"] > 0 and len(s["categories"]) >= 5


# --- testnet fleet + keeper endpoints (offline: no registry network, fake keeper) ----------------
def test_fleet_endpoint_graceful_without_registry(client, monkeypatch, tmp_path):
    from execution import testnet_chain as tc
    from webapp.backend import chain as chain_mod
    monkeypatch.setattr(tc, "MARKETS_JSON", tmp_path / "missing.json")
    monkeypatch.delenv("MARKETS_JSON", raising=False)
    chain_mod._cache.pop("fleet", None)          # bust the 5s cache from any earlier call
    d = client.get("/api/testnet/fleet").json()
    assert d["markets"] == [] and "note" in d    # graceful: no registry -> empty, no error


def test_keeper_status_and_risk_endpoints_offline(client, monkeypatch, tmp_path):
    from execution import testnet_keeper as tk
    from execution.risk import RiskGovernor, RiskLimits
    fake = tk.TestnetKeeper(interval_s=1.0)
    fake.risk = RiskGovernor(RiskLimits(kill_switch_path=str(tmp_path / "KILL")),
                             ledger_dir=str(tmp_path / "risk"))
    monkeypatch.setattr(tk, "_keeper", fake)
    d = client.get("/api/testnet/keeper").json()
    assert d["running"] is False and d["ticks_done"] == 0
    r = client.get("/api/testnet/risk").json()
    assert r["killed"] is False and r["halted"] is False

    # kill writes the file; every later allow_tx is denied; unkill removes it
    k = client.post("/api/testnet/kill").json()
    assert k["killed"] is True and (tmp_path / "KILL").exists()
    u = client.post("/api/testnet/unkill").json()
    assert u["removed"] is True and not (tmp_path / "KILL").exists()


def test_keeper_run_endpoint_reports_already_running(client, monkeypatch, tmp_path):
    from execution import testnet_keeper as tk

    class _Fake(tk.TestnetKeeper):
        @property
        def running(self):
            return True

    fake = _Fake(interval_s=1.0)
    monkeypatch.setattr(tk, "_keeper", fake)
    d = client.post("/api/testnet/keeper/run", json={"ticks": 5}).json()
    assert d["started"] is False and d["running"] is True
