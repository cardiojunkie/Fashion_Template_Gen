# Phase 3 — Image downloader and standardizer

## Goal

Deliver deterministic, SSRF-safe URL downloading, exact 1500×1500 white-canvas processing, flat ZIP output, and separate failure reporting.

## Checklist

- [ ] Parse text SKU from column A and retain URL ordinals from later physical columns.
- [ ] Enforce HTTP/HTTPS, DNS/redirect SSRF checks, concurrency, timeouts, retries, response size, and pixel limits.
- [ ] Validate content and implement the exact `PAD_WHITE` contract with sample previews.
- [ ] Save deterministic `sku-ordinal.jpg` files, an image-only flat ZIP, and separate report.
- [ ] Retry failures without redownloading successes.
- [ ] Keep background removal optional; test all mocked success/failure/security cases listed in `PLAN.md`.

## Acceptance

- [ ] Ordinals never shift after blanks/failures.
- [ ] Outputs are 1500×1500 RGB JPEGs without default stretching, cropping, or upscaling.
- [ ] ZIP/report and private-network rejection meet the contract.
- [ ] Tests and Ruff pass offline.

## Verification

```bash
python -m pytest tests/test_image_downloader.py tests/test_image_service.py
ruff check .
```

