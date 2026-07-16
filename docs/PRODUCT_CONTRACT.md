# Product Contract

This is the stable, distilled contract from Sections 3–11 of `PLAN.md`. `PLAN.md` wins if wording ever conflicts.

## Objective and boundaries

Build a secure, auditable Streamlit dashboard that:

- turns SKU-level fashion data and SKU-linked images into exact CMS upload workbooks;
- safely downloads and standardizes URL-sourced product images; and
- provides a validated attribute registry, evidence-aware review, and resumable job history.

Use Python 3.12, Streamlit, Pydantic v2, pandas/openpyxl, Pillow, httpx, `sqlite3`, pytest, Ruff, and the OpenAI Responses API with Structured Outputs when their phases require them. Do not add a separate frontend, microservices, queues, vector storage, Kubernetes, fine-tuning, separate OCR, a database abstraction over SQLite, premature batch processing, or web scraping during the MVP.

## Input workbook

Required columns are `sku`, `base_code`, `attributes__lulu_ean`, `attributes__shipping_weight`, and `model_code_input_data`.

- Treat SKU, base code, and EAN as trimmed strings; preserve leading zeros and original identifier text.
- Reject duplicate SKUs. Report duplicate EANs without silently discarding rows.
- A blank base code is a warning and gets an internal SKU fallback group key; keep the output base-code cell blank.
- Treat product data, formulas, and hyperlinks as untrusted. Never execute workbook formulas or macros.
- Explicit input wins over inference unless a reviewer approves a correction.

## Uploaded images

- Accept `.jpg`, `.jpeg`, `.png`, and `.webp`.
- Parse `SKU-ordinal.ext` using the final positive-integer suffix because SKUs may contain hyphens.
- Before any LLM request, report missing, orphaned, duplicate-ordinal, unreadable, and unsupported images.
- Apply EXIF orientation before preview or analysis.
- Explicitly label every image with its SKU and ordinal in multimodal requests.
- Prevent ZIP path traversal and ignore hidden operating-system files.

## Variant analysis

Each base-code group has its own editable mode; `PER_SKU` is the default.

- `PER_SKU`: one extraction request per SKU, using only that SKU's data and images; never inherit visual values.
- `BASE_CODE_SIZE_ONLY`: one representative request only when variants differ by size and show the same product. Choose a user selection first, otherwise the SKU with most valid images, then workbook order.
- Size-only sharing never overwrites SKU, EAN, base code, shipping weight, size, model, or explicit input.
- Warn or block size-only confirmation when color, pattern, product type, or pack count varies.
- Do not implement `HYBRID_SHARED_STYLE` before post-Phase-8 evaluation.

## Evidence and inference

Source priority is structured input, explicit product text, readable label/package text, visible characteristics, then approved deterministic rules. Conflicts always require review.

Every extractable definition uses one policy: `SYSTEM_COPY`, `EXPLICIT_TEXT_ONLY`, `VISUAL_OR_TEXT`, `DERIVED_BUSINESS_RULE`, or `GENERATED_CONTENT`.

- Identifiers and supplied weights are system copy.
- Names, titles, keywords, and bullets are generated only from accepted normalized facts.
- Exact composition, care, technical performance, certification, measurement, and origin are normally explicit-text-only.
- Broad visible form such as color, pattern, neckline, sleeve, closure, toe/heel, bag/cap, and frame/lens shape may use visual or text evidence.
- Gender, age group, season, occasion, and comfort require input evidence or an approved deterministic rule; never infer them from a human model's appearance.
- Unsupported values are internally `unknown` and export as blank, never as invented placeholders.

For Topwear color extraction, an explicitly supplied product-data color always wins. Vision must
not replace it, refine it to a more specific shade, or mark it as image-inferred; an apparent image
conflict retains the supplied value and creates a review warning. When color is absent, vision may
use only an approved broad registry value such as Blue, Red, White, Black, Green, Grey, or Brown.
A visually proposed nuanced shade must map through an approved broad alias or remain unknown; it
must never create a registry value. Broad image-derived color retains image provenance and a review
flag so Phase 6 can highlight the populated cell. Size is never inferred from garment proportions
or a person wearing the garment.

Phase 6 requires explicit review of broad image-derived color. Yellow CMS highlighting and the QC
inference note apply only when the reviewer accepts the unchanged image proposal. An edited color
is a reviewer override and is not labeled as image-inferred.

## Normalization and output

Code owns final CMS values. Match in this order: exact canonical value, Unicode/case/whitespace/punctuation-normalized canonical value, approved alias, optional review-only fuzzy suggestion, then blank plus review flag. Never silently add a model-proposed value.

- Emit one output row per input SKU and exact selected-set headers in Appendix A order.
- Keep debug, evidence, confidence, and internal status out of the CMS sheet.
- Preserve SKU, base code, and EAN as text; write nulls as blanks.
- Sanitize untrusted text that could become an Excel formula.
- Validate character limits and enum values before download.
- `.xlsx` is canonical. A true `.xls` requires a real tested writer/conversion path and must be blocked if data would be lost; never rename an `.xlsx` file.
- Keep validation/review reports separate from the CMS workbook.

Topwear review decisions persist in SQLite and retain proposal/final value, action, note,
timestamp, registry/prompt/schema/model versions, and evidence reference. Registry changes
revalidate stored enum values and flag invalid decisions without silently changing them.

## Catalog copy

- Use normalized accepted facts only; do not resend images for copy generation.
- Keep copy neutral, factual, and non-promotional.
- Target six short bullets but leave unsupported bullets blank and flag insufficient evidence.
- Use attribute-set-specific configured templates and approved golden examples.
- Do not mention missing data, image availability, warranties, or unsupported claims.

For the Topwear MVP, code deterministically builds identical `name` and
`attributes__product_title` values from accepted brand, approved series name when available,
material, product type, SKU-specific size, color, and explicit model number. Missing components
are omitted. SKU, EAN, base code, and model year never enter the title.

## Auditability

Retain per generated value: header, raw value, canonical value, status, evidence type/reference, confidence band, normalization rule, and any user override. Confidence is a review hint and never overrides missing or contradictory evidence.

## Security and privacy

- Keep API keys in server environment variables or Codespaces secrets; never expose them in UI, logs, reports, or downloads.
- Treat spreadsheets, descriptions, URLs, names, ZIPs, and images as untrusted.
- Protect downloads from SSRF: HTTP/HTTPS only; reject local, private, link-local, and loopback destinations; revalidate DNS/IP on redirects.
- Bound file count/size, response size, decoded pixels, timeout, decompression, concurrency, and retention.
- Never log raw image bytes or complete secrets.

## Attribute registry

`config/attribute_registry.xlsx` is the source of truth and has five sheets:

- `Attribute_Sets`: set ID/name, position, header, required.
- `Attribute_Definitions`: header, data type, scope, evidence policy, nullable, description, unit/format.
- `Permitted_Values`: one header row with canonical values starting at `value_1`.
- `Value_Aliases`: header, alias, canonical target, active.
- `Product_Profiles`: set, product type, profile, header, applicable.

Allowed data types are `ENUM`, `FREE_TEXT`, `INTEGER`, `DECIMAL`, `BOOLEAN`, `SYSTEM_COPY`, and `GENERATED_TEXT`. Allowed scopes are `SYSTEM`, `SKU`, `VARIANT`, `STYLE`, and `JOB`.

Reject activation for duplicate set headers, missing/duplicate positions, missing definitions, invalid alias targets, enums without values, invalid profile references, or generated system identifiers. Reject normalized duplicate canonical values. Never add unapproved values.

Men's Accessories eventually requires profile-specific mappings for bags/luggage, caps/headwear, watches, eyewear, and other accessories; approval is gated to its implementation phase.

## Internal contracts and processing

Use focused, versioned Pydantic models for input rows, images, variant groups, attribute observations, vision results, and review decisions. Internal provenance never becomes CMS columns.

For each SKU/header merge in this order: accepted reviewer override, structured input, normalized product text, label/OCR evidence, policy-permitted visual evidence, approved business rule, blank. Never use an LLM to copy identifiers between rows.

Load only headers applicable to the selected set/profile. Leave non-applicable fields blank and do not misuse `attributes__other_information` as a fallback.

Vision cache keys include mode, ordered identifiers, normalized product data, image hashes, set/profile, registry/prompt/schema versions, model, and image detail. Configuration or image changes invalidate cache entries.

Job states are `UPLOADED`, `VALIDATING`, `READY`, `RUNNING`, `REVIEW_REQUIRED`, `COMPLETED`, `PARTIAL_FAILURE`, and `FAILED`. Isolate failures per group/SKU and preserve successful work.

## Image downloader

- Column A is text SKU; later columns are URLs whose physical positions determine ordinals. Skip blanks without renumbering.
- Default limits: 8 total and 4 per-host downloads, 10-second connect and 30-second read timeout, 3 retries, 25 MB response, and 50 megapixels decoded.
- Retry temporary failures with bounded exponential backoff and jitter.
- `PAD_WHITE`: safely decode, EXIF-transpose, composite transparency onto white RGB, preserve aspect ratio, fit inside a default 1400×1400 box without default upscaling, center on a 1500×1500 white canvas, save optimized JPEG at quality 95.
- Background removal stays optional and replaceable.
- Produce a deterministic flat image-only ZIP and a separate detailed report.

## LLM integration

The application calls the OpenAI Responses API directly; Codex is not the runtime. Configure `OPENAI_API_KEY`, `OPENAI_MODEL`, and `OPENAI_IMAGE_DETAIL` in the environment when the LLM phase begins.

Requests include only applicable headers/enums, clearly delimited untrusted data, explicit image labels, controlled images, and a strict schema. Prompts reject embedded instructions, unsupported exact claims, and invented values, and require unknowns and conflict reporting.

Keep vision/data extraction separate from text-only generation until evaluation supports combining them. Retry only temporary/rate-limit failures; store request IDs and sanitized errors; make partial work resumable. Default tests use a fake client, and live tests are opt-in.

Post–Phase 8, administrators in a private development deployment may configure OpenAI-compatible
Responses or Chat Completions providers in the website. Vision extraction and catalog copy use
independent logical routes; activation requires current capability tests and never silently falls
back to another provider/model. Native non-OpenAI protocols require dedicated adapters.

Website-entered secrets default to server-session memory. Persistent configuration stores either
an environment-variable name or AES-GCM ciphertext protected by an external 32-byte master key;
plaintext keys never enter SQLite, URLs, logs, job/cache records, reports, widget defaults, or
history. Encrypted database storage is unavailable without the master key and, in production,
application authentication.

Custom base URLs are an SSRF boundary: public deployment requires HTTPS, verified TLS, public
destinations, bounded responses, known adapter paths, and redirect rejection. Local/private HTTP
endpoints require explicit development-only server flags plus an exact host allowlist, and those
flags are ignored in production.

## Quality gates

Build a manually checked golden set before broad rollout: at least ten Topwear base codes before Phase 6 and representative examples from every set/accessory profile before release.

Track canonical precision, evidence-supported coverage, unsupported-claim rate, blank rate, variant leakage, conflict detection, image association, workbook validation, request count, latency, and cost. Prefer precision over artificial coverage.

Auto-accept only when evidence policy permits it, the canonical value is valid, no higher-priority source conflicts, and an approved rule allows it. Model confidence alone never qualifies.
