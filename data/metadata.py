"""
metadata — market metadata + the tokenId <-> conditionId map, and a derived category.

HF `market_data` is keyed by outcome-token id (`id`) and carries `condition` (the conditionId),
`outcomeIndex` (0/1 → which leg is YES vs NO — this resolves the "run sigma on ONE canonical leg"
requirement), plus `marketName` / `marketSlug` / `description` / `startDate` / `endDate`.

Caveat (verified): there is NO `category` column. PolyLambda's sigma prior and lambda base-rate are
per-category, so we DERIVE a coarse category from the slug/name via keyword heuristics. This is
best-effort and labeled as such; a later pass can swap in Polymarket's Gamma API tags by slug.
"""
from __future__ import annotations

from .hf import query, table_path

# Coarse category keyword map (first match wins, order matters). Intentionally simple + auditable.
CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("crypto",     ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "doge", "xrp", "nft")),
    ("politics",   ("trump", "biden", "harris", "election", "president", "senate", "congress",
                    "governor", "democrat", "republican", "putin", "parliament", "prime-minister")),
    ("sports",     ("nba", "nfl", "mlb", "nhl", "soccer", "premier-league", "champions-league",
                    "world-cup", "super-bowl", "ufc", "f1", "tennis", "golf", "vs-")),
    ("economics",  ("fed", "cpi", "inflation", "gdp", "rate-hike", "recession", "jobs", "unemployment")),
    ("geopolitics",("war", "ukraine", "russia", "israel", "gaza", "china", "taiwan", "nato", "ceasefire")),
    ("tech-ai",    ("openai", "gpt", "ai-", "-ai", "tesla", "spacex", "apple", "google", "twitter", "musk")),
    ("entertainment", ("oscar", "grammy", "movie", "box-office", "album", "spotify", "netflix")),
]


def derive_category(slug: str | None, name: str | None = None) -> str:
    """Best-effort coarse category from slug/name keywords; 'other' when nothing matches."""
    hay = f"{(slug or '').lower()} {(name or '').lower()}"
    for cat, kws in CATEGORY_KEYWORDS:
        if any(kw in hay for kw in kws):
            return cat
    return "other"


# SQL fragment: a derived category as a CASE over lower(marketSlug || ' ' || marketName).
# Kept in sync with CATEGORY_KEYWORDS so aggregate queries can group by category server-side.
def category_case_sql(alias_slug: str = "marketSlug", alias_name: str = "marketName") -> str:
    hay = f"lower(coalesce({alias_slug},'') || ' ' || coalesce({alias_name},''))"
    branches = []
    for cat, kws in CATEGORY_KEYWORDS:
        conds = " OR ".join(f"{hay} LIKE '%{kw}%'" for kw in kws)
        branches.append(f"WHEN {conds} THEN '{cat}'")
    return "CASE " + " ".join(branches) + " ELSE 'other' END"


def tokens_for_condition(condition_id: str) -> list[str]:
    """The market's outcome token ids (2 for binary).

    NB: market_data.outcomeIndex is NULL in this dataset, so we cannot label YES vs NO here. The
    fill tape uses a deterministic canonical leg (data.fills), which is sufficient for sigma. YES
    semantics, when needed (the replay), are resolved from condition.positionIds at that layer.
    """
    sql = f"SELECT DISTINCT id FROM '{table_path('market_data')}' WHERE condition = ?"
    return [r[0] for r in query(sql, [condition_id])]


def canonical_token(condition_id: str) -> str | None:
    """The deterministic canonical leg (min tokenId) — the single axis sigma runs on."""
    toks = tokens_for_condition(condition_id)
    return min(toks) if toks else None


def market_meta(condition_id: str) -> dict | None:
    """Human-facing metadata for a market + its derived category."""
    sql = f"""
        SELECT condition, any_value(marketName), any_value(marketSlug),
               any_value(startDate), any_value(endDate)
        FROM '{table_path("market_data")}'
        WHERE condition = ?
        GROUP BY condition
    """
    rows = query(sql, [condition_id])
    if not rows:
        return None
    cid, name, slug, start, end = rows[0]
    return {"condition_id": cid, "name": name, "slug": slug,
            "start_date": start, "end_date": end, "category": derive_category(slug, name)}
