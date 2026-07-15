# Project Status

Current phase: Phase 4 — Variant groups, persistent jobs, and cache
Status: completed
Last updated: 2026-07-15

## Completed

- Added collision-safe base-code grouping with SKU-only fallback for blank base codes, per-group and bulk `PER_SKU` / `BASE_CODE_SIZE_ONLY` selection, conservative supplied-data warnings, size-only suggestions, and deterministic representative selection.
- Added exact request planning for mixed modes with represented SKUs, selected representative images, and complete deterministic cache keys covering every contract component.
- Added a versioned standard-library SQLite schema for jobs, normalized input rows, groups, image metadata/hashes, work items, fake result cache entries, errors/retries, timestamps, and artifact references.
- Added validated job transitions, transactional writes, isolated partial failures, interrupted-job resume, failure-only retry, and cache reuse without repeating successful fake extraction work.
- Added functional CMS Generator controls and read-only Attribute Registry and Job History pages with plans, modes, failures, retry/resume, cache-hit counts, and safe artifact access.
- Added executable `start.sh` startup for local/Codespaces forwarding with validated port precedence, dependency checking, headless Streamlit, usage-stat disabling, foreground `exec`, and private-by-default documentation.
- Kept Phase 4 orchestration local: no LLM client, API key, real model call, catalog generation, or Phase 5 implementation was added.

## Verification

- Prior Phase 3 baseline before editing: 84 tests passed; Ruff, registry validation, and `git diff --check` passed.
- `python -m pytest tests/test_variant_service.py tests/test_database.py tests/test_jobs.py`: pass; 34 tests passed.
- `python -m pytest`: pass; 126 tests passed.
- `ruff check .`: pass; no findings.
- `python -m fashion_cms.registry config/attribute_registry.xlsx`: pass; 7 sets and 78 definitions validated.
- `bash -n start.sh`, executable check, and `git diff --check`: pass.
- Streamlit AppTest: pass for CMS upload validation, persistent job creation, exact two-request plan, fake completion, Image Downloader, Attribute Registry, and Job History rendering.
- Bounded `./start.sh` smoke test: pass; health returned `ok`, all four routes returned HTTP 200, the launcher process was the foreground Streamlit process, and termination exited with status 0.

## Decisions or blockers

- No blockers.
- The persistent database defaults to `data/fashion_cms.sqlite3`; image bytes remain outside SQLite and only validated metadata and SHA-256 hashes are stored.
- Size-only detection remains advisory. Warnings never silently select or block a mode; the operator must explicitly choose it based on the visible-product rule.
- Phase 4 uses deterministic fake extraction results only. OpenAI client selection, live credentials, prompts, structured extraction, and product-profile extraction remain Phase 5 work.

## Next action

Create the Phase 4 checkpoint commit. Do not begin Phase 5 until it is explicitly requested.
