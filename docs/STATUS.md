# Project Status

Current phase: Post–Phase 8 — bring-your-own LLM provider extension
Status: engineering implementation/security audit complete; provider and release UAT pending
Release candidate: 0.1.0-rc1 (unpublished)
Last updated: 2026-07-16

## Completed

- Added SQLite schema v6 provider configuration, independent vision/catalog routes, versioned
  OpenAI Responses and Chat Completions adapters, discovery, capability tests, non-secret job
  snapshots, cache invalidation, disable/retire behavior, and offline fake-client compatibility.
- Added masked session-only keys, environment references, and development-only AES-GCM database
  secrets. Production encrypted entry stays disabled until real authentication exists.
- Added HTTPS/public-network defaults, DNS/IP pinning, peer validation, fixed endpoint paths,
  bounded responses/timeouts, strict auth headers, redirect blocking, sanitized errors, and exact
  development allowlists that cannot permit metadata/link-local/multicast/reserved addresses.
- Added the **LLM Providers** page, provider/operator guide, schema v6 backup/rollback notes, and a
  14-sheet UAT workbook with 22 provider-specific checks. Existing Phase 1–8 workflows remain
  unchanged and offline by default.

## Verification

- Full suite: `378 passed, 1 skipped`; the skip is the explicit opt-in live OpenAI test.
- `ruff check .`, `git diff --check`, `python -m pip check`, registry validation, and release-report
  validation: pass.
- `python -m pip install -e ".[dev]"`: pass with the new authenticated-encryption dependency.
- Migration: a populated schema v5 temporary database upgraded to v6 without losing its job;
  provider tables initialized empty and plaintext test secrets were absent from SQLite.
- Runtime: existing workspace `./start.sh 8501` returned `200 ok`; a separate `./start.sh 8502`
  returned `200` for `/` and `/LLM_Providers`, returned `ok` for health, stopped cleanly, and left
  no listener. Streamlit component testing verified the API-key widget is password-masked/blank.

## Release blockers

- Human approval of representative real-product ground truth for every set/profile.
- Live comparison of at least two approved models with credentials, approved pricing, and approved
  thresholds.
- Final permitted values, semantic field rules, title/copy rules, output format, background mode,
  and retention approval.
- Approved production host, authentication, HTTPS/reverse proxy, storage/backup, monitoring, and
  network-egress configuration.
- Approved live provider accounts/models, provider-side retention terms, pricing, and completion of
  the provider UAT checklist. Native non-OpenAI protocols still require dedicated adapters.

## Next action

Keep port 8501 private, follow section 15 of `uat/README.md`, and record all **LLM Providers** rows
in `uat/manual_uat_checklist.xlsx` using only approved test credentials. Then run the existing
Phase 1–8 UAT. Do not deploy, publish, tag, or mark production-ready until manual UAT and the user
sign-off ledger are complete.
