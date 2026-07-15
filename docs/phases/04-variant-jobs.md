# Phase 4 — Variant groups, jobs, and cache

## Goal

Persist per-base-code modes, representative SKU choices, deterministic work plans, resumable jobs, isolated failures, and cache behavior without live LLM extraction.

## Checklist

- [ ] Add minimal versioned SQLite schema for jobs, items, assets, groups, status/errors, and artifacts.
- [ ] Group rows with SKU fallback for blank base codes; default every group to `PER_SKU`.
- [ ] Support editable per-group/bulk `BASE_CODE_SIZE_ONLY`, warnings, and deterministic representative selection.
- [ ] Show planned request count before execution.
- [ ] Persist valid state transitions, partial failures, selections, and restart/resume controls.
- [ ] Implement the complete cache key using fake extraction results.
- [ ] Test mixed groups, fallbacks, overrides, representative order, cache invalidation, and failures.

## Acceptance

- [ ] Mixed modes and planned counts are correct.
- [ ] Representative selection is deterministic and user-overridable.
- [ ] Restart preserves state; configuration/image/mode changes invalidate cache.
- [ ] One failure does not erase successful state; tests and Ruff pass.

## Verification

```bash
python -m pytest tests/test_variant_service.py tests/test_database.py tests/test_jobs.py
ruff check .
```

