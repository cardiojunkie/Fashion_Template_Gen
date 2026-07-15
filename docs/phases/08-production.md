# Phase 8 — Evaluation, security, production, and release

## Goal

Prove the complete workflow accurate, secure, resilient, supportable, and deployable before release.

## Checklist

- [ ] Expand frozen golden data across every set/profile; measure per-field precision, supported coverage, blanks, conflicts, leakage, latency, and cost across at least two models.
- [ ] Agree thresholds with the user and route failures to review or stricter evidence policy.
- [ ] Threat-model and regression-test every workbook/ZIP/name/formula/image/URL/prompt/secret/resource boundary.
- [ ] Verify retention cleanup and secret-safe logs; configure upload, row, image, request, and cost limits.
- [ ] Add bounded concurrency, retry/resume, cancellation semantics, backups/migrations, cache invalidation, and artifact cleanup.
- [ ] Add workers or batch mode only when measured Streamlit limitations justify them.
- [ ] Document the chosen deployment, secrets/storage/health/backups/upgrades, user workflows, release checklist, and rollback.
- [ ] Run correctness review before minimalism review; freeze registry/prompt/schema/golden versions.

## Acceptance

- [ ] Clean-environment tests/Ruff and every golden end-to-end workflow pass.
- [ ] Outputs validate and neither analysis mode has known silent golden-set leakage.
- [ ] Downloader security/format cases pass; partial jobs resume and export successes.
- [ ] Request count/cost are visible; deployment/user/release docs are complete.
- [ ] User approves values, copy rules, review thresholds, and output format.

## Verification

```bash
python -m pytest
ruff check .
```

Then run the documented end-to-end release checklist.

