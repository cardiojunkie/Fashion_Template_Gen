# Phase 6 — Review, catalog copy, and Topwear export

## Goal

Normalize and review Topwear observations, generate factual text from accepted facts, and export the exact validated Topwear workbook.

## Checklist

- [x] Implement contractual merge order and exact/normalized/alias/review-only fuzzy matching.
- [x] Never auto-add canonical values; keep unmapped proposals blank and reviewable.
- [x] Show proposed/input/evidence/conflicts and allow persisted accept/edit/blank/reject decisions.
- [x] Filter conflicts, unmapped/invalid enums, insufficient evidence, and low confidence.
- [x] Approve golden-backed Topwear name/title/bullet/keyword templates.
- [x] Generate text-only copy from accepted facts and validate claims, repetition, limits, and placeholders.
- [x] Flatten one exact row per SKU, exclude internals, export `.xlsx`, and issue a separate validation summary.
- [x] Add fake-client upload-to-export tests.

## Acceptance

- [x] Configured `A-Line Fit` maps to `A-Line`; input wins absent explicit review override.
- [x] Review survives restart and unmapped values remain blank.
- [x] Copy uses accepted facts only.
- [x] Reopened output preserves exact headers/rows/text identifiers, valid enums, and no debug fields.
- [x] Tests and Ruff pass.

## Implemented workflow

Every supported observation enters the review table with its raw/input value, canonical proposal, normalization method, provenance, source priority, confidence, conflict, warning, and persisted decision. Enum edits use active permitted values only. Unresolved proposals block copy and export; registry changes revalidate stored enum decisions without silently rewriting them.

Accepted facts feed a separate text-only request. Code owns the deterministic Topwear title, validates keywords and up to six factual bullets, and leaves unsupported bullet cells blank. Export produces one exact 45-column row per input SKU and a separate QC report. Only an unchanged, accepted broad image-derived color receives a yellow CMS cell and inference note.

The active registry has no approved copy length limits, series-name field, occasion rules/values, or semantic definitions for overlapping pairs. These remain blank or reviewable pending registry/business approval.

## Verification

```bash
python -m pytest tests/test_normalization.py tests/test_review.py tests/test_catalog_service.py tests/test_topwear_e2e.py
ruff check .
```

Result: `34 passed`; complete suite `253 passed, 1 skipped`; Ruff passed.
