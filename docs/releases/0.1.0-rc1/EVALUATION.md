# Evaluation — 0.1.0-rc1

The committed 13-case dataset is an engineering fixture, not human-approved golden truth. Two deterministic fake model IDs exercise identical frozen cases, aggregation, leakage detection, usage, and policy routing. Their values and 0.01-second synthetic timing prove the framework only; they are not model-accuracy, latency, or price claims.

Metrics always include numerator, denominator, and sample count. A zero denominator produces `null`, never a misleading percentage.

- Precision = correct canonical nonblank predictions / all nonblank predictions.
- Coverage = populated fields / fields with an expected canonical value.
- Blank, conflict, invalid-enum, review-required, and unsupported-claim rates use the relevant annotated-field denominator.
- Variant leakage = differing variant fields incorrectly given the same nonblank value / evaluable differing variant pairs.
- Extraction failure = failed model-case predictions / model-case predictions.
- Latency per request or SKU and requests per SKU/base code divide totals by their named sample unit.
- Token totals are reported only when returned. Cost is `null` unless an approved matching price record exists.

`config/evaluation_thresholds.json` is versioned and pending. Until approval, every evaluated field routes to `REVIEW_REQUIRED`; approved field rules may instead route failures to `EXPLICIT_INPUT_ONLY` or `DISABLED`.
