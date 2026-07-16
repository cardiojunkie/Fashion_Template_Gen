# Known limitations — 0.1.0-rc1

- The evaluation dataset is an engineering fixture, not approved ground truth; no live accuracy, latency, leakage, or price claim exists.
- Official product types, enum sources, copy rules, and character limits remain incomplete for six Phase 7 sets. Unsupported fields stay blank/review-required.
- Thresholds, models, and pricing are pending. Automatic acceptance remains disabled and cost displays unavailable without an approved matching record.
- `.xlsx` is supported; true `.xls` is disabled. White compositing/padding is implemented; full background removal is not claimed.
- Development mode has no authentication. No public production exposure is allowed until a host, authentication, HTTPS, storage, backup, and monitoring plan is approved.
- SQLite and in-process bounded workers suit one application instance. There is no distributed/global multi-process request cap or background queue.
- Cancellation stops new scheduling but cannot retract a provider request already sent. Completed work is preserved and remaining units resume.
- Live extraction and catalog copy are fixed to NVIDIA `thinkingmachines/inkling`; there is no
  alternate endpoint/model, model discovery, local model, browser secret entry, or fallback.
- The application has no user authentication. Keep the app private and manage `NVIDIA_API_KEY`
  only in the server environment or deployment secret manager.
- Uploaded image bytes are not durable in SQLite; a restart-time live extraction retry needs the same validated inputs re-uploaded.
- Application SSRF checks do not replace infrastructure egress controls. Production should deny metadata/private networks at the network layer.
- Durable job/artifact deletion is disabled until retention is approved. Temporary content is kept in memory and failure paths discard it immediately.
- A production container was not added because no container-based host was approved.
