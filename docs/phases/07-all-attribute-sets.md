# Phase 7 — Remaining attribute sets and profiles

## Goal

Add Bottomwear, Ethnic Wear, Innerwear & Sleepwear, Footwear, Sports & Activewear, and Men's Accessories sequentially without regressing Topwear.

## Checklist

For each set, in the order above:

- [ ] Validate Appendix A order and approve product types/profiles, canonical values, aliases, policies, and scopes.
- [ ] Extend extraction with only applicable fields and add approved copy rules.
- [ ] Add golden size-only/visually varying fixtures and fake-client end-to-end tests.
- [ ] Run the full prior suite before moving to the next set.

Keep exact technical claims explicit-only unless registry-approved evidence says otherwise, including footwear performance/material dimensions, sports elasticity/water resistance, luggage certification/interiors, watch specifications, eyewear technical properties, and all origin/weight/dimension/care/composition fields.

Men's Accessories needs at least `bags_luggage`, `caps_headwear`, `watches`, `eyewear`, and `belts_wallets_ties_other`; send only profile-relevant fields.

## Acceptance

- [ ] All seven sets export exact headers and have validated approved profiles.
- [ ] Accessory profiles never receive irrelevant fields.
- [ ] Every set has representative golden/end-to-end coverage and Topwear still passes.
- [ ] Full tests and Ruff pass.

## Verification

```bash
python -m pytest
ruff check .
```

