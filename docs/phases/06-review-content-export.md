# Phase 6 — Review, catalog copy, and Topwear export

## Goal

Normalize and review Topwear observations, generate factual text from accepted facts, and export the exact validated Topwear workbook.

## Checklist

- [ ] Implement contractual merge order and exact/normalized/alias/review-only fuzzy matching.
- [ ] Never auto-add canonical values; keep unmapped proposals blank and reviewable.
- [ ] Show proposed/input/evidence/conflicts and allow persisted accept/edit/blank/reject decisions.
- [ ] Filter conflicts, unmapped/invalid enums, insufficient evidence, and low confidence.
- [ ] Approve golden-backed Topwear name/title/bullet/keyword templates.
- [ ] Generate text-only copy from accepted facts and validate claims, repetition, limits, and placeholders.
- [ ] Flatten one exact row per SKU, exclude internals, export `.xlsx`, and issue a separate validation summary.
- [ ] Add fake-client upload-to-export tests.

## Acceptance

- [ ] Configured `A-Line Fit` maps to `A-Line`; input wins absent explicit review override.
- [ ] Review survives restart and unmapped values remain blank.
- [ ] Copy uses accepted facts only.
- [ ] Reopened output preserves exact headers/rows/text identifiers, valid enums, and no debug fields.
- [ ] Tests and Ruff pass.

## Verification

```bash
python -m pytest tests/test_normalization.py tests/test_review.py tests/test_catalog_service.py tests/test_topwear_e2e.py
ruff check .
```

