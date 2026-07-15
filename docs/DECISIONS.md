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

## 2026-07-15 — Phase 5 Topwear extraction contract

- Live extraction uses the OpenAI Responses API directly through the existing `httpx` dependency. A small client protocol keeps the deterministic fake implementation as the offline/default test path; the application remains usable without `OPENAI_API_KEY` or `OPENAI_MODEL`, and every live call requires explicit operator confirmation.
- The versioned Topwear prompt and strict Structured Outputs schema use nullable fields and are validated again with Pydantic plus registry/evidence checks before acceptance. Refusals, incomplete responses, malformed structures, unknown identifiers, invalid enums, contradictory duplicates, and policy-violating observations never enter the accepted-result cache.
- The registry now contains the `topwear_mvp` applicability profile and exactly seven approved broad visual colors: Blue, Red, White, Black, Green, Grey, and Brown. Supplied color wins and creates a warning on visual conflict; absent color may use one of those broad values with image provenance. No nuanced-shade alias was approved, so visually proposed specific shades remain unknown.
- SQLite schema v2 adds sanitized per-work-item request metadata, including actual model, request ID, versions, detail, status, retries, and usage. It stores neither credentials nor raw authorization data. Uploaded image bytes remain outside SQLite; a restart-time retry requires the same validated inputs to be re-uploaded.
- Cache identity now includes the representative SKU and row-specific base code, EAN, and shipping weight in addition to the Phase 4 inputs. Representative overrides or any supplied row-data change therefore invalidate the result even when the selected image set is otherwise identical; cached records are semantically revalidated against current evidence and registry rules before reuse.

## 2026-07-15 — Phase 6 review, copy, and export contract

- Review decisions use the existing SQLite job store (schema v3) and retain the proposal, final value, action, note, timestamp, registry/prompt/schema/model versions, and evidence reference. A registry change revalidates stored values and flags invalid decisions without rewriting them.
- Topwear `name` and `attributes__product_title` are identical deterministic titles for the MVP. Missing series, material, model, or other components are omitted; series name has no approved registry source and model year has no output column.
- Catalog copy remains a separate text-only request over accepted facts. The fixed Topwear title/bullet rules and environment-configurable keyword separator are the only Phase 6 configuration; missing approved character limits produce QC warnings rather than invented truncation limits.
- Size-only groups may reuse bullets and keywords only in confirmed `BASE_CODE_SIZE_ONLY` mode when all non-size accepted facts match and group warnings show no material text/pack difference. Titles remain SKU-specific. `PER_SKU` never shares copy.
- Only an unchanged reviewer-accepted broad image-derived color receives the yellow fill and inference note. A reviewer edit is the higher-priority source and is therefore not labeled as image inference.
- Registry descriptions currently repeat overlapping field names without defining semantic differences. Affected proposals receive review warnings; the application does not duplicate or invent distinctions pending approved registry definitions.
