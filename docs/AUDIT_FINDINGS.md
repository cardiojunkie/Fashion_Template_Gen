# Retrospective audit findings

Audit date: 2026-07-16  
Release scope: engineering release candidate `0.1.0-rc1`; no deployment, tag, publication, or
business approval was performed.

## Defects

| ID | Severity | Area | Verified finding | Correction | Regression evidence | Status |
|---|---|---|---|---|---|---|
| AUD-001 | Major | Manual acceptance | The repository had no requested non-developer UAT checklist, structural inputs, real-product truth template, generated header contract, or read-only export verifier. Manual acceptance could not be executed or recorded consistently. | Added `uat/README.md`, 13-sheet checklist, seven structural workbooks plus negative/image/downloader fixtures, truth template, registry-generated expected headers, pack generator, and export verifier with terminal/JSON reports. | `tests/test_uat_export_verifier.py`; generated workbook inspection; all structural workbooks parse ready. | FIXED |
| AUD-002 | Major | UAT launcher | `start.sh` defaulted to `0.0.0.0` but honored an unrelated `HOST` environment variable, so a pre-existing environment could violate the explicit UAT bind requirement and make the forwarded app unreachable. | Removed the host override and always pass `--server.address 0.0.0.0`; port overrides remain supported. | Strengthened `test_port_environment_is_used_without_positional_port` with hostile `HOST=127.0.0.1`; startup health check. | FIXED |

No Critical defect was found. No Critical or Major engineering defect remains open after these
corrections.

## Open blockers that are not implementation defects

| ID | Audit status | Area | Evidence | Safe current behavior | Required owner/action |
|---|---|---|---|---|---|
| BLK-001 | BLOCKED_USER_INPUT | Human accuracy truth | Engineering fixtures/manifests are explicitly `PENDING` and contain no approved real images. | No live accuracy or golden-truth claim is made. | Catalog reviewer supplies real products, completes `real_product_ground_truth_template.xlsx`, and approves labels. |
| BLK-002 | BLOCKED_USER_INPUT | Live model comparison | Release gate is `NOT_RUN`; model pricing and thresholds are pending. | Default suite and UAT can run offline with the deterministic fake client. | Approve two model IDs, credentials, pricing, thresholds, then run the same frozen dataset. |
| BLK-003 | BLOCKED_USER_INPUT | Permitted values/product types | Configuration health reports absent approved sources for the six Phase 7 sets. | Unsupported/unmapped values remain blank and review-required. | Supply versioned authorized permitted-value/product-type sources. |
| BLK-004 | BLOCKED_USER_INPUT | Catalog/CMS business rules | Sign-off ledger lists unresolved semantics, title/limits, bullets, `.xls`, and background treatment. | Conservative accepted-fact copy, `.xlsx`, and white padding remain; QC reports gaps. | Authorized user records exact rules, sources, effective version, and rollback. |
| BLK-005 | BLOCKED_USER_INPUT | Retention | Durable automatic cleanup is deliberately disabled. | No unapproved automatic deletion occurs; root-scoped dry-run cleanup exists. | Approve retention period, storage scope, and cleanup schedule. |
| BLK-006 | BLOCKED_USER_INPUT | Production deployment | Host, authentication, HTTPS proxy, persistent storage, backup target, monitoring, and egress policy are unselected. | Codespaces UAT stays private; no deployment or public exposure occurred. | Infrastructure owner approves and validates a production-like environment. |

## Deferred minor observations

None. The audit did not add speculative workers, batch processing, `.xls` support, background
removal, authentication, deployment packaging, or dependencies without an approved need.
