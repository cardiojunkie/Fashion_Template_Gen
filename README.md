# Fashion CMS Upload Generator

A phased Streamlit application for turning fashion-product inputs and SKU-linked images into validated, auditable CMS upload workbooks.

Phase 5 adds a Topwear-only, evidence-aware multimodal extraction MVP. It supports per-SKU
and size-only base-code analysis, strict Structured Outputs, validated provenance, bounded
retry, result caching, and a deterministic fake client for offline use. Live extraction calls
the OpenAI Responses API directly and requires explicit confirmation. It does not yet provide
editable review, catalog copy, or final CMS exports.

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
inputs in CMS Generator when retrying Phase 5 work after a process restart.

Upload an `.xlsx` file containing `sku`, `base_code`, `attributes__lulu_ean`, `attributes__shipping_weight`, and `model_code_input_data`. Store SKU, base code, and EAN cells as text. Name images `SKU-positiveOrdinal.ext`; for example, `ABC-12-2.jpg` belongs to SKU `ABC-12` at ordinal 2.

The Image Downloader page accepts a separate `.xlsx` workbook with text SKU values in
column A and image URLs in columns B onward. URL ordinals come from physical column position,
so a URL in column C is always saved as `SKU-2.jpg`, even when column B is blank or fails.
Only HTTP/HTTPS URLs resolving to public destinations are fetched. Successful images are
EXIF-oriented, fitted without default crop/stretch/upscale, and centered on a white canvas.

## Configure Topwear extraction

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
```

The default suite uses only the fake client. When credentials are intentionally configured, run
the smallest opt-in live integration test separately:

```bash
RUN_LIVE_LLM_TESTS=1 python -m pytest -m live
```

## Maintain the attribute registry

The source of truth is `config/attribute_registry.xlsx`. Edit it with a workbook application while preserving these sheet and column names:

- `Attribute_Sets`: ordered CMS headers for each attribute set.
- `Attribute_Definitions`: one definition for every unique mapped header.
- `Permitted_Values`: canonical enum values beginning at `value_1`.
- `Value_Aliases`: aliases pointing to existing canonical values; inactive rows may hold pending mappings.
- `Product_Profiles`: approved profile-to-header applicability rules; leave rows empty rather than guessing rules that have not been approved.

Do not add guessed CMS values. Add approved canonical values to `Permitted_Values`, change the header's `data_type` to `ENUM` in both `Attribute_Definitions` and `Permitted_Values`, then activate aliases only when their canonical target exists. Men's Accessories must have the profiles required by `docs/PRODUCT_CONTRACT.md` before that attribute set goes live. Validate the saved workbook with:

```bash
python -m fashion_cms.registry config/attribute_registry.xlsx
```

Restart Streamlit after replacing the workbook. The committed `A-Line Fit` → `A-Line` alias is inactive pending an approved `A-Line` canonical value.

Project scope and phase gates live in `PLAN.md`; current progress lives in `docs/STATUS.md`.
