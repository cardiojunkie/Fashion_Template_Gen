# Project Status

Current phase: Phase 8 — Evaluation, security, production hardening, and release
Status: engineering complete; production release blocked on user approval and live evaluation
Release candidate: 0.1.0-rc1 (unpublished)
Last updated: 2026-07-16

## Completed

- Added centralized mandatory release gates and dashboard visibility; unresolved gates cannot produce a production-ready verdict.
- Added a versioned 13-case all-set/all-accessory engineering dataset, deterministic two-model comparison, sample-counted metrics, variant-leakage detection, and safe threshold policy routing.
- Hardened workbook, ZIP, filename, image, URL, prompt/model-output, secret/error, resource, cleanup, and export boundaries with Phase 8 regression coverage.
- Added centralized validated limits, request/cost preview and actual usage, persistent hard call accounting, bounded model concurrency/retries, cancellation/resume, successful partial export/QC, safe SQLite backup, and migration/rollback procedures.
- Added the threat model, security summary, manifests, evaluation report, deployment/user/backup guides, sign-off ledger, release/rollback checklists, and known limitations under `docs/releases/0.1.0-rc1/`.
- Preserved the exact seven-set CMS contracts, all five Men's Accessories profiles, existing downloader output, fake/offline defaults, and executable `./start.sh`.

## Verification

- Main and clean temporary environments: `317 passed, 1 skipped`; the skip is the explicit opt-in live OpenAI test.
- `ruff check .`, `git diff --check`, `python -m pip check`, registry validation, release-report validation, `bash -n start.sh`, and executable check: pass.
- Clean install: `python -m pip install -e ".[dev]"` passed in a new virtual environment.
- Runtime: `./start.sh 8501` health returned `ok`; Streamlit dashboard rendered with no AppTest exceptions; 95 critical workflow/security smoke tests passed; server stopped cleanly.

## Release blockers

- Human approval of representative golden truth for every set/profile.
- Live comparison of at least two approved models with credentials, approved pricing, and approved thresholds.
- Final permitted values, semantic field rules, title/copy rules, output format, and retention approval.
- Approved production host, authentication, HTTPS/reverse-proxy, storage/backup, monitoring, and network-egress configuration.

## Next action

Review `docs/releases/0.1.0-rc1/USER_SIGNOFF.md`. After approvals and credentials are supplied, run the live evaluation and production-like release checklist; do not deploy, tag, publish, or mark production-ready beforehand.
