# Security and reliability test summary — 0.1.0-rc1

Default tests are offline. The live NVIDIA Inkling check is explicitly opt-in.

- Workbook: type deception, malformed ZIP/XML, encryption, macros, external links, formulas, Excel errors, member/expanded/file/row/column/cell limits, identifiers, and formula-safe export.
- ZIP/image: traversal, absolute paths, hidden files, symlinks, nesting, flat-name collisions, file/member/expanded limits, magic/extension mismatch, malformed data, decompression/pixel/dimension limits, animation, unsupported modes, EXIF, transparency, CMYK, and deterministic JPEG/ZIP output.
- URL: non-web schemes, credentials, loopback/private/link-local/multicast/reserved/transition ranges, hostile DNS, redirect revalidation, IP pinning and peer verification, content type/length/body limits, connect/read/total deadlines, Retry-After, retry policy, per-host/total concurrency, ordinal preservation, and per-URL isolation.
- Model boundary: delimited injection strings, strict applicable schema, unknown SKU/header/image rejection, invalid enum/evidence/status rejection, unsupported technical claims, request/version metadata, and cache invalidation.
- NVIDIA runtime: fixed endpoint/model/request settings, missing/auth/rate-limit/timeout/redirect/
  malformed/duplicate/oversized response handling, exact-model SGLang response-format blue-square
  diagnostic with non-forced alternatives, session/key-bound extraction gating, image
  serialization, safe response metadata, job fingerprints, and plaintext-secret absence.
- Data safety: secret and authorization redaction, raw-secret absence from requests/errors/SQLite, formula-safe CMS/QC/report values, root-scoped idempotent cleanup, active-job protection, and durable cleanup disabled pending approval.
- Reliability: bounded model concurrency, persistent call circuit, partial failure, failure-only retry, interrupted and cancelled resume, no duplicate successful calls, successful partial QC export, SQLite migration/backup/reopen, and cached-result revalidation.

No dedicated static security scanner or type checker is configured in `pyproject.toml`; none was invented for the release. Ruff, `pip check`, registry validation, shell syntax, and clean-environment installation are part of the release checklist.
