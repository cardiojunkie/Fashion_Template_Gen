# User guide — 0.1.0-rc1

## CMS Generator

1. Select one of seven attribute sets and confirm its product profile. Men's Accessories requires one of five isolated profiles.
2. Upload a genuine `.xlsx` with text columns `sku`, `base_code`, `attributes__lulu_ean`, `attributes__shipping_weight`, and `input_data`. `base_code` groups variants; `input_data` is untrusted evidence for that SKU.
3. Upload `.jpg`, `.jpeg`, `.png`, or `.webp` files named `SKU-positiveOrdinal.ext`, directly or in a safe ZIP.
4. Review each base-code group. `PER_SKU` is the safe default. Use `BASE_CODE_SIZE_ONLY` only when variants differ solely by size and warnings are clear.
5. Check planned SKU/group counts, vision/text calls, fixed NVIDIA Inkling model, concurrency, cache hits, and hard call limit. Missing approved pricing displays cost unavailable.
6. Click **Test NVIDIA Connection**. It must pass for the current server session/key before **Run Data Extraction** is enabled. Extraction never starts automatically after upload. Failed units do not erase successful units; cancellation stops new scheduling but may not stop an already-sent request.
7. Review conflicts, unknowns, unmapped values, low evidence, and image-derived color. Unsupported technical claims stay blank. Accept/edit/blank/reject decisions persist.
8. Generate factual text-only catalog copy. Missing evidence never gets invented merely to fill six bullets.
9. Download the exact-header CMS workbook and separate QC workbook. A partial job exports successful rows only; QC lists every incomplete SKU.

Warnings: size-only mode can leak variant facts if grouping is wrong; official permitted values remain incomplete for six sets; broad vision-derived color needs review; cost estimates are not provider invoices.

## Image Downloader

Upload `.xlsx` with text SKU in column A and URLs in later columns. Physical URL column determines the output ordinal, including blanks and failures. Only public HTTP(S) images are accepted. Successful files are EXIF-oriented, decoded, centered without default upscale on a 1500×1500 white canvas, saved as `sku-N.jpg`, and placed in a flat image-only ZIP. The separate report retains each original URL result.

## Registry and configuration

The Attribute Registry page shows exact headers, profile applicability, missing approved data, centralized limits, pricing approval, and permitted-value sources. Edit `config/attribute_registry.xlsx` only from an approved source, then run:

```bash
python -m fashion_cms.registry config/attribute_registry.xlsx
```

Do not invent values or use `attributes__other_information` as a fallback.

## NVIDIA connection

Set `NVIDIA_API_KEY` only in the server environment. The endpoint
`https://integrate.api.nvidia.com/v1/chat/completions`, model `thinkingmachines/inkling`, and request
settings are fixed. The connection test uses only a generated blue-square image and requires exact
SGLang JSON-schema output; it sends no uploaded product data. There is no provider-management page, alternate
model, browser key entry, or fallback. Rotation and troubleshooting are in `docs/LLM_PROVIDERS.md`.

## Job History and release readiness

Job History shows modes, planned/attempted calls, fixed endpoint/model fingerprints, failures, cache hits, cancellation, artifacts, and supported retry/resume actions. Extraction retries after restart require the same validated inputs and a new connection pass because image bytes are not stored. Release Readiness shows every mandatory gate; blocked or not-run gates prevent a production-ready claim.

## Backup and troubleshooting

Follow `BACKUP_ROLLBACK.md` before migration or upgrade. Common blockers are wrong workbook type, the rejected legacy `model_code_input_data` header, formula cells, duplicate SKU, unsafe ZIP/image, missing profile, changed registry, unresolved review, exhausted call/cost limit, a missing/failed NVIDIA connection, or an unapproved release gate. Keep Codespaces forwarded ports private; development mode has no authentication.
