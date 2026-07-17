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
    from datetime import datetime, timezone
    payload = {"coef": [float(c) for c in model.coef_[0]], "intercept": float(model.intercept_[0]),
               "offset": float(offset), "feature_order": list(SAFE_FEATURES),
               "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "metrics": metrics}
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


def _hf_window_sql() -> str:
    """The HF-window predicate shared by every training read of the released layer.

    Same invariant as data.disputes.load_disputes: `hf_joinable` is SPATIAL (the market exists in HF),
    never temporal, so it does NOT keep these reads inside the HF snapshot window once the released
    layer is extended past the cutoff. Post-cutoff disputes would arrive with an HF-derived
    `market_size` of ~0 (the HF fill tape ends at the same cutoff, so those markets look fill-less) —
    i.e. phantom zero-liquidity positives — on top of an inflated category_base_rate. Bound both.
    """
    from data.disputes import HF_CUTOFF_TS
    return f"hf_joinable AND disputeTs <= {HF_CUTOFF_TS}"


def _disputes_by_proposer() -> dict[str, int]:
    """{proposer_lower: their total dispute count} across the released dispute layer (HF window)."""
    import duckdb

    rows = duckdb.sql(f"SELECT lower(proposer), count(*) FROM '{_RELEASE_PQ}' "
                      f"WHERE {_hf_window_sql()} AND proposer IS NOT NULL GROUP BY 1").fetchall()
    return {p: int(n) for p, n in rows if p}


def disputed_feature_index() -> dict[str, dict]:
    """{conditionId: {category, price, <SAFE_FEATURES>}} for every joinable disputed market, from the
    released dispute layer. Shared by training (adds disputed=1) AND the live builder (so a market's
    hazard input matches exactly what it was trained on). Point-in-time-safe."""
    import duckdb

    pq = _RELEASE_PQ
    disp = duckdb.sql(
        f"SELECT conditionId, category, preDisputePrice FROM '{pq}' "
        f"WHERE {_hf_window_sql()}").fetchall()

    br = _base_rate_fn()
    sizes = _fill_count_map([d[0] for d in disp])
    index: dict[str, dict] = {}
    for cid, cat, price in disp:
        cat = cat or "other"
        index[cid] = {
            # `price` is NOT a model feature (see SAFE_FEATURES) — it is only carried for the LIVE
            # builder, where estimate_lambda uses it for the jump_drift DIRECTION. 174 joinable rows
            # have no preDisputePrice (no pre-dispute fills in the tape); 0.5 is the deliberate neutral
            # default → logit 0 → jump_drift exactly 0, i.e. "no directional claim", not a fabricated
            # observation. Training never reads this field.
            "category": cat, "price": float(price) if price is not None else 0.5,
            "category_base_rate": br(cat), "market_size": market_size_feature(sizes.get(cid, 0)),
            # DEPLOYED model is size-only (category_base_rate + market_size). proposer_reliability is
            # a proven NULL: evaluated leakage-free AND market_size-matched (build_matched_training_rows
            # / v2), it does not beat the base rate (matched AUC 0.64 ≤ v1 0.68) — the earlier
            # "discrimination" was liquidity, not proposer reputation. latency_anomaly needs a
            # proposedAt indexer field (none exists — v3). Both stay 0 in the deployed model.
            "proposer_reliability": 0.0, "latency_anomaly": 0.0}
    return index


def market_feature_dicts(condition_ids: list[str]) -> dict[str, dict]:
    """SAFE_FEATURES (+ category/price) per market, for feeding estimate_lambda(model=...) in the
    live builder. Only disputed-layer markets are known; others are omitted (caller falls back)."""
    idx = disputed_feature_index()
    return {c: idx[c] for c in condition_ids if c in idx}


def load_controls_from_indexer(n: int, *, graphql_url: str | None = None) -> list[dict]:
    """PROPOSED-BUT-NOT-DISPUTED control markets from the indexer — the correct control population for
    a dispute-onset model. A round-0 ResolutionRequest with status RESOLVED + a non-null proposer was
    proposed and settled WITHOUT ever being disputed (a dispute bumps the round). These carry a REAL
    proposer, so proposer_reliability is a fair feature for both classes (unlike v1's arbitrary HF
    controls, which had none → the AUC-0.95 leakage). Returns up to n HF-joinable [{cid, proposer,
    oracle}] (NegRisk mapped to its tradeable cid). Empty on any failure (caller falls back)."""
    from data.disputes import ADAPTER_OF, _gql, resolve_indexer
    from data.hf import query, table_path

    url, secret = resolve_indexer(graphql_url)   # local (admin secret) → hosted (no secret) fallback
    if url is None:
        return []
    raw, offset, scan_cap = [], 0, max(3 * n, 2000)
    while len(raw) < scan_cap:
        q = ('query { ResolutionRequest(limit: 1000, offset: %d, order_by:{requestTimestamp: desc}, '
             'where:{round:{_eq:0}, proposer:{_is_null:false}, status:{_eq:"RESOLVED"}, '
             'market:{status:{_eq:"RESOLVED"}}}) { proposer market{ id questionId oracle } } }' % offset)
        batch = None
        for _ in range(3):                        # per-page retry: a transient blip must not drop the run
            try:
                batch = _gql(q, url=url, secret=secret, timeout=30).get("ResolutionRequest", [])
                break
            except Exception:
                batch = None
        if batch is None:                         # give up after retries — keep what we have, not []
            break
        raw.extend(batch)
        offset += len(batch)
        if len(batch) < 1000:
            break
    if not raw:
        return []

    # effective (HF-joinable) conditionId: native for V2/Legacy, tradeable-via-map for NegRisk
    nmap = {}
    try:
        from data.negrisk_map import load_negrisk_map
        nmap = load_negrisk_map()
    except Exception:
        pass
    cand: dict[str, dict] = {}
    for r in raw:
        m = r.get("market") or {}
        adapter = ADAPTER_OF.get((m.get("oracle") or "").lower(), "unknown")
        if adapter == "negrisk":
            hit = nmap.get(m.get("questionId") or "")
            eff = hit["tradeableConditionId"] if hit else None
        else:
            eff = m.get("id")
        if eff and r.get("proposer") and eff not in cand:
            cand[eff] = {"cid": eff, "proposer": r["proposer"].lower(), "oracle": m.get("oracle")}

    # keep only HF-joinable (has a row in the condition table), preserving request-time order
    cids = list(cand)
    joined: set[str] = set()
    cpath = table_path("condition")
    for i in range(0, len(cids), 5000):
        inl = ",".join(f"'{c}'" for c in cids[i:i + 5000])
        joined |= {x[0] for x in query(f"SELECT id FROM '{cpath}' WHERE id IN ({inl})")}
    return [cand[c] for c in cids if c in joined][:n]


def build_training_rows(*, control_per_category: int = 200) -> list[dict]:
    """Assemble labeled SAFE_FEATURES rows for the DEPLOYED (size-only) hazard: disputed markets
    (label 1) + sampled resolved controls (label 0), both IN-SLICE so market_size is counted the same
    way. proposer_reliability + latency_anomaly are 0 (the deployed model is category_base_rate +
    market_size; proposer is a proven null — see build_matched_training_rows). This is the honest v1
    the powered replay used."""
    from data.metadata import market_meta
    from data.prior_corpus import sampled_condition_ids

    index = disputed_feature_index()
    disputed_ids = set(index)
    rows: list[dict] = [feature_row(
        category_base_rate=f["category_base_rate"], market_size=f["market_size"],
        proposer_reliability=0.0, latency_anomaly=0.0, disputed=1) for f in index.values()]

    controls = [c for c in sampled_condition_ids(per_category=control_per_category)
                if c not in disputed_ids]
    br = _base_rate_fn()
    sizes = _fill_count_map(controls)
    for cid in controls:
        cat = (market_meta(cid) or {}).get("category", "other")
        rows.append(feature_row(
            category_base_rate=br(cat), market_size=market_size_feature(sizes.get(cid, 0)),
            proposer_reliability=0.0, latency_anomaly=0.0, disputed=0))
    return rows


def _cem_match(disp: list[dict], ctrl: list[dict], *, n_bins: int = 5, by_category: bool = False,
               seed: int = 7) -> list[dict]:
    """Coarsened exact matching on market_size deciles (optionally × category): within each cell keep
    an equal number of disputed and control rows, so the classes share the SAME market_size (and
    category) distribution — those can then no longer separate them, and the fit's residual signal is
    attributable to proposer_reliability. `by_category=False` matches on size only (keeps more pairs
    when controls are scarce; category stays a covariate). Returns the balanced row list."""
    import random
    from collections import defaultdict

    # deciles from the CONTROL market_size range (the scarce side), so cells actually contain controls
    csz = sorted(r["market_size"] for r in ctrl)
    edges = [csz[int(i * len(csz) / n_bins)] for i in range(1, n_bins)] if csz else []

    def size_bin(x: float) -> int:
        return sum(1 for e in edges if x > e)

    def key(r):
        return (r["category"], size_bin(r["market_size"])) if by_category else size_bin(r["market_size"])

    cd, cc = defaultdict(list), defaultdict(list)
    for r in disp:
        cd[key(r)].append(r)
    for r in ctrl:
        cc[key(r)].append(r)
    rng = random.Random(seed)
    out: list[dict] = []
    for cell in set(cd) | set(cc):
        d, c = cd.get(cell, []), cc.get(cell, [])
        k = min(len(d), len(c))
        if k == 0:
            continue
        rng.shuffle(d); rng.shuffle(c)
        out += d[:k] + c[:k]
    return out


def build_matched_training_rows(*, control_pool: int = 12000, graphql_url: str | None = None,
                                n_bins: int = 5, by_category: bool = False) -> list[dict]:
    """v2 MATCHED fair-controls set: disputed markets + proposed-but-not-disputed indexer controls
    that ARE liquid in the fill slice (market_size > 0, so counted the SAME way as disputed), then
    CEM-balanced on market_size deciles (optionally × category) so the fit ISOLATES
    proposer_reliability — the category+size confound can no longer separate the classes. Controls
    with no in-slice fills are dropped (their market_size=0 is a materialization artifact, not
    liquidity). Small-N by design (in-slice liquid controls are scarce) — read via the power calc.
    latency_anomaly stays 0 (no proposal timestamp — v3)."""
    import duckdb
    from data.metadata import market_meta

    dbp = _disputes_by_proposer()
    br = _base_rate_fn()
    # disputed rows with REAL proposer (self-contained: independent of the deployed size-only index,
    # which zeros proposer). LOO removes each market's own dispute.
    disp = duckdb.sql(f"SELECT conditionId, category, lower(proposer) FROM '{_RELEASE_PQ}' "
                      f"WHERE {_hf_window_sql()}").fetchall()
    disputed_ids = {d[0] for d in disp}
    dsizes = _fill_count_map([d[0] for d in disp])
    disp_rows = []
    for cid, cat, prop in disp:
        cat = cat or "other"
        disp_rows.append({"category": cat, "category_base_rate": br(cat),
                          "market_size": market_size_feature(dsizes.get(cid, 0)),
                          "proposer_reliability": proposer_reliability_feature(prop, dbp, exclude=True),
                          "latency_anomaly": 0.0, "disputed": 1})

    ctrl = [c for c in load_controls_from_indexer(control_pool, graphql_url=graphql_url)
            if c["cid"] not in disputed_ids]
    sizes = _fill_count_map([c["cid"] for c in ctrl])
    ctrl_rows = []
    for c in ctrl:
        fills = sizes.get(c["cid"], 0)
        if fills <= 0:                                  # not-in-slice → unfair 0 market_size; drop
            continue
        cat = (market_meta(c["cid"]) or {}).get("category", "other")
        ctrl_rows.append({"category": cat, "category_base_rate": br(cat),
                          "market_size": market_size_feature(fills),
                          "proposer_reliability": proposer_reliability_feature(c["proposer"], dbp, exclude=False),
                          "latency_anomaly": 0.0, "disputed": 0})

    matched = _cem_match(disp_rows, ctrl_rows, n_bins=n_bins, by_category=by_category)
    return [feature_row(category_base_rate=r["category_base_rate"], market_size=r["market_size"],
                        proposer_reliability=r["proposer_reliability"],
                        latency_anomaly=r["latency_anomaly"], disputed=r["disputed"]) for r in matched]


def train_and_cache(*, matched: bool = False, graphql_url: str | None = None,
                    path: str = HAZARD_MODEL_CACHE) -> dict:
    """Fit the hazard logistic, persist it, and return honest held-out metrics. Default = the DEPLOYED
    size-only model (category_base_rate + market_size). `matched=True` runs the v2 fair-controls
    EVALUATION (proposer_reliability isolated by market_size-matching) — its result is the null
    verdict, not a model to deploy."""
    rows = (build_matched_training_rows(graphql_url=graphql_url) if matched
            else build_training_rows())
    return fit_and_save(rows, path=path)


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


def main(argv: list[str] | None = None) -> None:
    """The reproducible training entry point (was manual/REPL-only before): fit + cache the model
    so the deployed .data_cache/hazard_model.json is regenerable with one command. Extracted from
    __main__ so tests can drive the argument parsing/printout with train_and_cache stubbed."""
    import argparse

    ap = argparse.ArgumentParser(
        description="Fit + cache the dispute-hazard model so the deployed .data_cache/hazard_model.json "
                    "is reproducible. Default = the DEPLOYED size-only model; --matched runs the v2 "
                    "fair-controls proposer-null EVALUATION (needs the indexer; NOT a deployable model).")
    ap.add_argument("--matched", action="store_true",
                    help="v2 fair-controls study (CEM-matched on market_size to isolate proposer_reliability)")
    ap.add_argument("--graphql-url", default=None,
                    help="indexer endpoint for --matched controls (default: auto-resolve local→hosted)")
    ap.add_argument("--path", default=None,
                    help="output JSON path (default: the deployed cache; --matched defaults to a "
                         "separate *_matched_eval.json so the EVALUATION never clobbers the deployed model)")
    args = ap.parse_args(argv)

    path = args.path or (HAZARD_MODEL_CACHE.replace(".json", "_matched_eval.json") if args.matched
                         else HAZARD_MODEL_CACHE)
    m = train_and_cache(matched=args.matched, graphql_url=args.graphql_url, path=path)
    print(f"wrote {m.get('path')}")
    print(f"  n={m.get('n')} positives={m.get('positives')} "
          f"held-out AUC={m.get('holdout_auc')} discriminates={m.get('discriminates')}")
    off = m.get("offset")
    print(f"  offset={off:.4f} natural_rate={m.get('natural_rate')}" if off is not None
          else f"  natural_rate={m.get('natural_rate')}")
    print(f"  caveat: {m.get('caveat')}")


if __name__ == "__main__":
    main()
