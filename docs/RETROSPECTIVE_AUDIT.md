# Retrospective Phase 1–8 audit

Audit date: 2026-07-16  
Scope: `PLAN.md`, `docs/PRODUCT_CONTRACT.md`, the active Phase 8 checklist, registry/configuration,
implementation, tests, release evidence, startup, and the manual-UAT deliverables.  
Evidence rule: a checked plan box was never treated as evidence. `PASS` requires inspected
implementation plus passing verification. Live accuracy, business approval, and deployment choices
remain user-controlled.

Statuses: `PASS`, `FAIL`, `PARTIAL`, `BLOCKED_USER_INPUT`, `NOT_IMPLEMENTED`, and
`NOT_APPLICABLE`.

## Phase 1 — Foundation and registry

| Phase | Requirement ID | Requirement summary | Implementation file/module | Test or verification evidence | Status | Finding | Corrective action | Remaining manual check | Blocker |
|---|---|---|---|---|---|---|---|---|---|
| 1 | P1-01 | Python 3.12 project and dependency declarations | `pyproject.toml` | Clean install; `pip check`; full suite | PASS | Required runtime/dev dependencies are declared; no separate framework or unused runtime dependency found. | None | Repeat clean install on target host. | — |
| 1 | P1-02 | Repository operating documents | `AGENTS.md`, `README.md`, `.env.example`, `docs/*` | File/content inspection | PASS | Required commands, phase boundaries, trust rules, and pending decisions are documented. | None | Catalog user reads UAT guide. | — |
| 1 | P1-03 | Streamlit startup and configuration health | `app.py`, `start.sh` | `tests/test_start_script.py`; live health check | PASS | Five pages load from the validated registry; launcher stays foreground/headless. | Fixed forced bind in AUD-002. | Open all pages during UAT. | — |
| 1 | P1-04 | Five-sheet attribute registry workbook | `config/attribute_registry.xlsx` | Registry CLI; `tests/test_registry.py` | PASS | All required sheets and columns load and validate. | None | User validates any replacement registry copy. | — |
| 1 | P1-05 | Registry loader, normalization, and indexes | `src/fashion_cms/registry.py` | Registry tests and direct CLI | PASS | Exact mappings, definitions, values, aliases, profiles, and normalized indexes are built. | None | Import a user-approved registry during UAT. | — |
| 1 | P1-06 | Registry activation validation | `src/fashion_cms/registry.py` | Duplicate/missing/alias/profile negative tests | PASS | Invalid headers, positions, definitions, canonical values, aliases, data types, and profiles block activation. | None | Try one invalid registry copy. | — |
| 1 | P1-07 | Registry fingerprint/version | `src/fashion_cms/registry.py` | CLI fingerprint; cache tests | PASS | SHA-256 fingerprint is exposed and participates in cache invalidation. | None | Record fingerprint in UAT environment sheet. | — |
| 1 | P1-08 | Seven exact attribute-set mappings and counts | Registry plus `PLAN.md` Appendix A | `test_committed_header_names_membership_and_order_match_plan`; generated `uat/expected_headers.json` | PASS | Counts/order are 45, 43, 44, 43, 46, 46, and 61. | None | Visually confirm Registry page counts. | — |
| 1 | P1-09 | Unique definitions for every mapped header | Registry | `test_committed_registry_has_one_correct_definition_per_header`; CLI reports 78 definitions | PASS | Every mapped header has exactly one definition. | None | None beyond registry import UAT. | — |
| 1 | P1-10 | Evidence policies, data types, scopes, and system/generated fields | Registry definitions | Registry and conservative-evidence tests | PASS | Allowed enums validate; identifiers are system-copy and copy fields are generated-content. | None | User reviews policy semantics before sign-off. | — |
| 1 | P1-11 | Permitted-value sources without invented production enums | Registry/configuration health | Registry inspection and tests | PASS | Only seven approved broad colors and `A-Line` are present; absent sources are reported, not fabricated. | None | Approve remaining official values. | Final permitted values pending. |
| 1 | P1-12 | Header-scoped aliases | Registry and `normalization.py` | Alias/normalization tests | PASS | Alias lookup is normalized and header-scoped; missing targets are rejected. | None | User confirms source approval. | — |
| 1 | P1-13 | `A-Line Fit` maps only to configured `A-Line` | Registry and `normalization.py` | `test_approved_alias_is_header_scoped` | PASS | Mapping exists only for `attributes__fit_type`; no fuzzy route is used. | None | Verify once in review UAT. | — |
| 1 | P1-14 | Fuzzy mappings are suggestions only | `normalization.py`, `review.py` | Fuzzy/ambiguous tests | PASS | Fuzzy output never silently becomes a canonical value. | None | Try an unmapped input. | — |
| 1 | P1-15 | Product profiles and reference integrity | Registry/profile helpers | Profile matrix/isolation tests | PASS | Technical profile matrices are complete and references validate. | None | Confirm every profile in UI. | Approved product-type mappings remain pending for six sets. |
| 1 | P1-16 | Configuration-health reporting | Registry helper and Registry page | `test_committed_technical_profiles_and_configuration_health` | PASS | Topwear is technically ready; missing approvals for six sets are displayed explicitly. | None | Confirm warnings remain visible. | User approval is intentionally outstanding. |

## Phase 2 — Workbook and image input

| Phase | Requirement ID | Requirement summary | Implementation file/module | Test or verification evidence | Status | Finding | Corrective action | Remaining manual check | Blocker |
|---|---|---|---|---|---|---|---|---|---|
| 2 | P2-01 | Attribute-set/profile selector | `app.py` CMS Generator | UI code inspection; all-set workflow tests | PASS | Seven sets and valid profiles route into exact contracts. | None | Select every set/profile. | — |
| 2 | P2-02 | Genuine `.xlsx` parsing and safe `.xls` behavior | `excel_service.py` | malformed/unsupported tests | PASS | Genuine `.xlsx` is parsed; `.xls` is explicitly rejected without extension renaming. | None | Confirm final CMS format. | True `.xls` requirement is BLOCKED_USER_INPUT. |
| 2 | P2-03 | Required-column and `InputRow` validation | `excel_service.py`, `models.py` | `tests/test_excel_service.py` | PASS | Exact required columns, strict identifier types, weights, blanks, row limits, and cell limits validate. | None | Run valid/missing-column workbooks. | — |
| 2 | P2-04 | Formula/error/untrusted workbook handling | `preflight_xlsx`, parser | Formula, external-content, malformed XML tests | PASS | Formulas, errors, macros, external links, encryption, and unsafe packages are rejected. | None | Run benign formula UAT. | — |
| 2 | P2-05 | Duplicate rows/SKUs/EANs | `excel_service.py` | duplicate SKU/EAN tests | PASS | Duplicate SKU blocks; duplicate EAN warns without dropping rows. | None | Upload `duplicate_sku.xlsx`. | — |
| 2 | P2-06 | Missing identifiers and base-code fallback | `InputRow`, parser, variant service | blank-base and invalid-cell tests | PASS | Blank SKU blocks; blank base code warns and uses a private fallback only. | None | Upload `missing_base_code.xlsx`. | — |
| 2 | P2-07 | Image and ZIP upload boundaries | `image_service.py` | upload, archive, count/size tests | PASS | Direct images and safe ZIP members are accepted within configured limits. | None | Upload direct and ZIP images. | — |
| 2 | P2-08 | ZIP path, symlink, hidden, nesting, and collision safety | `image_service.py` | traversal/symlink/nested/collision tests | PASS | Archives are inspected in memory; unsafe members block and hidden OS files are ignored. | None | Run benign path fixture. | — |
| 2 | P2-09 | Image decoding, format, dimensions, EXIF | `image_service.py` | format/decode/pixel/EXIF tests | PASS | Magic, extension, mode, animation, pixels, and orientation validate before processing. | None | Upload malformed fixture. | — |
| 2 | P2-10 | SKU filename matching and ordinals | `image_service.py` | hyphenated SKU test | PASS | Final positive integer suffix is used; `ABC-12-2.jpg` maps to SKU `ABC-12`, ordinal 2. | None | Confirm with supplied PNG fixture. | — |
| 2 | P2-11 | Missing, orphan, duplicate-ordinal reporting | `image_service.py`, `app.py` | warning/duplicate tests | PASS | Exact files and SKUs are reported; duplicate ordinal has no silent winner. | None | Run image association UAT. | — |
| 2 | P2-12 | Critical blocking and actionable warnings | result readiness plus UI | parser/image tests; code inspection | PASS | Critical findings disable processing; warnings remain visible and may continue. | None | Observe both paths in browser. | — |
| 2 | P2-13 | Blank CMS skeleton, exact headers, no internals | `build_blank_cms_workbook` | exact export and reopen tests | PASS | One row per valid input and exact selected headers only. | None | Download and run verifier. | — |
| 2 | P2-14 | Text identifier preservation | parser/export | leading-zero/formula-safe export tests | PASS | Leading-zero SKU/EAN and base code remain text; dangerous text is neutralized. | None | Reopen in catalog user's spreadsheet app. | — |
| 2 | P2-15 | Validation is local: no network/LLM | parser/image modules | dependency/call-path inspection | PASS | Workbook/image validation has no client or network call. | None | Keep Fake selected and monitor UI. | — |

## Phase 3 — Image downloader

| Phase | Requirement ID | Requirement summary | Implementation file/module | Test or verification evidence | Status | Finding | Corrective action | Remaining manual check | Blocker |
|---|---|---|---|---|---|---|---|---|---|
| 3 | P3-01 | Column A SKU and URLs from B onward | `parse_url_workbook` | URL workbook tests | PASS | SKU is strict text and later physical columns produce requests. | None | Fill downloader UAT workbook. | — |
| 3 | P3-02 | Physical URL ordinal and blank skipping | parser | blank/failure ordinal tests | PASS | Blank URL 1 does not renumber URL 2. | None | Run fail-then-success UAT. | — |
| 3 | P3-03 | HTTP/HTTPS syntax and credential rejection | downloader URL validator | scheme/credential tests | PASS | Only credential-free HTTP(S) destinations proceed. | None | Try benign invalid URL. | — |
| 3 | P3-04 | Redirect and SSRF/DNS/IP protection | downloader | redirect, private DNS, IP pinning, peer tests | PASS | Every hop resolves to public addresses and connects to a validated peer. | None | Run loopback negative UAT. | — |
| 3 | P3-05 | Timeouts, retry policy, backoff, and deadlines | `DownloadSettings`, fetch loop | timeout/status/backoff/deadline tests | PASS | Temporary failures retry within bounded attempts/deadline; permanent failures do not loop. | None | Observe controlled failure report. | — |
| 3 | P3-06 | Response and retained-output limits | downloader/config | declared/streamed size and limits tests | PASS | Declared and streamed bytes are bounded before retention. | None | Near-limit local UAT. | — |
| 3 | P3-07 | Pixel/decode/content validation | downloader/image service | pixels, HTML, malformed tests | PASS | HTML, malformed bodies, over-pixel images, unsupported animation/modes are rejected. | None | Run HTML URL and malformed image. | — |
| 3 | P3-08 | EXIF orientation | standardizer | orientation tests | PASS | Orientation is applied before sizing/reporting. | None | Inspect an oriented real sample. | — |
| 3 | P3-09 | Transparency and CMYK conversion | standardizer | PNG/CMYK tests | PASS | Alpha composites to white and CMYK becomes RGB. | None | Inspect transparent PNG output. | — |
| 3 | P3-10 | White canvas and exact output | `standardize_pad_white` | dimension/RGB/canvas tests | PASS | Output is exact 1500×1500 RGB JPEG on white. | None | Inspect output properties. | — |
| 3 | P3-11 | Preserve aspect ratio; no crop/stretch/upscale | standardizer | aspect/no-upscale tests | PASS | Source fits within content box and is centered without default upscale. | None | Compare a rectangular source. | — |
| 3 | P3-12 | Exact `sku-ordinal.jpg` naming | downloader | success and failure ordinal tests | PASS | URL 1 failure plus URL 2 success remains `sku-2.jpg`. | None | Run mandatory ordinal scenario. | — |
| 3 | P3-13 | Flat deterministic successful-only ZIP | `build_image_zip` | flat/sorted/deterministic ZIP test | PASS | Report and failures never enter the image ZIP. | None | Inspect ZIP manually. | — |
| 3 | P3-14 | Separate readable failure report | `build_download_report` | exact fields/formula-safety tests | PASS | SKU, ordinal, URL, status, dimensions, filename, and safe error are separate. | None | Open report. | — |
| 3 | P3-15 | Retry failed only | downloader previous-result reuse | retry reuse test | PASS | Previous successes are keyed by SKU, ordinal, URL and not fetched again. | None | Retry one corrected failure. | — |
| 3 | P3-16 | Bounded total/per-host concurrency | downloader | concurrency test | PASS | Defaults 8 total and 4 per host; environment overrides validate. | None | None unless target host tuning is approved. | — |

## Phase 4 — Grouping, jobs, and cache

| Phase | Requirement ID | Requirement summary | Implementation file/module | Test or verification evidence | Status | Finding | Corrective action | Remaining manual check | Blocker |
|---|---|---|---|---|---|---|---|---|---|
| 4 | P4-01 | SQLite schema and versioned migrations | `database.py` | migration/restart/newer-schema tests | PASS | Transactional schema v5 validates and rejects unsupported newer DBs. | None | Restart app during UAT. | — |
| 4 | P4-02 | Persist jobs, items, assets, modes, errors, artifacts | database/job service | database/jobs tests | PASS | Required normalized state and artifact references persist; image bytes/secrets do not. | None | Inspect Job History. | — |
| 4 | P4-03 | Group modes and mixed-mode upload | `variant_service.py`, UI | mixed group tests | PASS | `PER_SKU` default and `BASE_CODE_SIZE_ONLY` are stored per group; mixed modes work. | None | Run mixed-mode UAT. | — |
| 4 | P4-04 | Size-only requires explicit user action | variant service/UI | default/suggestion test | PASS | Detection only suggests; it never silently selects size-only. | None | Confirm checkbox/select behavior. | — |
| 4 | P4-05 | Difference warnings and unsafe grouping | signal extraction/grouping | color/pattern/product/pack/model tests | PASS | Known supplied differences warn; explicit input is never shared over. | None | Mandatory real black/solid vs blue/striped UAT. | — |
| 4 | P4-06 | Deterministic editable representative | selection rules/UI | representative tests | PASS | User selection wins, then valid-image count, then workbook order. | None | Change and persist representative. | — |
| 4 | P4-07 | Exact planned request count | request plan/database | mixed/count and stored-plan tests | PASS | One work item per PER_SKU SKU or confirmed size-only group. | None | Compare UI count before run. | — |
| 4 | P4-08 | State transitions and restart persistence | database/jobs | transition/restart tests | PASS | Invalid edits roll back; valid state and selections survive restart. | None | Stop/restart during job UAT. | — |
| 4 | P4-09 | Partial failure isolation | jobs/database | partial failure tests | PASS | One failure preserves successful work and job becomes partial failure. | None | Simulate controlled fake failure. | — |
| 4 | P4-10 | Retry/resume avoids successful calls | jobs | retry/resume tests | PASS | Only failed/interrupted units are rescheduled. | None | Retry and compare attempted calls. | — |
| 4 | P4-11 | Cache key covers images/mode/registry | variant/extraction cache | exhaustive cache tests | PASS | Image hashes, mode, representative, registry and identifiers invalidate as required. | None | Change one image and observe miss. | — |
| 4 | P4-12 | Cache key covers prompt/schema/model/profile/detail | cache context | exhaustive cache tests | PASS | Every configured request-contract component participates in identity. | None | Change one safe configuration in test environment. | — |
| 4 | P4-13 | Cache result semantic revalidation | extraction cache | corrupt/stale cache tests | PASS | Stale or policy-invalid cached results are deleted/recomputed, never accepted. | None | None beyond regression suite. | — |
| 4 | P4-14 | Job History retry/resume/artifacts | `app.py` Job History | UI inspection plus job tests | PASS | History exposes status, selections, attempts, failures, cancellation, artifacts, and safe controls. | None | Complete recovery UAT. | — |
| 4 | P4-15 | Variant safety: black/solid vs blue/striped | grouping, extraction, review, copy | explicit color/profile conflict and leakage tests | PASS | Engineering fixtures show no silent cross-SKU color/pattern/design copy in PER_SKU. | None | Mandatory real-product UAT remains. | — |

## Phase 5 — Vision extraction

| Phase | Requirement ID | Requirement summary | Implementation file/module | Test or verification evidence | Status | Finding | Corrective action | Remaining manual check | Blocker |
|---|---|---|---|---|---|---|---|---|---|
| 5 | P5-01 | Replaceable LLM client, OpenAI implementation, fake client | `llm_service.py`, extraction module | LLM client/fake tests | PASS | Protocol plus direct Responses API client and deterministic fake exist. | None | Run approved live smoke only when enabled. | — |
| 5 | P5-02 | Environment validation without secret exposure | `LLMSettings`, error sanitizer | settings/secret tests | PASS | Missing/invalid config disables live calls; key is never rendered or persisted. | None | Confirm disabled message with no key. | — |
| 5 | P5-03 | Versioned prompts and strict schemas | extraction constants/builders | schema/request tests | PASS | Topwear and generic contracts are versioned, nullable, strict, and fingerprinted in jobs/cache. | None | Record versions in live evaluation. | — |
| 5 | P5-04 | Applicable profiles, headers, and permitted enums only | extraction contract | applicable payload tests | PASS | Request excludes system/generated and profile-inapplicable fields; enums are canonical registry values only. | None | Inspect each profile preview. | — |
| 5 | P5-05 | Explicit SKU/image labels | request builder | exact label/association tests | PASS | Every selected image is preceded by SKU and physical ordinal; unrelated images are excluded. | None | Inspect a test request with safe fake data. | — |
| 5 | P5-06 | Delimited untrusted product data and injection instruction | prompt/request builder | single-document/injection tests | PASS | Product data is a delimited JSON data document; prompt rejects embedded instructions. | None | Run benign injection-text UAT. | — |
| 5 | P5-07 | PER_SKU and size-only request construction | extraction planner | plan/request tests | PASS | PER_SKU isolates rows/images; confirmed size-only sends one representative request with provenance. | None | Run both modes with real products. | — |
| 5 | P5-08 | Structured response and unknown-field/SKU validation | response validator | malformed/unknown/duplicate tests | PASS | Extra fields, wrong set/SKU/image, invalid status/evidence, and contradictory duplicates reject. | None | None beyond regression suite. | — |
| 5 | P5-09 | Invalid-enum and evidence-policy handling | response normalization | enum/evidence/conservative tests | PASS | Invalid enum blocks; policy-violating claims become unknown/review-required. | None | Compare live proposals to ground truth. | — |
| 5 | P5-10 | Unsupported exact material/care/fit/performance/origin/weight/dimensions | evidence validator | conservative-field tests across sets | PASS | Appearance alone cannot establish these claims; output remains blank/review-required. | None | Real-product negative review. | — |
| 5 | P5-11 | Usage/request/version/error audit storage | extraction record/database | metadata/restart/secret tests | PASS | Sanitized request ID, actual model, usage, retries, versions, status, and error persist. | None | Inspect Job History after a run. | — |
| 5 | P5-12 | Retry classification | LLM call wrapper | temporary/permanent retry tests | PASS | Only temporary/rate-limit failures retry; validation/refusal/incomplete failures do not repeat unchanged. | None | Controlled fake failure only. | — |
| 5 | P5-13 | Progress, bounded execution, cancellation-safe updates | jobs/extraction/UI | concurrency/cancellation tests | PASS | Unit results commit independently; cancellation stops new scheduling and preserves completed work. | None | Cancellation UAT. | — |
| 5 | P5-14 | Cache integration | extraction cache | hit/miss/semantic cache tests | PASS | Valid unchanged result avoids a call; invalid/stale result never enters accepted cache. | None | Observe cache count in UI. | — |
| 5 | P5-15 | Offline suite does not require API key | fake clients/test marker | full suite: one live test skipped | PASS | Default execution is deterministic and paid-call-free. | None | Keep live option unselected for structural UAT. | — |
| 5 | P5-16 | Topwear golden cases are human-approved accuracy truth | `tests/fixtures/topwear_golden.json` | Fixture structure tests only | BLOCKED_USER_INPUT | Engineering expected outcomes exist, but no authorized reviewer approval proves real vision accuracy. | Use UAT ground-truth template and obtain approval. | Test at least ten genuine Topwear base codes. | Human-approved products/images/labels are required. |

## Phase 6 — Normalization, review, copy, and export

| Phase | Requirement ID | Requirement summary | Implementation file/module | Test or verification evidence | Status | Finding | Corrective action | Remaining manual check | Blocker |
|---|---|---|---|---|---|---|---|---|---|
| 6 | P6-01 | Deterministic seven-level merge priority | `review.py` | source-priority/conflict tests | PASS | Reviewer decision, structured input, explicit text, label, visual, rule, blank order is encoded. | None | Create one visible source conflict. | — |
| 6 | P6-02 | Canonical/normalized/alias/fuzzy/unmapped normalization | `normalization.py` | normalization suite | PASS | Exact and approved alias can resolve; fuzzy remains review-only; unmapped remains blank. | None | Run A-Line and unmapped cases. | — |
| 6 | P6-03 | Review filters and evidence/conflict visibility | `app.py`, review models | review tests plus UI inspection | PASS | Conflict, unmapped, invalid, insufficient, low-confidence, image-color, SKU/header filters exist. | None | Exercise filters in browser. | — |
| 6 | P6-04 | Enum dropdown and accept/edit/reject/blank actions | review UI/service | all-action and enum tests | PASS | Actions validate and persist; invalid enum edits are rejected. | None | Exercise all four actions. | — |
| 6 | P6-05 | Review persistence and registry revalidation | database/review | restart/change tests | PASS | Decision provenance survives restart; registry changes flag invalid decisions without rewriting them. | None | Restart after decisions. | — |
| 6 | P6-06 | Explicit input color beats vision | extraction/review | color conflict tests | PASS | Explicit canonical color remains final and conflict is visible. | None | Real conflict UAT. | — |
| 6 | P6-07 | Missing color uses only approved broad vision value | extraction/review | broad/specific color tests | PASS | Only seven configured broad values may populate; nuanced shade without alias stays unknown. | None | Review live/fake image color. | — |
| 6 | P6-08 | Vision-derived color remains review-visible | review/export | bulk-accept/highlight tests | PASS | It cannot bulk-auto-accept; unchanged accepted image color receives QC/highlight provenance. | None | Inspect CMS and QC. | — |
| 6 | P6-09 | Malformed size is not silently normalized | variant/review validators | malformed/strict normalization inspection | PASS | No free-form size parser converts `XXL–L` to a supported value. | None | Run supplied malformed-size row. | — |
| 6 | P6-10 | Size-only sharing eligibility and row identity | review/catalog/export | size-only conflict and all-set tests | PASS | Only safe eligible observations/copy may share; SKU, EAN, size, base code, weight, model remain row-specific. | None | Genuine size-only UAT. | — |
| 6 | P6-11 | Catalog request uses accepted facts and no images | `catalog_service.py` | request/facts tests | PASS | Copy is a separate text-only call over accepted normalized facts. | None | Inspect live provider log metadata, not secret/content. | — |
| 6 | P6-12 | Title/name rules and missing components | deterministic title builder | combination/duplicate-model tests | PASS | Topwear path omits missing pieces and excludes SKU/EAN/base/year. | None | User reviews real titles. | Final per-set format/limits pending. |
| 6 | P6-13 | Factual bullets and blanks | catalog validation | unsupported/repetition/dedup tests | PASS | Unsupported or repeated claims reject; fewer than six supported bullets stay blank. | None | Review sparse-fact output. | Bullet business rule pending. |
| 6 | P6-14 | Useful, non-stuffed keywords | catalog validation | request/output validation tests | PASS | Keywords are built/validated from accepted facts with forbidden placeholders rejected. | None | User checks customer usefulness. | — |
| 6 | P6-15 | Exact Topwear export, QC separation, reopen | catalog/export service | end-to-end and workbook reopen tests | PASS | Exactly 45 CMS headers, one row per SKU, no internals; QC is separate. | None | Run UAT verifier on downloaded file. | — |
| 6 | P6-16 | Approved copy templates/golden examples | fixed conservative implementation and fixtures | engineering tests only | PARTIAL | Safe Topwear rules work, but authorized approval of per-set title/copy/limits is absent. | Keep conservative path; record approved rules before production. | Complete catalog-copy sign-off. | User business decision required. |

## Phase 7 — Remaining attribute sets

| Phase | Requirement ID | Requirement summary | Implementation file/module | Test or verification evidence | Status | Finding | Corrective action | Remaining manual check | Blocker |
|---|---|---|---|---|---|---|---|---|---|
| 7 | P7-01 | Bottomwear fake-client end-to-end workflow | generic extraction/review/catalog/export | parametrized all-set test | PASS | Profile selection, extraction schema, review, copy, exact 43-header export and reopen pass. | None | Run catalog-user export UAT. | — |
| 7 | P7-02 | Ethnic Wear fake-client end-to-end workflow | same | parametrized all-set test | PASS | Exact 44-header offline workflow passes. | None | Run catalog-user export UAT. | — |
| 7 | P7-03 | Innerwear & Sleepwear fake-client end-to-end workflow | same | parametrized all-set test | PASS | Exact 43-header offline workflow passes. | None | Run catalog-user export UAT. | — |
| 7 | P7-04 | Footwear fake-client end-to-end workflow | same | parametrized all-set and technical-claim tests | PASS | Exact 46-header workflow passes; technical claims remain conservative. | None | Run catalog-user export UAT. | — |
| 7 | P7-05 | Sports & Activewear fake-client end-to-end workflow | same | parametrized all-set and technical-claim tests | PASS | Exact 46-header workflow passes; technical claims remain conservative. | None | Run catalog-user export UAT. | — |
| 7 | P7-06 | Men's Accessories fake-client end-to-end workflow | same | parametrized all-set/profile tests | PASS | Exact 61-header workflow passes with explicit profile. | None | Run every accessory profile UAT. | — |
| 7 | P7-07 | Exact headers/order for all seven sets | registry/export | registry and all-set export tests | PASS | Every contract matches Appendix A and generated expected headers. | None | Run verifier on seven downloads. | — |
| 7 | P7-08 | Profile selection and applicable schema | registry/extraction/UI | applicability/payload tests | PASS | Only profile-applicable technical fields enter schemas and requests. | None | Inspect field preview. | — |
| 7 | P7-09 | Approved permitted-value sources for six added sets | registry configuration health | configuration-health tests | BLOCKED_USER_INPUT | Sources are explicitly absent; unsupported values stay blank. | Do not invent values; import authorized sources when supplied. | Obtain source/version approval. | User/vendor source required. |
| 7 | P7-10 | Per-set title/name/bullet/keyword rules | generic conservative copy path | all-set engineering tests | PARTIAL | Safe accepted-fact generation works, but set-specific rules/golden approval are absent. | Retain conservative behavior and QC warnings. | Approve outputs per set. | User business rules required. |
| 7 | P7-11 | Review, CMS export, QC report, workbook reopen | review/catalog service | parametrized all-set workflow | PASS | Each set produces separate exact CMS and QC workbooks. | None | Manual spreadsheet reopen. | — |
| 7 | P7-12 | PER_SKU, valid size-only, and unsafe grouping | variant/review/catalog | phase7 fixture and all-set tests | PASS | Both modes run; explicit visual/profile differences do not silently share. | None | Genuine real-product mode UAT. | — |
| 7 | P7-13 | Golden fixtures represent all sets/modes | phase7/phase8 engineering datasets | fixture coverage tests | PASS | Engineering fixtures cover every set and varying/size-only cases; explicitly not human truth. | None | Human ground-truth approval remains Phase 8 work. | — |
| 7 | P7-14 | Bags/luggage profile isolation | registry/extraction/export | accessory profile isolation tests | PASS | Watch/eyewear-only fields are excluded and blank. | None | Real bag/luggage UAT. | — |
| 7 | P7-15 | Caps/headwear profile isolation | registry/extraction/export | accessory profile isolation tests | PASS | Bag/watch/eyewear technical fields are excluded and blank. | None | Real cap/headwear UAT. | — |
| 7 | P7-16 | Watches profile isolation | registry/extraction/export | accessory profile isolation tests | PASS | Bag/eyewear fields are excluded and blank. | None | Real watch UAT. | — |
| 7 | P7-17 | Eyewear profile isolation | registry/extraction/export | accessory profile isolation tests | PASS | Bag/watch fields are excluded and blank. | None | Real eyewear UAT. | — |
| 7 | P7-18 | Belts/wallets/ties/other isolation | registry/extraction/export | accessory profile isolation tests | PASS | Unrelated specialist fields are excluded and blank. | None | Real relevant-product UAT. | — |
| 7 | P7-19 | Irrelevant accessory fields remain blank with 61 headers | export applicability validation | all-set/export and new UAT verifier tests | PASS | Full CMS contract remains while profile-inapplicable values are blocked. | None | Run verifier with `--profile`. | — |
| 7 | P7-20 | Conservative technical fields | registry evidence policies and response validator | visual technical-claim tests | PASS | Arch/heel/grip/water, elasticity, TSA/compartments/laptop, watch sizes/movement, polarization/lens type require explicit evidence. | None | Compare real output with approved evidence. | — |

## Phase 8 — Hardening and release readiness

| Phase | Requirement ID | Requirement summary | Implementation file/module | Test or verification evidence | Status | Finding | Corrective action | Remaining manual check | Blocker |
|---|---|---|---|---|---|---|---|---|---|
| 8 | P8-01 | Evaluation framework and dataset distinction | `evaluation.py`, engineering dataset/manifest | Phase 8 evaluation tests | PASS | Dataset kind and approval status prevent an engineering fixture being called golden truth. | None | Approve real dataset separately. | — |
| 8 | P8-02 | Metric calculations and dimensions | evaluation module | deterministic positive/negative metric tests | PASS | Precision, supported coverage, blanks, conflicts, invalid enums, claims, leakage, failures, latency, requests, usage/cost aggregate with sample counts. | None | Run on approved live predictions. | — |
| 8 | P8-03 | Deterministic two-model comparison runner | evaluation module | distinct-model/comparison tests | PASS | Runner requires distinct model IDs and compares frozen cases deterministically. | None | Configure two approved live models. | — |
| 8 | P8-04 | Live two-model accuracy/cost comparison | release gates/evaluation report | Gate `model_comparison=NOT_RUN` | BLOCKED_USER_INPUT | No paid call was made and no live accuracy claim exists. | Run only after approvals/credentials/ground truth. | Execute documented live comparison. | Approved models, credentials, pricing, thresholds, truth required. |
| 8 | P8-05 | Request, usage, and cost visibility | jobs/config/UI | usage/cost tests | PASS | Planned/actual attempts and usage persist; unavailable unapproved pricing is displayed honestly. | None | Confirm with approved pricing. | — |
| 8 | P8-06 | Release gates prevent premature readiness | `release_gates.py`, dashboard, JSON | release-gate tests | PASS | Any mandatory blocked/not-run gate prevents production-ready verdict. | None | User reviews Release Readiness. | — |
| 8 | P8-07 | Workbook security | parser/preflight/export | workbook hardening tests | PASS | Package, formula, external, limit, cell, and output safety boundaries pass. | None | Benign negative UAT. | — |
| 8 | P8-08 | ZIP and filename security | image service | archive hardening tests | PASS | Traversal, symlink, nesting, collision, hidden, unsafe flat names, and limits are covered. | None | Benign archive UAT. | — |
| 8 | P8-09 | Image security | image service/downloader | image hardening tests | PASS | Format, mode, animation, decompression, pixels, dimensions, EXIF, transparency, and malformed content are bounded. | None | Malformed/near-limit UAT. | — |
| 8 | P8-10 | URL/SSRF security | downloader | network boundary tests | PASS | Public-only DNS/IP validation, redirect revalidation, pinning, peer checks, proxies off, and deadlines pass offline. | None | Loopback UAT. | — |
| 8 | P8-11 | Prompt-injection and model-output boundary | prompt/schema/validator | injection/strict schema tests | PASS | Untrusted data cannot change instruction hierarchy; malformed/unsupported output is rejected. | None | Benign untrusted-text UAT. | — |
| 8 | P8-12 | Secrets and logging redaction | settings/sanitizers/jobs | secret/error/SQLite tests | PASS | Credentials, authorization, URL userinfo, raw data, and named secret values are redacted/absent. | None | Deployment log review. | Host log controls remain external. |
| 8 | P8-13 | Configurable validated resource limits | `config.py`, env example, consumers | boundary tests | PASS | Workbook/image/request/concurrency/call/cost settings have validation and absolute ceilings. | None | Approve production values. | — |
| 8 | P8-14 | Bounded model concurrency and call circuit | jobs/extraction | concurrency/circuit tests | PASS | In-process workers and persistent per-job call limit bound provider attempts. | None | Load test only on approved host. | Multi-process global cap would require host design. |
| 8 | P8-15 | Retry, partial failure, resume, cancellation | jobs/database/UI | recovery tests | PASS | Classifications and durable per-unit updates preserve completed results and resume only unfinished work. | None | Job Recovery UAT. | — |
| 8 | P8-16 | Artifact and temporary cleanup safety | cleanup module | cleanup tests | PASS | Root-scoped dry run refuses symlinks/active descendants and is idempotent. | None | Operator reviews approved root. | — |
| 8 | P8-17 | Automatic durable retention cleanup | cleanup/release docs | `durable_cleanup_disabled` test | BLOCKED_USER_INPUT | Automatic deletion is intentionally disabled to prevent unapproved data loss. | Enable only after retention approval. | Approve retention period and schedule. | User policy required. |
| 8 | P8-18 | Backup, migration, upgrade, rollback | database and release docs | backup/migration tests; doc inspection | PASS | Atomic non-overwriting SQLite backup, integrity check, transactional migration, restore and rollback procedures exist. | None | Operator rehearsal on approved storage. | — |
| 8 | P8-19 | Deployment documentation | release deployment/environment docs | doc inspection | PASS | Linux/Codespaces health, secrets, storage, reverse proxy, backup, monitoring, upgrade requirements are explicit. | None | Validate on selected host. | — |
| 8 | P8-20 | Approved deployment/hosting/authentication | release gate | Gate `deployment_configuration=BLOCKED_USER_DECISION` | BLOCKED_USER_INPUT | No host, auth, HTTPS, storage, monitoring, or egress choice was invented. | User selects and approves production architecture. | Production-like checklist. | User/infrastructure decision required. |
| 8 | P8-21 | User documentation and sign-off tracking | release guides and `uat/README.md` | doc/artifact inspection | PASS | User workflows, limitations, checklist, UAT records, and explicit decision ledger exist. | None | Catalog user executes UAT. | — |
| 8 | P8-22 | Version manifests and frozen engineering evidence | release JSON manifests | artifact integrity tests | PASS | Registry, prompts, schemas, engineering dataset, thresholds/pricing status, and implementation hashes are recorded. | None | Regenerate only for a new candidate. | — |
| 8 | P8-23 | Manual UAT checklist and structural inputs | `uat/` and generator | generated artifact inspection; parser checks | PASS | Thirteen-sheet checklist, seven structural inputs, negative fixtures, downloader workbook, and real-truth template exist. | Added in AUD-001. | User fills Actual Result/evidence/status. | — |
| 8 | P8-24 | Expected header contract generated from registry | `uat/expected_headers.json`, generator | fingerprint/count inspection | PASS | JSON is generated, not manually duplicated, and contains exact ordered headers/counts/display names. | Added in AUD-001. | Compare after any approved registry replacement. | — |
| 8 | P8-25 | Read-only CMS export verifier and reports | `uat/scripts/verify_exports.py` | `tests/test_uat_export_verifier.py` | PASS | It validates opening, exact headers, rows/SKUs, text identifiers, enums, formula safety, and profile blanks; prints text and JSON without mutation. | Added in AUD-001. | Run against every downloaded CMS workbook. | — |
| 8 | P8-26 | Final business-rule and release approval | `USER_SIGNOFF.md`, release gates | gate report inspection | BLOCKED_USER_INPUT | Permitted values, semantics, copy, formats, background, models/pricing/thresholds and production controls remain pending. | Authorized user records exact decisions and evidence. | Complete sign-off after UAT. | User approval required. |

## Audit totals and verdict basis

| Phase | PASS | PARTIAL | BLOCKED_USER_INPUT | FAIL | NOT_IMPLEMENTED | NOT_APPLICABLE |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 16 | 0 | 0 | 0 | 0 | 0 |
| 2 | 15 | 0 | 0 | 0 | 0 | 0 |
| 3 | 16 | 0 | 0 | 0 | 0 | 0 |
| 4 | 15 | 0 | 0 | 0 | 0 | 0 |
| 5 | 15 | 0 | 1 | 0 | 0 | 0 |
| 6 | 15 | 1 | 0 | 0 | 0 | 0 |
| 7 | 18 | 1 | 1 | 0 | 0 | 0 |
| 8 | 22 | 0 | 4 | 0 | 0 | 0 |
| **Total** | **132** | **2** | **6** | **0** | **0** | **0** |

Engineering verification and startup pass, and the manual UAT pack is usable. The audit verdict is
`READY_FOR_MANUAL_UAT`, not production-ready. Live accuracy, business rules, retention, and
deployment can be marked only by authorized user evidence after manual UAT.
