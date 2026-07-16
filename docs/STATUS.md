# Project Status

Current phase: Post–Phase 8 — NVIDIA-only extraction with `input_data`
Status: implementation and offline verification complete; rotated-key live UAT pending
Release candidate: 0.1.0-rc1 (unpublished)
Last updated: 2026-07-16

## Completed

- Replaced `model_code_input_data` with the required `input_data` workbook column across parsing,
  models, persistence mapping, prompts, review provenance, cache identity, fixtures, and guides.
  The legacy header is rejected explicitly. `base_code` remains the only variant-grouping key.
- Added the fixed `NvidiaInklingClient` for `thinkingmachines/inkling` at NVIDIA's HTTPS chat
  endpoint with `temperature=1`, `top_p=0.95`, `max_tokens=8192`, `stream=false`, mandatory
  SGLang `response_format`, high-detail images, retries, bounded time/size, redirect rejection,
  response validation, and full-chain secret redaction. Adapter/cache identity is version 2.
- Removed provider configuration from application navigation and all active configurable-provider
  and legacy OpenAI runtime paths. Schema-v6 provider/capability/audit rows remain inert and
  readable. New job snapshots contain fixed NVIDIA endpoint/model fingerprints, never the key.
- Added **Test NVIDIA Connection** above upload/extraction. Its generated 96 × 96 blue-square PNG
  and exact two-field schema test authentication, model access, vision, and structured output in one
  call. The pass is bound to the current Streamlit session and `NVIDIA_API_KEY` fingerprint;
  extraction and catalog copy stay disabled until it passes.
- Kept the physical SQLite `job_rows.model_data` column while mapping it to `input_data` in code.
  Prompt, result, input-row, and cache contracts are version 2. Completed v1 extraction history is
  read-only; unfinished v1 work requires re-upload under the new contract.
- Regenerated the UAT inputs/checklist and updated the product contract, plan, runtime guide,
  deployment/security/user documentation, README, and `.env.example`.

## Verification

- Full offline suite: `396 passed, 1 skipped`; the skip is the explicit opt-in NVIDIA live test.
- `ruff check .`: pass.
- `python -m fashion_cms.registry config/attribute_registry.xlsx`: pass — 7 sets, 78
  definitions, fingerprint `9ed268029dc95dafeb9afd3638b2b059727187e8242dbdcdcfbfe5de34c24811`.
- Focused tests cover the exact five-column contract, legacy-header rejection, formula and
  identifier safety, base-code grouping, independent `input_data` cache invalidation, strict NVIDIA
  payload/response boundaries, the session/key connection gate, one-click job orchestration with
  SKU evidence plus labelled images, and read-only historical extraction behavior.
- The adapter-v1 live diagnostic authenticated but returned non-JSON because its structured-output
  parameter was incompatible with Inkling's SGLang backend. Adapter v2 has not been live-verified.
  Any credential exposed in chat or diagnostic output must be revoked and is not approved for reuse.

## Release blockers

- Revoke the exposed credential, configure a newly rotated `NVIDIA_API_KEY`, pass the opt-in live
  diagnostic, and complete a representative real-product extraction/review/export UAT.
- Human approval of representative ground truth for every set/profile and completion of the
  existing live model-evaluation release gate with approved credentials, pricing, and thresholds.
- Final permitted values, semantic field rules, title/copy rules, output format, background mode,
  and retention approval.
- Approved production host, authentication, HTTPS/reverse proxy, storage/backup, monitoring,
  provider retention terms, and network-egress configuration.

## Next action

Set only a newly rotated key in the server environment, then run
`RUN_LIVE_NVIDIA_TESTS=1 python -m pytest -m live`. In the private dashboard, pass **Test NVIDIA
Connection** and complete the representative-product rows in `uat/manual_uat_checklist.xlsx`.
Do not deploy, publish, tag, or mark production-ready until the release gates and user sign-off
ledger are complete.
