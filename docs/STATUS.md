# Project Status

Current phase: Phase 2 — Deterministic workbook and image-input pipeline
Status: completed
Last updated: 2026-07-15

## Completed

- Added the CMS attribute-set selector, safe local `.xlsx` parsing into strict Pydantic rows, severity-grouped validation, ready preview, and blocked critical-error flow.
- Preserved trimmed SKU, base code, and EAN text—including leading zeros—and retained blank base-code cells with the SKU fallback group key.
- Added bounded direct image/ZIP intake for JPEG, PNG, and WEBP; safe in-memory ZIP handling; complete-SKU/final-ordinal matching; EXIF-aware previews; and missing, orphan, duplicate, unsupported, unreadable, and malformed-file reporting.
- Added exact registry-ordered blank CMS `.xlsx` export with one row per input SKU, system-copy fields only, text identifier cells, formula-injection protection, and no extra CMS columns.
- Kept the implementation local and deterministic with no network, downloader, image-standardization, job, or LLM code.
- Audited every Phase 2 acceptance criterion, fixed README diff hygiene, and removed unused image-hash metadata that belonged to a later cache phase.

## Verification

- `python -m pytest tests/test_registry.py tests/test_excel_service.py tests/test_image_service.py`: pass; 37 tests passed.
- `python -m pytest`: pass; 37 tests passed.
- `ruff check .`: pass; no findings.
- `git diff --check`: pass; no whitespace errors.
- `python -m fashion_cms.registry config/attribute_registry.xlsx`: pass; 7 sets and 78 definitions validated.
- Streamlit AppTest and `streamlit run app.py --server.headless true`: pass; the app rendered and the server reached ready state.
- Manual in-memory audit: all seven selected attribute sets exported three exact rows and reopened; leading zeros, blank null cells, literal formula-like identifiers, safe ZIP handling, hidden-file ignores, final-ordinal matching, and warning/critical gates passed.

## Decisions or blockers

- No blockers.
- True `.xls` support remains an explicit product decision. It was not added because `.xlsx` is canonical and no `.xls` consumer requirement has been confirmed.
- Phase 2 file limits and severity defaults are recorded in `docs/DECISIONS.md`.

## Next action

```text
Continue the Fashion CMS Upload Generator project by implementing Phase 3 only.

Read AGENTS.md, docs/STATUS.md, docs/PRODUCT_CONTRACT.md, docs/phases/03-image-module.md, and the Phase 3 section of PLAN.md. Inspect the existing implementation and confirm Phase 2 acceptance still passes before editing. Do not begin Phase 4.
```
