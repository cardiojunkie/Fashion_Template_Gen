# Project Status

Current phase: Phase 3 — Image downloader and 1500 × 1500 standardizer
Status: completed
Last updated: 2026-07-15

## Completed

- Added a native Image Downloader Streamlit page for bounded `.xlsx` URL-workbook parsing, physical-column ordinals, text SKUs, previews, downloads, and failure-only retry.
- Added HTTP/HTTPS-only downloads with public-IP DNS validation, validated-IP connection pinning, Host/TLS SNI preservation, redirect revalidation, proxy bypass, peer verification, total/per-host concurrency limits, timeouts, retries, bounded jittered backoff, and response limits.
- Added verified JPEG/PNG/WEBP decoding, 50-megapixel enforcement, EXIF orientation, safe RGB/alpha/palette/greyscale/CMYK conversion, low-resolution warnings, and exact no-crop/no-stretch/no-default-upscale `PAD_WHITE` output.
- Added deterministic flat image-only ZIP output, a separate text-safe XLSX report, sanitized errors, aggregate memory limits, and session-only reuse of successful files when failed URLs are retried.
- Defined the replaceable background-removal adapter without installing or requiring a background-removal dependency.
- Kept all processing in memory with no temporary paths, jobs, cache/database, LLM, or Phase 4 implementation.

## Verification

- `python -m pytest tests/test_image_downloader.py tests/test_image_service.py`: pass; 63 tests passed offline with mocked HTTP.
- `python -m pytest`: pass; 84 tests passed.
- `ruff check .`: pass; no findings.
- `git diff --check`: pass; no whitespace errors.
- `python -m fashion_cms.registry config/attribute_registry.xlsx`: pass; 7 sets and 78 definitions validated.
- Streamlit AppTest: pass for both pages, leading-zero SKU/ordinal-2 preview, mocked partial failure, retry without repeating success, low-resolution preview, and separate ZIP/report downloads.
- `streamlit run app.py --server.headless true` plus `/_stcore/health`: pass; server reached healthy state.

## Decisions or blockers

- No blockers.
- `REMOVE_AND_WHITE` remains an adapter contract only; selecting a background-removal implementation is deferred until a lightweight, approved need exists.
- Retry state is intentionally scoped to the current Streamlit session; persistent jobs/cache belong to Phase 4 and were not started.
- True `.xls` support remains an explicit product decision; `.xlsx` is canonical.

## Next action

Create the Phase 3 checkpoint commit. Do not begin Phase 4 until it is explicitly requested.
