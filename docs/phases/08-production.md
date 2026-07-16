# Phase 8 — Evaluation, security, production, and release

## Goal

Prove the complete workflow accurate, secure, resilient, supportable, and deployable before release.

## Checklist

- [ ] Expand frozen golden data across every set/profile; measure per-field precision, supported coverage, blanks, conflicts, leakage, latency, and cost across at least two models. Engineering fixture and deterministic two-model runner are complete; human approval and live comparison remain blocked.
- [ ] Agree thresholds with the user and route failures to review or stricter evidence policy.
- [x] Threat-model and regression-test every workbook/ZIP/name/formula/image/URL/prompt/secret/resource boundary.
- [x] Verify retention cleanup and secret-safe logs; configure upload, row, image, request, and cost limits.
- [x] Add bounded concurrency, retry/resume, cancellation semantics, backups/migrations, cache invalidation, and artifact cleanup.
- [x] Add workers or batch mode only when measured Streamlit limitations justify them; no measured need exists, so neither was added.
- [x] Document the pending deployment decisions, secrets/storage/health/backups/upgrades, user workflows, release checklist, and rollback.
- [x] Run correctness review before minimalism review; freeze registry/prompt/schema/golden versions.

## Acceptance

- [x] Clean-environment tests/Ruff and every engineering-fixture end-to-end workflow pass.
- [x] Outputs validate and neither analysis mode has known silent engineering-fixture leakage.
- [x] Downloader security/format cases pass; partial jobs resume and export successes.
- [x] Request count/cost availability are visible; deployment/user/release docs are complete.
- [ ] User approves values, copy rules, review thresholds, and output format.

## Verification

```bash
python -m pytest
ruff check .
```

Then run the documented end-to-end release checklist.
