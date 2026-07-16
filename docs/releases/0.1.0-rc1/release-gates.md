# Release gates — 0.1.0-rc1

Verdict: **BLOCKED**. The engineering release candidate is ready for user acceptance, but production release is not approved.

| Gate group | Status | Evidence or blocker |
|---|---|---|
| Tests, lint, registry, exact exports | PASS | 396 tests passed; one opt-in live NVIDIA test skipped; Ruff and registry validation passed. |
| Seven workflows and accessory isolation | PASS | Offline end-to-end and strict applicability checks passed. |
| Security, NVIDIA runtime, recovery, cancellation, backup | PASS | Automated boundary, NVIDIA, migration, and recovery checks passed. |
| Evaluation framework and leakage formula | PASS | Deterministic engineering checks passed; not live accuracy evidence. |
| Human golden dataset | BLOCKED_USER_DECISION | Representative labels require human review and approval. |
| Live two-model comparison | NOT_RUN | Approved models, credentials, pricing, thresholds, and golden truth are absent. |
| Deployment configuration | BLOCKED_USER_DECISION | Host and authentication are unapproved. |
| Business rules | BLOCKED_USER_DECISION | See `USER_SIGNOFF.md`. |

The machine-readable report is `release-gates.json`; the dashboard uses it and cannot display production readiness while any mandatory gate is not `PASS`.
