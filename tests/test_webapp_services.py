"""webapp/backend/services.py — smoke tests that lock the JSON contracts the dashboard depends on
for the endpoints added in the feature-wiring rounds (proposers, dispute analytics, quote curve,
the frozen-config expansion) and the live-with-fallback behavior (recon_live, ablation). These
assert shape + graceful degradation, not exact numbers."""
import os

import pytest

from webapp.backend import services
from webapp.backend import cache


def test_frozen_config_exposes_full_knob_set():
    cfg, source = cache.frozen_config()
    assert source in ("live", "published")
    # the expanded set (not just the headline nine)
    for k in ("gamma", "k", "kappa", "lambda_star", "kappa_loss", "sigma_ref",
              "quote_size", "reduce_fraction", "positioning", "mode"):
        assert k in cfg


def test_proposers_leaderboard_shape():
    out = services.proposers(limit=5)
    assert set(out) >= {"rows", "total_proposers", "note"}
    assert len(out["rows"]) <= 5
    if out["rows"]:
        r = out["rows"][0]
        assert set(r) == {"proposer", "disputes"}
        # sorted descending by dispute count
        counts = [row["disputes"] for row in out["rows"]]
        assert counts == sorted(counts, reverse=True)


def test_disputes_analytics_shape():
    out = services.disputes_analytics(bins=8)
    assert "n" in out
    if out["n"]:
        assert isinstance(out.get("histogram", []), list)
        assert isinstance(out.get("scatter", []), list)
        # scatter is capped for payload size
        assert len(out.get("scatter", [])) <= 600
        for pt in out.get("histogram", []):
            assert {"x0", "x1", "n"} <= set(pt)


def test_quote_curve_skews_with_inventory():
    out = services.quote_curve(category="politics", price=0.62, steps=5)
    assert set(out) >= {"points", "mid", "sigma", "lambda_jump"}
    pts = out["points"]
    assert len(pts) == 5
    for p in pts:
        assert p["bid"] < p["ask"]                       # a valid two-sided quote
    # long inventory (last point) is skewed below flat/short inventory (first point)
    assert pts[-1]["mid"] <= pts[0]["mid"]


def test_ablation_published_shape_and_arms():
    out = services.ablation(live=False)
    assert out["source"] == "published"
    assert out["arms"], "expected at least the published arms"
    arm0 = out["arms"][0]
    assert {"arm", "arm_label", "points"} <= set(arm0)
    assert arm0["points"] and {"lambda_star", "pnl_net_of_rewards", "sharpe"} <= set(arm0["points"][0])


def test_ablation_live_falls_back_honestly_without_indexer(monkeypatch):
    monkeypatch.delenv("INDEXER_GRAPHQL_URL", raising=False)
    monkeypatch.delenv("ENVIO_GRAPHQL_URL", raising=False)
    out = services.ablation(live=True)
    assert out["source"] == "published"
    assert "no INDEXER_GRAPHQL_URL" in out.get("live_error", "")


def test_recon_live_falls_back_to_published_without_indexer(monkeypatch):
    monkeypatch.delenv("INDEXER_GRAPHQL_URL", raising=False)
    monkeypatch.delenv("ENVIO_GRAPHQL_URL", raising=False)
    out = services.recon_live()
    assert out["source"] == "published"
    # published recon still carries the by-category breakdown the UI renders
    assert "by_category" in out


def test_ablation_full_reader_is_none_or_rows():
    rows = services._ablation_full_rows()
    assert rows is None or (isinstance(rows, list) and rows)
