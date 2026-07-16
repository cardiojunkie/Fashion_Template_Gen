# User sign-off — 0.1.0-rc1

Codex has not approved any business decision. Update a row only after an authorized user provides the decision and source.

| Decision | Status | Safe behavior while pending |
|---|---|---|
| Final permitted values for every enum | PENDING | Unknown/unmapped values remain blank and review-required |
| `fit` vs `fit_type`; `pattern` vs `pattern_type`; `closure` vs `fastening_type`; `occasion` vs `occasion_type`; `material` vs `fabric`; `package_contents` vs `in_the_box`; `fabric_care` vs `care_instructions` | PENDING | Values are not copied between ambiguous fields |
| Title/name formats and character limits by set | PENDING | Existing conservative title path remains; missing limits are reported |
| Whether six non-empty bullets are mandatory | PENDING | Unsupported bullets stay blank |
| Whether CMS requires `.xlsx`, true `.xls`, or both | PENDING | `.xlsx` only; `.xls` is disabled |
| White padding versus background removal | PENDING | Transparency composites to white; no full background-removal claim |
| Job/image/result retention period | PENDING | Durable automatic deletion is disabled |
| Production host | PENDING | No production deployment or container is selected |
| Authentication method | PENDING | Public production exposure is blocked |
| Auto-accept and evaluation thresholds | PENDING | All evaluated fields require review |
| Approved model(s) | PENDING | Live model-comparison gate remains `NOT_RUN` |
| Approved model pricing | PENDING | Requests/usage remain visible; cost is unavailable |

Required acceptance evidence: approver, date, exact values/rules, effective version, source document, affected attribute sets/profiles, and rollback instruction.
