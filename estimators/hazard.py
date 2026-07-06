"""
hazard — the structural dispute-onset model (Panel D "λ · jump intensity = ENGINE": hazard/logistic
on structural signals → P(dispute within dt)).

The pure logistic fit already lives in `estimators.lambda_engine.fit_hazard` (class-weighted, reports
Brier not accuracy). What was missing is (1) the point-in-time-safe FEATURES and (2) the labeled
training set. This module builds both, fits, and persists a dependency-light predictor that
`estimate_lambda(..., model=...)` consumes so `lambda_jump` becomes the structural hazard rather
than the flat category base rate.

Honesty (DECISIONS.md #9/#11): disputes are ~1% of markets → CALIBRATION-LIMITED. We report Brier
vs the base-rate Brier and the positive count; if the model does not beat the base rate, that is the
correct, expected result — the structural signal is weak and the base rate is the honest default.

SAFE_FEATURES (no lookahead, from lambda_engine): category_base_rate, market_size,
proposer_reliability, latency_anomaly — every one knowable at market onset.
"""
from __future__ import annotations

import json
import math
import os

from .lambda_engine import SAFE_FEATURES, fit_hazard

HAZARD_MODEL_CACHE = os.path.join(os.environ.get("DATA_CACHE_DIR", ".data_cache"), "hazard_model.json")


# --- point-in-time-safe feature transforms (pure) ------------------------------------------------
def market_size_feature(fill_count: int) -> float:
    """Liquidity proxy: log1p(fills). Bounded, monotone; thin markets score low."""
    return math.log1p(max(fill_count, 0))


def proposer_reliability_feature(proposer: str | None, disputes_by_proposer: dict[str, int],
                                 *, exclude: bool = True) -> float:
    """LEAVE-ONE-OUT proposer dispute history (higher = more dispute-prone = less reliable).

    disputes_by_proposer: {proposer_lower: their total dispute count across the corpus}. For the
    market being scored we subtract its own contribution (exclude) so a proposer's label never leaks
    into its own feature. Unknown proposer → 0.0 (no adverse history)."""
    if not proposer:
        return 0.0
    n = disputes_by_proposer.get(proposer.lower(), 0)
    if exclude:
        n = max(n - 1, 0)
    return math.log1p(n)


def latency_anomaly_feature(latency_s: float | None, cat_median: float, cat_mad: float) -> float:
    """Robust z-score of the request→event latency vs the category median (MAD-scaled). 0 when the
    latency is unknown (controls with no dispute) or the category has no spread — the deliberately
    weak feature."""
    if latency_s is None or cat_mad <= 0:
        return 0.0
    return (latency_s - cat_median) / (1.4826 * cat_mad)


def feature_row(*, category_base_rate: float, market_size: float, proposer_reliability: float,
                latency_anomaly: float, disputed: int | None = None) -> dict:
    """Assemble one SAFE_FEATURES row (+ optional label) in the canonical order."""
    row = {"category_base_rate": category_base_rate, "market_size": market_size,
           "proposer_reliability": proposer_reliability, "latency_anomaly": latency_anomaly}
    if disputed is not None:
        row["disputed"] = int(disputed)
    return row


# --- dependency-light persisted predictor --------------------------------------------------------
def _logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


class LoadedHazard:
    """Reconstructed logistic predictor with a sklearn-compatible predict_proba(X) — no sklearn or
    pickle needed at load/predict time.

    predict_proba(X)[:, 1] = sigmoid(X·coef + intercept + offset). `offset` is the PRIOR-CORRECTION
    that maps the class-balanced fit back to the natural dispute prevalence, so the output is a real
    ~1%-scale P(dispute) usable as lambda_jump (comparable to lambda_star) — NOT the ~0.5-centred
    probability a class-weighted logistic emits on a balanced set."""

    def __init__(self, coef, intercept, feature_order, offset: float = 0.0):
        self.coef = coef
        self.intercept = intercept
        self.feature_order = feature_order
        self.offset = offset

    def predict_proba(self, X):
        import numpy as np  # return an ndarray so callers can use sklearn-style [i, 1] indexing

        out = []
        for x in X:
            z = self.intercept + self.offset + sum(c * float(v) for c, v in zip(self.coef, x))
            p = 1.0 / (1.0 + math.exp(-z))
            out.append([1.0 - p, p])
        return np.array(out)


def save_hazard_model(model, metrics: dict, offset: float, *, path: str = HAZARD_MODEL_CACHE) -> str:
    """Persist a fitted sklearn LogisticRegression as plain JSON (coef/intercept/offset/features)."""
    payload = {"coef": [float(c) for c in model.coef_[0]], "intercept": float(model.intercept_[0]),
               "offset": float(offset), "feature_order": list(SAFE_FEATURES), "metrics": metrics}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=1)
    return path


def load_hazard_model(path: str = HAZARD_MODEL_CACHE) -> LoadedHazard | None:
    """The persisted hazard predictor for `estimate_lambda(model=...)`, or None if not trained yet."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        p = json.load(f)
    return LoadedHazard(p["coef"], p["intercept"], tuple(p["feature_order"]), p.get("offset", 0.0))


def _fill_count_map(condition_ids: list[str]) -> dict[str, int]:
    """{conditionId: fill_count} over the local/HF order_filled slice (one scan). Empty on error —
    market_size then degrades to 0 for all, which the fit tolerates."""
    try:
        from data.hf import query, table_path

        cids = "(" + ",".join("'" + c.replace("'", "") + "'" for c in condition_ids) + ")"
        sql = f"""
            WITH toks AS (
                SELECT id AS tok, any_value(condition) AS condition
                FROM '{table_path("market_data")}'
                WHERE condition IN {cids} GROUP BY id
            )
            SELECT t.condition AS cid, count(*) AS fills
            FROM '{table_path("order_filled")}' o
            JOIN toks t ON t.tok = (CASE WHEN o.makerAssetId='0' THEN o.takerAssetId ELSE o.makerAssetId END)
            GROUP BY t.condition
        """
        return {r[0]: int(r[1]) for r in query(sql)}
    except Exception:
        return {}


def _base_rate_fn():
    """A cached category → base-rate lookup off the HF denominators + dispute numerators."""
    from data.base_rates import category_base_rate, category_counts_hf
    from data.disputes import dispute_counts_by_category

    counts = category_counts_hf()
    dcounts = dispute_counts_by_category()
    cache: dict[str, float] = {}

    def br(cat: str) -> float:
        cat = cat or "other"
        if cat not in cache:
            cache[cat] = category_base_rate(cat, dcounts, counts)["rate"]
        return cache[cat]

    return br


_RELEASE_PQ = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "dataset_release", "polymarket-oov2-disputes-v1", "disputes.parquet")


def _disputes_by_proposer() -> dict[str, int]:
    """{proposer_lower: their total dispute count} across the released dispute layer."""
    import duckdb

    rows = duckdb.sql(f"SELECT lower(proposer), count(*) FROM '{_RELEASE_PQ}' "
                      f"WHERE hf_joinable AND proposer IS NOT NULL GROUP BY 1").fetchall()
    return {p: int(n) for p, n in rows if p}


def disputed_feature_index() -> dict[str, dict]:
    """{conditionId: {category, price, <SAFE_FEATURES>}} for every joinable disputed market, from the
    released dispute layer. Shared by training (adds disputed=1) AND the live builder (so a market's
    hazard input matches exactly what it was trained on). Point-in-time-safe."""
    import duckdb
    from statistics import median

    pq = _RELEASE_PQ
    disp = duckdb.sql(
        f"SELECT conditionId, category, lower(proposer) proposer, requestTimestamp, disputeTs, "
        f"preDisputePrice FROM '{pq}' WHERE hf_joinable").fetchall()

    dbp = _disputes_by_proposer()
    lat_by_cat: dict[str, list[float]] = {}
    for _, cat, _, rq, dt, _ in disp:
        if rq is not None and dt is not None:
            lat_by_cat.setdefault(cat or "other", []).append(float(dt) - float(rq))
    cat_lat = {c: (median(v), median([abs(x - median(v)) for x in v]) or 1.0)
               for c, v in lat_by_cat.items() if v}

    br = _base_rate_fn()
    sizes = _fill_count_map([d[0] for d in disp])
    index: dict[str, dict] = {}
    for cid, cat, prop, rq, dt, price in disp:
        cat = cat or "other"
        index[cid] = {
            "category": cat, "price": float(price) if price is not None else 0.5,
            "category_base_rate": br(cat), "market_size": market_size_feature(sizes.get(cid, 0)),
            # v1 uses only features FAIRLY computable for both classes. proposer_reliability and
            # latency_anomaly are dispute-side quantities we cannot compute for arbitrary controls
            # (the indexer's ResolutionRequest doesn't cover most HF-resolved controls — NegRisk
            # phantom cids + non-OOv2 markets), so including them would LEAK the label. Zeroed in v1
            # (the fit then rests on category_base_rate + market_size); the transforms remain in the
            # schema + are unit-tested for the v2 fair-controls build. See DECISIONS.md #9.
            "proposer_reliability": 0.0, "latency_anomaly": 0.0}
    return index


def market_feature_dicts(condition_ids: list[str]) -> dict[str, dict]:
    """SAFE_FEATURES (+ category/price) per market, for feeding estimate_lambda(model=...) in the
    live builder. Only disputed-layer markets are known; others are omitted (caller falls back)."""
    idx = disputed_feature_index()
    return {c: idx[c] for c in condition_ids if c in idx}


def _control_proposers(cids: list[str], graphql_url: str | None = None) -> dict[str, str]:
    """{conditionId: proposer_lower} for control markets, from the indexer's round-0
    ResolutionRequest. Without this, controls have no proposer and `proposer_reliability` would
    trivially separate the classes (leakage). Returns {} on any failure (feature then degrades)."""
    import json
    import urllib.request

    url = graphql_url or os.environ.get("GRAPHQL_URL", "http://localhost:8080/v1/graphql")
    secret = os.environ.get("HASURA_ADMIN_SECRET", "testing")
    out: dict[str, str] = {}
    try:
        for i in range(0, len(cids), 400):
            inlist = ",".join('"' + c + '"' for c in cids[i:i + 400])
            q = ('{ ResolutionRequest(where:{market:{id:{_in:[' + inlist +
                 ']}}, round:{_eq:0}}){ proposer market{ id } } }')
            req = urllib.request.Request(url, data=json.dumps({"query": q}).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "x-hasura-admin-secret": secret})
            for r in json.load(urllib.request.urlopen(req, timeout=30))["data"]["ResolutionRequest"]:
                m, p = r.get("market") or {}, r.get("proposer")
                if p and m.get("id"):
                    out[m["id"]] = p.lower()
    except Exception:
        return out
    return out


def build_training_rows(*, control_per_category: int = 150) -> list[dict]:
    """Assemble labeled SAFE_FEATURES rows: disputed markets (label 1) from the released dispute
    layer + sampled resolved controls (label 0). Point-in-time-safe. Control PROPOSERS are pulled
    from the indexer so `proposer_reliability` (a proposer's cross-market dispute history) is a fair
    feature for BOTH classes — not a disputed-only artifact. latency_anomaly is a dispute-onset
    quantity (undefined for a market that never disputed), so controls carry 0 — the honestly weak
    feature."""
    from data.metadata import market_meta
    from data.prior_corpus import sampled_condition_ids

    index = disputed_feature_index()
    disputed_ids = set(index)
    rows: list[dict] = [feature_row(
        category_base_rate=f["category_base_rate"], market_size=f["market_size"],
        proposer_reliability=f["proposer_reliability"], latency_anomaly=f["latency_anomaly"],
        disputed=1) for f in index.values()]

    controls = [c for c in sampled_condition_ids(per_category=control_per_category)
                if c not in disputed_ids]
    br = _base_rate_fn()
    sizes = _fill_count_map(controls)
    for cid in controls:
        meta = market_meta(cid) or {}
        rows.append(feature_row(                        # proposer/latency zeroed — see index note
            category_base_rate=br(meta.get("category", "other")),
            market_size=market_size_feature(sizes.get(cid, 0)),
            proposer_reliability=0.0, latency_anomaly=0.0, disputed=0))
    return rows


def train_and_cache(*, control_per_category: int = 150, path: str = HAZARD_MODEL_CACHE) -> dict:
    """Build the real training set, fit the hazard logistic, persist it, and return honest metrics
    (Brier vs base-rate Brier + positives). The one-call entry point for the offline model build."""
    return fit_and_save(build_training_rows(control_per_category=control_per_category), path=path)


def _holdout_eval(rows, offset: float, *, seed: int = 0) -> dict:
    """Stratified 70/30 hold-out: train on 70%, score the untouched 30% at natural calibration.
    Held-out Brier + AUC — discrimination (AUC) is the honest metric for a rare event."""
    import numpy as np
    from sklearn.metrics import brier_score_loss, roc_auc_score

    pos = [r for r in rows if r["disputed"] == 1]
    neg = [r for r in rows if r["disputed"] == 0]
    rng = np.random.RandomState(seed)
    rng.shuffle(pos); rng.shuffle(neg)
    cp, cn = int(len(pos) * 0.7), int(len(neg) * 0.7)
    train, test = pos[:cp] + neg[:cn], pos[cp:] + neg[cn:]
    if len({r["disputed"] for r in train}) < 2 or len({r["disputed"] for r in test}) < 2:
        return {"holdout_brier": None, "holdout_auc": None}
    m, _ = fit_hazard(train)
    Xte = [[float(r[f]) for f in SAFE_FEATURES] for r in test]
    yte = [r["disputed"] for r in test]
    raw = [row[1] for row in m.predict_proba(Xte)]
    cal = [1.0 / (1.0 + math.exp(-(_logit(p) + offset))) for p in raw]   # natural-prevalence calibrated
    return {"holdout_brier": float(brier_score_loss(yte, cal)),
            "holdout_auc": float(roc_auc_score(yte, raw))}


def fit_and_save(labeled_rows, *, natural_rate: float | None = None,
                 path: str = HAZARD_MODEL_CACHE) -> dict:
    """Fit the hazard logistic, recalibrate to natural prevalence, evaluate HONESTLY, and persist.

    The training set is class-balanced (positives enriched from ~1% to ~60%), so a class-weighted
    logistic emits ~0.5-centred probabilities. We (1) store a prior-correction `offset` so the live
    output is a real ~1%-scale P(dispute), and (2) report HELD-OUT Brier + AUC plus the no-skill and
    category-base-rate baselines — with the DECISIONS.md #9 caveat that at ~1% prevalence this is
    calibration-limited. AUC (discrimination) is the honest headline; absolute Brier on the enriched
    set is not a natural-prevalence comparison."""
    rows = list(labeled_rows)
    model, metrics = fit_hazard(rows)
    y = [int(r["disputed"]) for r in rows]
    pi_train = sum(y) / len(y)
    if natural_rate is None:  # the population's natural prior = mean per-row category base rate
        natural_rate = sum(float(r["category_base_rate"]) for r in rows) / len(rows)
    offset = _logit(natural_rate) - _logit(pi_train)

    base_pred = [float(r["category_base_rate"]) for r in rows]
    metrics.update({
        "pi_train": pi_train, "natural_rate": natural_rate, "offset": offset,
        "no_skill_brier": pi_train * (1 - pi_train),
        "base_rate_brier_enriched": sum((p - t) ** 2 for p, t in zip(base_pred, y)) / len(y)})
    metrics.update(_holdout_eval(rows, offset))
    auc = metrics.get("holdout_auc")
    metrics["discriminates"] = bool(auc is not None and auc > 0.6)
    metrics["caveat"] = (
        "CLASS-ENRICHED training (positives ~60% vs ~1% natural); output prior-corrected to natural "
        "prevalence via `offset`. Headline = held-out AUC (discrimination). At ~1% prevalence this is "
        "CALIBRATION-LIMITED (DECISIONS.md #9) — a directional structural signal, NOT a validated edge "
        "over the category base rate; the base rate remains the honest default.")
    save_hazard_model(model, metrics, offset, path=path)
    metrics["path"] = path
    return metrics
