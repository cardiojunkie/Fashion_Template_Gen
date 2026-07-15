# Decision Records

## 2026-07-15 — Phase 2 local file boundaries

- `.xlsx` is the only accepted input and output workbook format. True `.xls` remains deferred until the CMS consumer confirms a need and a tested parser/writer can guarantee no data loss.
- Workbooks and ZIP members are validated and read in memory; ZIP paths are never extracted to the filesystem. Workbook limits are 25 MB uploaded, 100 MB expanded, 2,000 internal members, 100,000 rows, and 500 columns.
- Uploaded image limits are 25 MB and 50 megapixels per image, 100 MB per ZIP, 1,000 members per ZIP, 500 top-level/expanded files, 250 MB uploaded, and 500 MB expanded in total. Validation reports are bounded.
- Ambiguous, unsafe, malformed, unreadable, mislabeled, or over-limit input is critical. Blank optional workbook values, duplicate EANs, missing/orphan images, unsupported extensions, and malformed image names are warnings and may continue.

These defaults satisfy the untrusted-input and data-loss boundaries in the product contract without adding storage, archive, or legacy Excel dependencies.

## 2026-07-15 — Phase 3 network and output boundaries

- URL workbooks reuse the Phase 2 `.xlsx` preflight and are capped at 500 URLs. SKU text is never sanitized into a different identifier; values that cannot form a safe flat filename are blocked for correction.
- Downloads use declared `httpx` with environment proxies disabled. Every HTTP/HTTPS destination and redirect must resolve only to public IPs, and the connection is pinned to a validated address while preserving the original Host header and TLS SNI. Non-public, metadata, multicast, transition, and mismatched peer addresses are rejected.
- Secure defaults are configurable through the `FASHION_CMS_IMAGE_*` environment variables documented in `.env.example`; absolute validation ceilings remain in code. Decoding is serialized to cap worst-case 50-megapixel memory use, and retained processed output is capped at 500 MB.
- Successful outputs remain in memory. The image ZIP is flat, image-only, and byte-deterministic; the separate report is `.xlsx` with all untrusted strings forced to safe literal text.
- Failed-URL retry is session-only and keyed by SKU, physical ordinal, and source URL, so successes are reused without introducing Phase 4 persistence or cache infrastructure.
- `REMOVE_AND_WHITE` is a replaceable protocol only. `PAD_WHITE` remains the required dependency-free path.

## 2026-07-15 — Phase 4 grouping, persistence, and cache

- Persistent orchestration uses Python `sqlite3` at `data/fashion_cms.sqlite3` by default, with `PRAGMA user_version` migrations and direct transactional operations. The v1 schema stores normalized rows, group selections, image metadata/hashes, work items, fake result references/cache entries, failures/retries, and artifact references; it never stores image bytes or secrets.
- Persisted group keys are typed as `base:<base_code>` or `sku:<sku>` so a blank-base fallback SKU cannot collide with another row's supplied base code. The original nullable `base_code` remains unchanged and the internal key can never enter CMS output.
- `PER_SKU` remains the default. Size-only suggestions and difference warnings are advisory supplied-text checks; only an explicit per-group or bulk operator action changes analysis mode.
- Cache entries use a canonical JSON payload and SHA-256 over mode, ordered group/SKU membership and normalized product text, representative image hashes, attribute set/profile, and registry/prompt/schema/model/detail versions. Phase 4 stores deterministic fake results under that key and makes no model call.
- Work is persisted per request. Successful and review-required items remain intact when siblings fail; failure-only retry increments only failed items, while interrupted pending/running items can resume.
