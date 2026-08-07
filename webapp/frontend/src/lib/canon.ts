// CANON (frontend mirror). The prose of record lives in webapp/backend/constants.py; most of it
// reaches the UI over /api/overview. These two strings are the exception: they render as
// above-the-fold chrome (the Hero <h2>, the document <title>/meta) before any fetch resolves, so
// data-driving them would blank the header on cold load. They are mirrored here instead, and
// tests/test_docs_canon.py fails CI if this file, index.html, and constants.THESIS_TITLE ever
// disagree. Do not edit one without the others.
export const THESIS_TITLE = 'Disputes are jumps, not locks, priced into the spread.'
