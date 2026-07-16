# Release checklist — 0.1.0-rc1

Engineering checks:

- [x] Full offline tests, Ruff, diff whitespace, dependency consistency, registry validation, and shell syntax.
- [x] Clean temporary installation and executable `./start.sh` smoke launch.
- [x] Health/dashboard, seven sets, five accessory profiles, configuration health, offline extraction, review persistence, partial failure/resume, cancellation, CMS/QC export, downloader, history, and release-gate view.
- [x] Exact headers/order, identifier text, workbook reopen, per-SKU/valid size-only/unsafe grouping, Topwear regression, cache invalidation, backup, and cleanup.
- [x] Workbook, ZIP, filename, formula, image, URL/SSRF, prompt/model-output, secret/logging, and resource-limit boundaries.
- [x] Version, dataset, registry, prompt/schema, threshold, and pricing manifests frozen for `0.1.0-rc1`.

User/release checks:

- [ ] Approve representative golden ground truth for every set/profile.
- [ ] Approve two model IDs, pricing, thresholds, and run the opt-in live comparison.
- [ ] Approve permitted values, semantic field rules, copy/title rules, and output format.
- [ ] Approve retention, production host, authentication, HTTPS/reverse proxy, storage, backup, monitoring, and egress policy.
- [ ] Re-run every gate in the approved production-like environment and replace `BLOCKED_USER_DECISION`/`NOT_RUN` results with evidence-backed `PASS` only.
- [ ] Obtain authorized release approval.

Do not deploy, publish, tag, or create a release while any mandatory gate is not `PASS`.
