"""Canon lint — the guard that keeps prose single-sourced. See DECISIONS.md §F.

Why this exists: the repo accumulated ~52k words of markdown in which the same handful of ideas
were restated 5-15 times each. Restatements drift. By 2026-08 the deployed dashboard led with
"exits before they lock your capital" — a premise the project's own verification pass had proven
false — while the corrected sentence sat beneath it as a footnote. Three different ablation number
sets shipped simultaneously. Worse, the frontend smoke test PINNED the stale tagline, so the false
copy was load-bearing on green CI.

The rule (DECISIONS.md §F): every concept is stated IN FULL in exactly one home. Everywhere else
gets one sentence and a link. This module makes that mechanical:

  * banned pre-correction phrases cannot reappear anywhere outside the archive
  * canon strings cannot be restated verbatim outside their declared home
  * the two UI sites that bypass the API (Hero title, index.html) cannot drift from the constant
  * docs can only shrink

Pure stdlib, no network, no data dependency — matches the offline philosophy in notes/09-testing.md.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import pytest

from webapp.backend import constants as K

ROOT = Path(__file__).resolve().parent.parent

# Walk the filesystem rather than shelling out to git: the Docker image (Dockerfile) copies the tree
# without .git, and this test must run there too. The skip list reproduces .gitignore for the
# directories that would otherwise smuggle in stale copies — notably webapp/frontend/dist/, which
# contains every UI string compiled into a bundle.
SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".data_cache",
    "dist", "generated", ".vercel", "deploy",
}
SKIP_SUFFIXES = (".bak", ".log", ".pdf", ".excalidraw")
TEXT_SUFFIXES = (".md", ".py", ".ts", ".tsx", ".html", ".yaml", ".yml", ".sol")

# The archive: the only files allowed to narrate what was once believed (DECISIONS.md §F). They
# quote the falsified claims deliberately, as corrected errors — that record is the project's
# evidence that it caught its own bad premise, so it is concentrated here rather than deleted.
ARCHIVE = {"DECISIONS.md", "LEDGER.md", "ANALYSIS.md"}
SELF = "tests/test_docs_canon.py"


@lru_cache(maxsize=1)
def _corpus() -> tuple[tuple[str, str], ...]:
    """(relpath, text) for every prose-bearing file, read once per session.

    Uses os.walk with in-place dirnames pruning rather than rglob: rglob would still descend into
    .venv / node_modules (~100k files) before the filter rejected them, which took minutes.
    """
    out: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix not in TEXT_SUFFIXES or fn.endswith(SKIP_SUFFIXES):
                continue
            try:
                out.append((str(p.relative_to(ROOT)), p.read_text(encoding="utf-8")))
            except (UnicodeDecodeError, OSError):  # pragma: no cover
                continue
    return tuple(sorted(out))


def _hits(pattern: re.Pattern[str], allow: set[str]) -> list[str]:
    """Every (file:line) match outside the allowed files."""
    out = []
    for rel, text in _corpus():
        if rel in allow:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                out.append(f"{rel}:{i}: {line.strip()[:120]}")
    return out


# --- 1. banned pre-correction phrases ----------------------------------------------------------
# Each of these asserts the falsified premise: that a dispute locks your position. It does not —
# only redemption freezes; the CLOB stays open (DECISIONS.md §C.1). Allowed ONLY in the archive,
# which quotes them as corrected errors, and in this file, which must name them to ban them.
BANNED = {
    "lock-your-capital": r"lock(s|ed)?\s+your\s+capital|exits?\s+before\s+(they|it)\s+lock",
    "cant-trade-out": r"can'?t\s+trade\s+out|cannot\s+trade\s+out",
    "un-hedgeable": r"un-?hedgeable",
    "positions-lock": r"positions\s+lock\s+for",
    "blind-flatten": r"flatten\s+inventory\s*(→|->)\s*0|do\s+not\s+re-?quote\s+until\s+resolved",
}


@pytest.mark.parametrize("name,pattern", sorted(BANNED.items()))
def test_no_banned_stale_phrases(name: str, pattern: str) -> None:
    hits = _hits(re.compile(pattern, re.I), allow=ARCHIVE | {SELF})
    assert not hits, (
        f"Pre-correction phrase '{name}' reappeared. A dispute does NOT lock your position — only "
        f"redemption freezes and exit liquidity degrades (~5c haircut). See DECISIONS.md §C.1.\n"
        + "\n".join(hits)
    )


# --- 2. canon strings are single-sourced -------------------------------------------------------
# {constants attribute: files allowed to contain it verbatim}. constants.py is always implicitly
# allowed (it is the home). A 6th file restating one of these is a hard failure — write one
# sentence and a link instead (DECISIONS.md §F, "Echo" tier).
CANON_HOMES: dict[str, set[str]] = {
    "THESIS": {"Readme.md", "REPORT.md"},
    "THESIS_TITLE": {"Readme.md", "REPORT.md", "webapp/frontend/index.html",
                     "webapp/frontend/src/lib/canon.ts"},
    "EXIT_GATE": set(),
    "EDGE_ONE_LINER": {"REPORT.md"},  # C7's canon home is REPORT.md §4 (the numbers live there)
    "POWER_CAVEAT": set(),
    "RECON_CAVEAT": set(),
    "DATASET_ONE_LINER": set(),
    "HAZARD_CAVEAT": set(),
}
CONSTANTS_FILE = "webapp/backend/constants.py"


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


@pytest.mark.parametrize("attr", sorted(CANON_HOMES))
def test_canon_strings_single_sourced(attr: str) -> None:
    value = _normalize(getattr(K, attr))
    allowed = CANON_HOMES[attr] | {CONSTANTS_FILE, SELF}
    offenders = [rel for rel, text in _corpus()
                 if rel not in allowed and value in _normalize(text)]
    assert not offenders, (
        f"constants.{attr} is restated verbatim outside its home {sorted(allowed)}: {offenders}. "
        f"Canon is stated once; everywhere else gets one sentence + a link (DECISIONS.md §F)."
    )


# --- 3. the thesis of record -------------------------------------------------------------------
EXPECTED_THESIS = (
    "A dispute doesn't freeze the order book — only redemption. So it's a directional price jump "
    "you can model and defend against, not a lock. The engine folds that jump intensity (λ) "
    "straight into the pricing math, and only pulls liquidity when E[jump loss] > forgone rewards."
)
EXPECTED_TITLE = "Disputes are jumps, not locks — priced into the spread."


def test_thesis_is_of_record() -> None:
    """Pinned verbatim so a silent edit to the product's central claim fails CI."""
    assert K.THESIS == EXPECTED_THESIS
    assert K.THESIS_TITLE == EXPECTED_TITLE
    # THESIS_NUANCE was the corrected text living *subordinate* to a false THESIS. Keeping both
    # fields is what produced the contradiction; the name must not come back.
    assert not hasattr(K, "THESIS_NUANCE"), "THESIS_NUANCE is retired — THESIS now holds the truth."


def test_overview_serves_the_canon() -> None:
    from webapp.backend import services

    payload = services.overview()
    assert payload["thesis"] == EXPECTED_THESIS
    assert payload["thesis_title"] == EXPECTED_TITLE
    assert "thesis_nuance" not in payload


# --- 4. the two UI sites that bypass the API ---------------------------------------------------
def test_ui_title_matches_canon() -> None:
    """Hero <h2> and the document <meta> render before any fetch resolves, so they mirror the
    constant rather than reading it. This is the latch that stops them drifting apart again."""
    canon_ts = (ROOT / "webapp/frontend/src/lib/canon.ts").read_text(encoding="utf-8")
    assert K.THESIS_TITLE in canon_ts, "src/lib/canon.ts is out of sync with constants.THESIS_TITLE"

    index_html = (ROOT / "webapp/frontend/index.html").read_text(encoding="utf-8")
    assert K.THESIS_TITLE in index_html, "index.html <meta description> is out of sync"

    hero = (ROOT / "webapp/frontend/src/sections/Hero.tsx").read_text(encoding="utf-8")
    assert "THESIS_TITLE" in hero, "Hero must import the canon title, not hard-code a string"
    assert "lock your capital" not in hero


# --- 5. no correction archaeology outside the archive ------------------------------------------
def test_no_correction_archaeology() -> None:
    """The corrections record is evidence of intellectual honesty and is kept — but CONCENTRATED in
    DECISIONS.md §C plus the ANALYSIS.md narrative, not smeared across every doc and docstring as
    a wrong-version-then-right-version pair."""
    hits = _hits(re.compile(r"⚠\s*CORRECTION|CORRECTION NOTICE|Corrected design"),
                 allow=ARCHIVE | {SELF})
    assert not hits, (
        "Correction archaeology belongs in DECISIONS.md §C / ANALYSIS.md. State the corrected "
        "fact as plain body text instead.\n" + "\n".join(hits)
    )


# --- 6. the word ratchet -----------------------------------------------------------------------
# Per-file maximum word counts. Docs may only shrink. A markdown file absent from this dict fails:
# either give it a budget or fold it into an existing doc. This is the direct enforcement of
# "one-liner contents, not long contents all over explaining the same thing".
BUDGETS: dict[str, int] = {
    "ANALYSIS.md": 833,
    "BUSINESS_PLAN.md": 1428,
    "DATASET.md": 3341,
    "DECISIONS.md": 1569,  # grew: absorbed the canon table other files shed
    "JURISDICTION.md": 570,
    "LEDGER.md": 5484,      # archive tier — the build log is allowed to be long
    "METHODOLOGY.md": 1829,
    "REPORT.md": 6332,
    "ROADMAP.md": 2432,
    "Readme.md": 1216,
    "WEATHER_COPYTRADE.md": 2011,
    "dataset_release/polymarket-oov2-disputes-v1/README.md": 1014,
    "indexer/DEPLOY.md": 555,
    "indexer/README.md": 691,
    "notes/01-architecture.md": 861,
    "notes/02-module-reference.md": 1757,
    "notes/03-data-backbone.md": 755,
    "notes/04-model-pricing.md": 862,
    "notes/05-forwardtest-ablation.md": 727,
    "notes/06-onchain-webapp.md": 1248,
    "notes/07-config-reference.md": 767,
    "notes/08-entrypoints-runbook.md": 542,
    "notes/09-testing.md": 650,
    "notes/10-glossary.md": 722,   # canon home for the glossary
    "notes/11-testnet-proof.md": 804,
    "notes/12-liveness-refresh.md": 3311,  # archive tier — dated fix post-mortems
    "notes/13-testnet-execution.md": 926,
    "notes/14-go-live-runbook.md": 795,
    "notes/README.md": 457,
    "notes/day01-lifecycle.md": 211,
    "polycool_info.md": 3659,
    "webapp/DEPLOY.md": 624,
    "webapp/README.md": 466,
}
TOTAL_WORD_BUDGET = 49449


def _markdown_files() -> dict[str, int]:
    return {rel: len(text.split()) for rel, text in _corpus() if rel.endswith(".md")}


def test_doc_word_budget() -> None:
    counts = _markdown_files()

    unbudgeted = sorted(set(counts) - set(BUDGETS))
    assert not unbudgeted, (
        f"New markdown file(s) with no word budget: {unbudgeted}. Add an entry to BUDGETS, or "
        f"fold the content into an existing doc rather than starting a new one."
    )

    over = {f: (n, BUDGETS[f]) for f, n in counts.items() if n > BUDGETS[f]}
    assert not over, (
        "Doc(s) grew past budget (actual, budget): "
        + str(over)
        + ". Docs may only shrink. If the content is genuinely new canon, lower another budget or "
          "state it once and link (DECISIONS.md §F)."
    )


def test_total_word_budget() -> None:
    """Stops the per-file ratchet being gamed by spreading prose across more files."""
    total = sum(_markdown_files().values())
    assert total <= TOTAL_WORD_BUDGET, (
        f"Total tracked markdown is {total:,} words, over the {TOTAL_WORD_BUDGET:,} budget."
    )
