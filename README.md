# Fashion CMS Upload Generator

A phased Streamlit application for turning fashion-product inputs and SKU-linked images into validated, auditable CMS upload workbooks.

Release candidate `0.1.0-rc1` supports all seven CMS attribute sets through evidence-aware extraction, canonical
normalization, persisted review, factual text-only catalog copy, exact per-set CMS workbooks, and
separate QC workbooks. Live OpenAI calls remain optional and explicitly confirmed; fake extraction
and copy clients keep the default workflow and tests offline.

Phase 8 adds centralized release gates, deterministic evaluation tooling, configurable resource and
cost limits, bounded model concurrency, persistent call accounting, cancellation/resume, partial
exports, database backup, cleanup safety, and production/user documentation. The candidate is not
approved for production: human golden data, live model comparison, business rules, hosting, and
authentication remain blocked in `docs/releases/0.1.0-rc1/USER_SIGNOFF.md`.

The existing workbook validation, blank CMS export, SSRF-safe 1500 × 1500 image downloader,
persistent jobs, Attribute Registry, and Job History remain available.

## Requirements

- Python 3.12

## Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Run

```bash
./start.sh
./start.sh 8502
PORT=8503 ./start.sh
PYTHON_BIN=python3 ./start.sh
```

The default port is 8501. In GitHub Codespaces, the application appears in the
**PORTS** panel; keep the forwarded port private for normal testing. If automatic
forwarding does not occur, add the selected port manually in the **PORTS** panel.
Press Ctrl+C to stop the application.

Do not expose a port publicly while real product data or API keys are in use.

Jobs are stored in `data/fashion_cms.sqlite3` by default so selections and progress survive
Streamlit reruns and Codespace restarts. The CMS Generator never stores uploaded image bytes in
the job database; it stores validated metadata and SHA-256 hashes. Re-upload the same validated
inputs in CMS Generator when retrying extraction after a process restart.

## Review and export CMS attribute sets

Select an attribute set and confirm its product profile before extraction. Men's Accessories
requires an explicit choice among bags/luggage, caps/headwear, watches, eyewear, and other
accessories; only profile-applicable fields are sent for extraction and all other CMS cells stay
blank. The selected profile persists with the job and participates in cache invalidation.

After extraction, review each proposed attribute in CMS Generator. You can filter by conflict,
unmapped/unknown/invalid values, low confidence, image-derived color, completion state, base code,
SKU, or header. Enum edits are limited to active registry values; accept, edit, blank, and reject
decisions persist across reruns and restarts. Unresolved decisions block catalog copy and export.

Catalog copy uses accepted facts only and sends no images. Code builds the SKU-specific title and
validates keywords and up to six factual bullets. Unsupported bullet cells remain blank. The final
CMS download contains only the exact selected-set headers; the separate QC download contains provenance,
review actions, warnings, and image-color inference notes. An accepted broad image-derived color is
yellow in the CMS workbook; supplied or reviewer-edited colors are not.

The active registry is authoritative. Add approved aliases to `Value_Aliases` for the same header
only, then validate the workbook. A registry change revalidates stored enum decisions rather than
silently changing them.

Upload an `.xlsx` file containing `sku`, `base_code`, `attributes__lulu_ean`, `attributes__shipping_weight`, and `model_code_input_data`. Store SKU, base code, and EAN cells as text. Name images `SKU-positiveOrdinal.ext`; for example, `ABC-12-2.jpg` belongs to SKU `ABC-12` at ordinal 2.

The Image Downloader page accepts a separate `.xlsx` workbook with text SKU values in
column A and image URLs in columns B onward. URL ordinals come from physical column position,
so a URL in column C is always saved as `SKU-2.jpg`, even when column B is blank or fails.
Only HTTP/HTTPS URLs resolving to public destinations are fetched. Successful images are
EXIF-oriented, fitted without default crop/stretch/upscale, and centered on a white canvas.

## Configure extraction and copy

Fake extraction is selected by default and does not require an API key or internet access. For
an explicitly confirmed live request, set these server-side environment variables:

```bash
export OPENAI_API_KEY="your-secret-key"
export OPENAI_MODEL="your-model-id"
export OPENAI_IMAGE_DETAIL="high"
```

`OPENAI_IMAGE_DETAIL` defaults to `high`. The application starts without live credentials and
disables live extraction with a configuration message. It never displays or stores the API key.
Use `.env.example` as a name-only reference; do not put a real secret in Git.

## Verify

```bash
python -m pytest
ruff check .
python -m fashion_cms.registry config/attribute_registry.xlsx
python -m fashion_cms.release_gates docs/releases/0.1.0-rc1/release-gates.json
```

The default suite uses only the fake client. When credentials are intentionally configured, run
the smallest opt-in live integration test separately:

```bash
RUN_LIVE_LLM_TESTS=1 python -m pytest -m live
```

Run the Phase 6 upload-to-export path directly with:

```bash
python -m pytest tests/test_topwear_e2e.py
```

Run all Phase 7 set/profile checks with:

```bash
python -m pytest tests/test_attribute_sets.py tests/test_registry.py
```

## Maintain the attribute registry

The source of truth is `config/attribute_registry.xlsx`. Edit it with a workbook application while preserving these sheet and column names:

- `Attribute_Sets`: ordered CMS headers for each attribute set.
- `Attribute_Definitions`: one definition for every unique mapped header.
- `Permitted_Values`: canonical enum values beginning at `value_1`.
- `Value_Aliases`: aliases pointing to existing canonical values; inactive rows may hold pending mappings.
- `Product_Profiles`: approved profile-to-header applicability rules; leave rows empty rather than guessing rules that have not been approved.

Do not add guessed CMS values. Add approved canonical values to `Permitted_Values`, change the header's `data_type` to `ENUM` in both `Attribute_Definitions` and `Permitted_Values`, then activate aliases only when their canonical target exists. The six Phase 7 sets currently have safe technical routing profiles but no approved CMS product-type mappings or set-specific permitted-value sources; the dashboard reports this configuration-incomplete state. Validate the saved workbook with:

```bash
python -m fashion_cms.registry config/attribute_registry.xlsx
```

Restart Streamlit after replacing the workbook. The active header-scoped alias maps
`A-Line Fit` to the permitted `A-Line` value for `attributes__fit_type` only.

Project scope and phase gates live in `PLAN.md`; current progress lives in `docs/STATUS.md`.
The complete release checklist and operational guides live in `docs/releases/0.1.0-rc1/`.
