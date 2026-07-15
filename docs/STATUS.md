# Project Status

Current phase: Phase 5 — Topwear multimodal extraction
Status: completed
Last updated: 2026-07-15

## Completed

- Reconfirmed the Phase 4 foundation: default and mixed analysis modes, deterministic representative selection and override persistence, exact request planning, restart-safe SQLite state, isolated partial failures, complete cache invalidation, and all four Streamlit pages remain functional.
- Added a replaceable deterministic fake client and direct OpenAI Responses API client with environment-only credentials, configurable model and image detail, bounded retry for temporary failures, refusal/incomplete handling, sanitized errors, and no-key startup.
- Added a versioned Topwear prompt, strict nullable Structured Outputs schema, Pydantic parsing, registry-backed applicability and enum checks, explicit SKU/image labels, prompt-injection boundaries, evidence provenance, conflicts, warnings, request metadata, and usage storage.
- Enforced conservative evidence policy and approved color behavior: supplied color wins; missing color can use only seven broad registry values with image provenance; unsupported shades and visually inferred size, composition, origin, weight, dimensions, demographics, comfort, or technical claims remain unknown.
- Integrated validated result caching and representative-SKU invalidation with exact execution-count checks, per-work-item failure isolation, cache-aware retry/resume, and representative-image provenance for size-only sharing.
- Extended CMS Generator with a Topwear MVP request plan, cached/request-required status, explicit live-call confirmation, progress and counts, read-only observations/evidence, warnings/conflicts, and failure-only retry. No Phase 6 editing or generated CMS content was added.
- Added 12 manually inspectable golden Topwear fixtures and offline coverage for request construction, modes, evidence/color policy, output validation, resilience, caching, security, partial failure, and registry behavior.

## Verification

- Phase 4 preflight: 34 focused tests and 126-test baseline passed; Ruff, launcher checks, diff check, health endpoint, and all four pages passed.
- `python -m pytest tests/test_llm_service.py tests/test_topwear_extraction.py tests/test_cache.py`: pass; 93 passed, 1 live test skipped.
- `python -m pytest`: pass; 219 passed, 1 live test skipped.
- `ruff check .`: pass; no findings.
- `python -m fashion_cms.registry config/attribute_registry.xlsx`: pass; 7 sets and 78 definitions validated.
- `bash -n start.sh`, executable check, and `git diff --check`: pass.
- Streamlit AppTest: pass for Topwear selection, 2-request `PER_SKU` plan, 1-request `BASE_CODE_SIZE_ONLY` plan, fake completion, read-only evidence/warnings, cache transition, and the three other pages.
- Bounded `./start.sh 8505` smoke test: pass; local and private Codespaces-forwarded health checks succeeded, all four routes returned HTTP 200, and the server stopped cleanly.
- Live integration test: NOT RUN because `OPENAI_API_KEY` and `OPENAI_MODEL` were not configured; the default fake-client suite passed without network access.

## Decisions or blockers

- No Phase 5 blockers or unresolved acceptance failures.
- The fake client remains the safe default. Live controls stay disabled until both required live settings are present.
- Uploaded image bytes are intentionally not retained in SQLite; restart-time extraction or retry requires re-uploading the same validated inputs.

## Next action

Create the Phase 5 checkpoint commit. Phase 6 is the next implementation phase, but do not begin it until explicitly requested.
