# Phase 1 — Repository foundation and attribute registry

## Goal

Create the minimal Python 3.12 project, persistent context, runnable Streamlit shell, exact Appendix A mappings, five-sheet registry, validation/indexing/fingerprint code, and tests. Do not implement workbook inputs, image work, jobs, LLM calls, review, or export.

## Checklist

- [ ] Add only Phase 1 runtime and development dependencies.
- [ ] Create all context/docs files required by `PLAN.md`.
- [ ] Start a Streamlit shell showing product name and phase status.
- [ ] Create all five registry sheets and exact ordered mappings for seven sets.
- [ ] Define each unique header once with valid type, scope, policy, and nullability.
- [ ] Keep unapproved enum slots empty; keep the `A-Line Fit` alias inactive until `A-Line` exists.
- [ ] Load, normalize, index, validate, and fingerprint the registry.
- [ ] Reject duplicate mappings/canonical values, missing definitions, invalid aliases/types/profiles.
- [ ] Document registry maintenance and reload.

## Acceptance

- [ ] A clean Python 3.12 environment installs with the documented command.
- [ ] Streamlit starts without exception.
- [ ] The committed registry validates.
- [ ] Every set exactly matches Appendix A name, membership, and order.
- [ ] Every mapped header has exactly one definition.
- [ ] Invalid duplicate-value and alias fixtures are rejected.
- [ ] Tests and Ruff pass.
- [ ] Status identifies Phase 2 as next.

## Verification

```bash
python -m pytest
ruff check .
streamlit run app.py --server.headless true
```

Stop Streamlit after startup is confirmed.

