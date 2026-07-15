# Repository Instructions

Build a secure, auditable Streamlit dashboard that turns fashion-product data and SKU-linked images into exact CMS upload workbooks. Follow `PLAN.md` and the global contract in `docs/PRODUCT_CONTRACT.md`; work on one phase at a time and do not scaffold later phases.

## Start every phase

1. Read `docs/STATUS.md`.
2. Read only the active file under `docs/phases/` plus `docs/PRODUCT_CONTRACT.md`.
3. Inspect the current implementation before editing.
4. Confirm prior acceptance criteria still pass.

## Commands

```bash
python -m pip install -e ".[dev]"
streamlit run app.py
python -m pytest
ruff check .
```

Validate the registry directly with:

```bash
python -m fashion_cms.registry config/attribute_registry.xlsx
```

## Non-negotiable rules

- Preserve exact CMS header spelling, membership, order, and identifier text.
- Never invent permitted values or product facts. Unsupported values remain blank and reviewable.
- Treat spreadsheets, descriptions, URLs, filenames, ZIPs, and images as untrusted input.
- Keep secrets in environment variables; never log or export them.
- Prefer existing code, the standard library, native platform features, and current dependencies.
- Do not remove validation, security, accessibility, data-loss protection, or required tests to reduce code.
- Do not begin the next phase early or add speculative infrastructure.
- Run the active phase's complete verification block before declaring it complete.
- At every phase boundary, replace `docs/STATUS.md` with current status and append to `docs/DECISIONS.md` only for a changed architecture or business rule.

