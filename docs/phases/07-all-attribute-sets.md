# Phase 7 — Remaining attribute sets and profiles

## Goal

Add Bottomwear, Ethnic Wear, Innerwear & Sleepwear, Footwear, Sports & Activewear, and Men's Accessories sequentially without regressing Topwear.

## Checklist

For each set, in the order above:

- [x] Validate Appendix A order and add safe technical profiles, policies, and scopes.
- [x] Extend extraction/review/export with only profile-applicable fields.
- [x] Add golden size-only/visually varying fixtures and fake-client end-to-end tests.
- [x] Run registry/header and Topwear regressions in sequence.

Still configuration-dependent for every new set:

- [ ] Approve CMS product types and their profile mappings.
- [ ] Supply approved set-specific permitted values and aliases.
- [ ] Approve product-type-specific copy templates and character limits.

Keep exact technical claims explicit-only unless registry-approved evidence says otherwise, including footwear performance/material dimensions, sports elasticity/water resistance, luggage certification/interiors, watch specifications, eyewear technical properties, and all origin/weight/dimension/care/composition fields.

Men's Accessories needs at least `bags_luggage`, `caps_headwear`, `watches`, `eyewear`, and `belts_wallets_ties_other`; send only profile-relevant fields.

## Acceptance

- [x] All seven sets export exact headers and have validated technical profiles.
- [x] Accessory profiles never receive irrelevant fields.
- [x] Every set has representative golden/end-to-end coverage and Topwear still passes.
- [x] Full tests and Ruff pass.

## Verification

```bash
python -m pytest
ruff check .
```
