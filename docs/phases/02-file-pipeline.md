# Phase 2 — Deterministic workbook and image-input pipeline

## Goal

Parse and validate CMS input workbooks and uploaded images, preserve identifiers, and export an exact blank CMS skeleton. Make no network or LLM calls.

## Checklist

- [ ] Add the CMS Generator selector and safe `.xlsx` parsing into Pydantic rows.
- [ ] Preserve identifier text and validate required columns, duplicates, blanks, types, and formulas.
- [ ] Safely accept images/ZIPs; match the final ordinal suffix; validate decoding, EXIF, duplicates, missing, and orphan files.
- [ ] Show actionable severity-grouped validation and block critical errors.
- [ ] Create one exact ordered output row per SKU, copy system fields, and export `.xlsx`.
- [ ] Confirm true `.xls` need before adding a tested parser/writer.
- [ ] Cover leading zeros, hyphenated SKUs, malformed files, blank base codes, duplicate images, and orphans.

## Acceptance

- [ ] Valid inputs reach a ready preview and critical errors block processing.
- [ ] Leading zeros survive parse/export; `ABC-12-2.jpg` maps to SKU `ABC-12`, ordinal 2.
- [ ] Blank output has exact headers, no extras, and no network/LLM activity.
- [ ] Tests and Ruff pass.

## Verification

```bash
python -m pytest tests/test_registry.py tests/test_excel_service.py tests/test_image_service.py
ruff check .
```

