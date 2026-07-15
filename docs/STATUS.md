# Project Status

Current phase: Phase 6 — Review, catalog copy, and Topwear export
Status: completed
Last updated: 2026-07-15

## Completed

- Reconfirmed Phase 5 extraction, evidence/color policy, strict validation, caching, fake-client operation, and secret safety before building on it.
- Added exact, normalized, approved-alias, and review-only fuzzy normalization. `A-Line Fit` maps only to the active `A-Line` value for `attributes__fit_type`; unmapped and ambiguous values remain blank and reviewable.
- Added deterministic source priority, conflict retention, overlapping-field warnings, broad image-color review, and persisted accept/edit/blank/reject decisions with registry revalidation.
- Added the editable Topwear review UI with permitted-value enum controls, detailed provenance, required filters, safe bulk acceptance, model-year schema warnings, and restart-safe SQLite decisions.
- Added deterministic SKU-specific titles and separate text-only keyword/bullet generation from accepted facts, strict provenance/content validation, one bounded retry, insufficient-evidence warnings, and size-only copy reuse.
- Added exact 45-column Topwear `.xlsx` export, text-preserved identifiers, formula sanitization, enum/row/header/reopen validation, accepted image-color yellow fills, and a separate QC workbook.
- Added offline normalization, review, catalog, workbook, QC, size-variant, and upload-to-export tests. No Phase 7 attribute-set implementation was added.

## Verification

- Phase 5 preflight: `93 passed, 1 skipped` in the focused suite; all requested launcher, lint, diff, registry, and complete-suite checks passed.
- Phase 6 focused suite: `34 passed`.
- Complete suite: `253 passed, 1 skipped`; the skipped test is the opt-in live OpenAI integration test.
- `ruff check .`, registry validation, `bash -n start.sh`, executable check, and `git diff --check`: pass.
- Bounded `./start.sh 8506` smoke: local and private Codespaces health endpoints returned 200, all four application routes returned 200, the default page completed Streamlit AppTest without exceptions, and the server stopped cleanly.
- A browser binary was unavailable for literal click-through testing. Review actions, refresh-safe persistence, blocking, downloads, and workbook reopening are covered by the focused and end-to-end automated tests.

## Decisions or blockers

- No Phase 6 acceptance blocker remains.
- No approved catalog-copy character limits, series-name source/header, occasion values/business rules, or semantic definitions for overlapping Topwear fields exist in the active registry. The application omits unsupported values and emits review/QC warnings instead of inventing them.
- Model year remains outside the 45-column Topwear schema and is recorded only as a pending schema warning in review/QC when encountered.
- Live content generation was not run because live credentials/model configuration were not supplied; the default fake-client path passed.

## Next action

Create the Phase 6 checkpoint commit. Phase 7 is next, but must begin only on an explicit request.
