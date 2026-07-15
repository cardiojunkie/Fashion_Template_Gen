# Decision Records

## 2026-07-15 — Phase 2 local file boundaries

- `.xlsx` is the only accepted input and output workbook format. True `.xls` remains deferred until the CMS consumer confirms a need and a tested parser/writer can guarantee no data loss.
- Workbooks and ZIP members are validated and read in memory; ZIP paths are never extracted to the filesystem. Workbook limits are 25 MB uploaded, 100 MB expanded, 2,000 internal members, 100,000 rows, and 500 columns.
- Uploaded image limits are 25 MB and 50 megapixels per image, 100 MB per ZIP, 1,000 members per ZIP, 500 top-level/expanded files, 250 MB uploaded, and 500 MB expanded in total. Validation reports are bounded.
- Ambiguous, unsafe, malformed, unreadable, mislabeled, or over-limit input is critical. Blank optional workbook values, duplicate EANs, missing/orphan images, unsupported extensions, and malformed image names are warnings and may continue.

These defaults satisfy the untrusted-input and data-loss boundaries in the product contract without adding storage, archive, or legacy Excel dependencies.
