# Phase 5 — Topwear vision extraction

## Goal

Implement evidence-aware Topwear extraction only, with the fixed NVIDIA Inkling runtime, strict structured output, applicable canonical values, both analysis modes, caching, and no default live calls in tests.

## Checklist

- [x] Add the fixed NVIDIA Inkling client, offline fake client, and secret-safe environment validation.
- [ ] Version prompt/schema and send only Topwear profile headers and canonical values.
- [ ] Explicitly label SKU/ordinal before each image and delimit product data as untrusted.
- [ ] Build exact `PER_SKU` and representative `BASE_CODE_SIZE_ONLY` requests.
- [ ] Parse strict `VisionResult`; reject unknown headers/SKUs, statuses, and enums.
- [ ] Store sanitized raw/parsed results, versions, request ID, usage, and errors.
- [ ] Retry only retryable failures; preserve cancellation-safe progress.
- [ ] Add ten manually checked Topwear golden fixtures; keep live tests opt-in.

Visual focus is product type, broad color, pattern/type, design, neckline, cuff, sleeve, closure/fastening, and finish. Treat material/composition, care, exact fit, comfort, origin, weight, and dimensions conservatively.

## Acceptance

- [ ] Request count matches mode and image/SKU labels survive parsing.
- [ ] Unsupported facts remain unknown/blank; invalid output cannot become accepted data.
- [ ] Cache prevents unchanged calls; golden fixtures record expected review outcomes.
- [ ] Tests and Ruff pass without an API key.

## Verification

```bash
python -m pytest tests/test_llm_service.py tests/test_topwear_extraction.py tests/test_cache.py
ruff check .
```

Live verification is optional and explicit: `RUN_LIVE_NVIDIA_TESTS=1 python -m pytest -m live`.
