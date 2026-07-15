# Fashion CMS Upload Generator

A phased Streamlit application for turning fashion-product inputs and SKU-linked images into validated, auditable CMS upload workbooks.

Phase 3 provides local workbook/image validation, exact blank CMS workbook export, and an
SSRF-safe image downloader that creates standardized 1500 × 1500 JPEGs, a flat image ZIP,
and a separate report. It intentionally contains no job, LLM, or catalog-generation pipeline.

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
streamlit run app.py
```

Upload an `.xlsx` file containing `sku`, `base_code`, `attributes__lulu_ean`, `attributes__shipping_weight`, and `model_code_input_data`. Store SKU, base code, and EAN cells as text. Name images `SKU-positiveOrdinal.ext`; for example, `ABC-12-2.jpg` belongs to SKU `ABC-12` at ordinal 2.

The Image Downloader page accepts a separate `.xlsx` workbook with text SKU values in
column A and image URLs in columns B onward. URL ordinals come from physical column position,
so a URL in column C is always saved as `SKU-2.jpg`, even when column B is blank or fails.
Only HTTP/HTTPS URLs resolving to public destinations are fetched. Successful images are
EXIF-oriented, fitted without default crop/stretch/upscale, and centered on a white canvas.

## Verify

```bash
python -m pytest
ruff check .
python -m fashion_cms.registry config/attribute_registry.xlsx
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
