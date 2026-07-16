# Manual UAT pack

This pack is for a catalog user to test release candidate `0.1.0-rc1`. It does not approve or
deploy production. Record every result in `manual_uat_checklist.xlsx`; use real user-supplied
products and `real_product_ground_truth_template.xlsx` for accuracy checks.

The structural workbooks contain identifiers and explicit test labels only. Rows beginning
`REPLACE-` must be replaced with genuine product SKUs, facts, and images before size-only,
visual-variant, or live-vision accuracy testing. They are not fashion ground truth.

## 1. Start the application privately

1. Open a terminal at the repository root.
2. Run:

   ```bash
   chmod +x ./start.sh
   ./start.sh 8501
   ```

3. Keep that terminal running for the whole UAT session.
4. Open the **Ports** tab in Codespaces.
5. Find port `8501` and confirm its visibility is **Private**. Do not make it public.
6. Select **Open in Browser** for port `8501`.
7. Confirm the **CMS Generator** page loads without an error.
8. Open **Attribute Registry** and confirm the configuration-health table loads.
9. Record the environment in the **Environment** sheet of `manual_uat_checklist.xlsx`.

To stop the application after UAT, return to the terminal and press **Ctrl+C**.

## 2. Check the registry

1. Open **Attribute Registry** in the left navigation.
2. Confirm these seven sets and header counts appear:

   - Topwear — 45
   - Bottomwear — 43
   - Ethnic Wear — 44
   - Innerwear & Sleepwear — 43
   - Footwear — 46
   - Sports & Activewear — 46
   - Men's Accessories — 61

3. Select each set and profile. Confirm applicable extraction fields change with the profile.
4. Confirm the six post-Topwear sets clearly report missing approved product types and
   set-specific permitted-value sources. This is a user-decision blocker, not a PASS.
5. In a second terminal, run:

   ```bash
   python -m fashion_cms.registry config/attribute_registry.xlsx
   ```

6. Confirm it reports `valid: 7 sets, 78 definitions` and a fingerprint.
7. To test rejection, make a temporary copy of the registry outside `config/`, duplicate a
   canonical enum value in that copy, and run the same command against the copy.
8. Confirm validation fails. Do not replace the committed registry with the invalid copy.

## 3. Validate input workbooks

1. Open **CMS Generator**.
2. Select **Topwear** and its default profile.
3. Upload `uat/inputs/topwear_structural.xlsx` and confirm its exact required columns are `sku`,
   `base_code`, `attributes__lulu_ean`, `attributes__shipping_weight`, and `input_data`.
   `base_code` groups variants; `input_data` is evidence for its SKU. `attributes__model` remains
   a CMS output field and is not a replacement input column.
4. Upload `uat/inputs/images/000123-1.png` and `uat/inputs/images/ABC-12-2.png`.
5. Confirm `000123` and EAN `0000000123456` retain their leading zeros.
6. Confirm `ABC-12-2.png` maps to SKU `ABC-12`, ordinal `2`.
7. Confirm missing-image warnings name the unmatched structural SKUs and do not trigger a
   model call.
8. Download the blank CMS workbook and confirm it has exactly 45 headers, retains
   `attributes__model`, and has no debug columns.
9. Upload `uat/inputs/duplicate_sku.xlsx`. Confirm the duplicate SKU is named and processing
   is blocked.
10. Upload `uat/inputs/missing_required_column.xlsx`. Confirm `input_data` is named and processing
    is blocked.
11. Upload `uat/inputs/legacy_input_header.xlsx`. Confirm `model_code_input_data` is explicitly
    rejected and is not guessed or converted.
12. Upload `uat/inputs/missing_base_code.xlsx`. Confirm it produces an actionable warning; if
    exported, the base-code cell stays blank.
13. Upload `uat/inputs/formula_like_text.xlsx`. Confirm formula-like text is treated as data and
    never executes.

## 4. Test uploaded images

1. Return to `topwear_structural.xlsx`.
2. Upload `000123-1.png` and `ABC-12-2.png`; confirm both associations and ordinals.
3. Add `ORPHAN-SKU-1.png`; confirm the exact orphan filename is reported.
4. Upload the same matched image twice; confirm duplicate ordinal is critical and neither copy
   is silently selected as the winner.
5. Upload `malformed.jpg`; confirm the exact file is reported as unreadable and processing is
   blocked.
6. Confirm unsupported files cannot be selected, or record the browser-level rejection of
   `unsupported_extension.txt`.

## 5. Test safe size-only variants with real products

1. Copy the structural workbook to a new working file.
2. Replace `REPLACE-SIZE-1` and `REPLACE-SIZE-2` with two genuine variants that have:

   - the same base code;
   - the same brand, model, description, color, pattern, and design;
   - different sizes only; and
   - real SKU-specific EANs and images.

3. Remove other rows from the working file.
4. Upload it and both variants' correctly named images.
5. Create the persistent job.
6. Confirm the group starts in `PER_SKU`.
7. Select `BASE_CODE_SIZE_ONLY`, select or confirm the representative SKU, and save.
8. Confirm the planned vision-request count becomes `1` for that group.
9. Pass **Test NVIDIA Connection**, run extraction, review, generate copy, and export.
10. Confirm eligible shared visual fields are consistent.
11. Confirm SKU, EAN, base code, shipping weight, model, and size remain specific to each row.

## 6. Test unsafe visual variants — mandatory

1. Copy the structural workbook to a new working file.
2. Replace `REPLACE-VISUAL-1` with a genuine black solid product.
3. Replace `REPLACE-VISUAL-2` with a genuine blue striped product.
4. Give both rows the same base code and supply real SKU-specific facts and images.
5. Remove other rows from the working file and upload it.
6. Create the job and keep `PER_SKU` selected.
7. Confirm the UI warns about detected color/pattern/design differences when supported by the
   supplied text.
8. Confirm the planned request count is one per SKU.
9. Run extraction, review both SKUs, generate copy, and export.
10. Confirm black/solid never appears on the blue/striped SKU.
11. Confirm blue/striped never appears on the black/solid SKU.
12. Record screenshots and results in **Variant Testing**. This scenario must PASS before sign-off.

## 7. Test real vision extraction

Do this only after an approved rotated NVIDIA key, pricing decision, and ground truth are available.

1. Copy `real_product_ground_truth_template.xlsx` to a working file.
2. Enter at least one real product for every attribute set and every Men's Accessories profile.
3. Record each expected canonical value or explicitly mark **expected blank**.
4. Record the evidence type and obtain reviewer approval independently of model output.
5. Configure `NVIDIA_API_KEY` server-side. Never enter a secret in the browser,
   workbook, screenshot, or report.
6. Restart the app and confirm the displayed fixed model is `thinkingmachines/inkling`.
7. Click **Test NVIDIA Connection** and confirm the generated blue-square guided-JSON diagnostic
   passes before any product extraction is enabled.
8. Upload one set/profile at a time and click **Run Data Extraction** explicitly for each run.
9. Compare every proposed value with approved ground truth.
10. Record incorrect, unsupported, and unexpectedly blank results. High confidence is not proof.
11. If no approved live setup exists, mark these tests **BLOCKED**, never **PASS**.

## 8. Test review behavior

1. Complete an approved NVIDIA extraction that creates review items.
2. Confirm explicit input beats a conflicting model proposal.
3. Confirm the conflict, evidence type/reference, and proposal stay visible.
4. Test **Accept**, **Edit**, **Reject**, and **Blank** on separate items.
5. Try to enter an invalid enum. Confirm it cannot be silently accepted.
6. Confirm unmapped values remain blank and review-required.
7. Stop and restart the app, open **Job History**, and reopen the job.
8. Confirm every saved decision and note survives.

## 9. Test catalog copy

1. Complete review for the selected successful SKUs.
2. Generate catalog copy.
3. Confirm title/name use only accepted facts and do not duplicate the model.
4. Confirm missing title components are omitted cleanly.
5. Confirm bullets are neutral and factual.
6. Confirm no material, care, performance, certification, origin, weight, or dimension claim
   appears without accepted supporting evidence.
7. Confirm unsupported bullet cells remain blank rather than being invented to fill six slots.
8. Confirm bullets do not all begin with the same noun.
9. Confirm keywords are useful, customer-facing, and not stuffed.

## 10. Export and verify every attribute set

For each set, complete the approved NVIDIA workflow, review, download the CMS workbook and separate
QC report, then run the verifier. Use the matching original structural workbook.

Example for Topwear:

```bash
python uat/scripts/verify_exports.py \
  --attribute-set topwear \
  --input-workbook uat/inputs/topwear_structural.xlsx \
  --report-json uat/topwear_export_report.json \
  /path/to/topwear_cms_upload.xlsx
```

Example for one Men's Accessories profile:

```bash
python uat/scripts/verify_exports.py \
  --attribute-set mens_accessories \
  --profile watches \
  --input-workbook uat/inputs/mens_accessories_structural.xlsx \
  --report-json uat/mens_accessories_watches_report.json \
  /path/to/mens_accessories_cms_upload.xlsx
```

To see all options:

```bash
python uat/scripts/verify_exports.py --help
```

The verifier is read-only. It checks workbook opening, exact header names/order/count, no extra
internal fields, expected SKU rows, text identifier preservation, approved enums, formula safety,
and profile-inapplicable blank fields. It prints a terminal report and writes JSON. A failing
check exits with code `1`.

## 11. Test all Men's Accessories profiles

Repeat the workflow and verifier for:

1. `bags_luggage`
2. `caps_headwear`
3. `watches`
4. `eyewear`
5. `belts_wallets_ties_other`

For each profile:

1. Select Men's Accessories.
2. Select and explicitly confirm the profile.
3. Confirm unrelated specialist fields are absent from the extraction-field preview and review.
4. Export the exact 61-header CMS workbook.
5. Run the verifier with the same `--profile`.
6. Confirm every irrelevant specialist field remains blank.

## 12. Test the image downloader

1. Open `uat/inputs/image_downloader_uat.xlsx` in Excel or LibreOffice.
2. Enter stable, user-controlled public URLs; do not commit the edited workbook.
3. For `VALID-IMAGE`, put one valid image URL in **Image 1**.
4. For `TRANSPARENT-PNG`, put a transparent PNG URL in **Image 1**.
5. For `FAIL-THEN-SUCCESS`, put a known failing public URL in **Image 1** and a valid image in
   **Image 2**.
6. For `HTML-RESPONSE`, put a URL returning HTML in **Image 1**.
7. For `PRIVATE-URL`, put `http://127.0.0.1/test.jpg` in **Image 1**.
8. Save the working copy and upload it on **Image Downloader**.
9. Confirm the preview keeps physical URL ordinals.
10. Download and inspect successful results.
11. Confirm every successful image is exactly 1500×1500 RGB JPEG on white.
12. Confirm aspect ratio is preserved and nothing is cropped or stretched by default.
13. Confirm transparent areas are white.
14. Confirm failed URL 1 does not rename successful URL 2: it must be `FAIL-THEN-SUCCESS-2.jpg`.
15. Confirm the ZIP is flat and contains successful images only.
16. Confirm the separate report identifies each failure and original ordinal.
17. Correct one failed public URL and select **Retry failed URLs**.
18. Confirm successful images are not downloaded again.

## 13. Test job recovery

1. Create a multi-item extraction job only after the NVIDIA connection gate passes.
2. Use an operator-controlled mock/failure environment or automated test for deterministic failure;
   do not cause a paid live failure merely for UAT.
3. Confirm completed items remain after one controlled failure.
4. Retry only failed items and confirm successful calls are not repeated.
5. Restart the app.
6. Open **Job History** and confirm the job, modes, representative, errors, and reviews persist.
7. Resume unfinished work using the same validated inputs when requested.
8. Export successful partial work and confirm QC names incomplete SKUs.
9. Start another controlled multi-item job and request cancellation.
10. Confirm completed results remain and unscheduled items can resume.

## 14. Run benign security checks

Use only local benign fixtures and private port `8501`.

1. Upload `formula_like_text.xlsx`; confirm text never executes as a formula.
2. Upload `images/malformed.jpg`; confirm safe decode rejection.
3. Use a safe locally created near-limit image/workbook; confirm the configured limit is clear and
   the app remains responsive.
4. Enter `http://127.0.0.1/test.jpg` in the downloader; confirm SSRF rejection.
5. Test a benign path-like ZIP member in a temporary archive; confirm traversal rejection.
6. Confirm unsupported extensions are rejected by the uploader or validator.
7. Do not test real internal services, credentials, malicious payloads, or public exposure.

## 15. Test the NVIDIA connection gate

Use only an approved rotated NVIDIA key. Never place it in chat, Git, a workbook, screenshot,
browser URL, or test report. Each live diagnostic may incur a provider charge.

1. With `NVIDIA_API_KEY` unset, open **CMS Generator**. Confirm the connection test reports missing
   server configuration and **Run Data Extraction** is disabled.
2. Set `NVIDIA_API_KEY` server-side, restart privately, and confirm the displayed endpoint/model are
   `https://integrate.api.nvidia.com/v1/chat/completions` and `thinkingmachines/inkling`. Confirm no
   provider, endpoint, or model editor exists.
3. Click **Test NVIDIA Connection** once. Confirm it sends only the generated 96 x 96 white image
   with a blue square and requires exactly `shape=square` and `color=blue` under guided JSON.
4. Confirm a pass enables extraction only for the current server session/key fingerprint. Restart
   or rotate the key and confirm a fresh pass is required.
5. In an operator-controlled mock or automated test, exercise 401, 429, timeout, redirect,
   malformed JSON, extra JSON fields, and wrong shape/color. Confirm each failure is sanitized and
   keeps extraction disabled.
6. Process one real Topwear SKU with nonblank `input_data` and a matched image. Click **Run Data
   Extraction** once and confirm one persistent job uses both the delimited SKU data and labelled
   image, then requires review.
7. Complete review and generate catalog copy. Confirm only accepted text facts are sent for copy;
   images are not resent and missing approved pricing remains unavailable.
8. Confirm Job History records fixed endpoint/model fingerprints but no API key. Search logs and
   SQLite for a unique safe test-key value and confirm it is absent. Historical provider rows, if
   present from an older database, remain readable but cannot configure new jobs.
9. Record all rows on the **NVIDIA Connection** checklist sheet. Detailed expected behavior and
   troubleshooting are in `docs/LLM_PROVIDERS.md`.

## 16. Record defects and sign-off

1. For every FAIL, add a row to **Defects** with a unique Defect ID, exact steps, actual result,
   expected result, and evidence path.
2. Do not mark a failed test PASS until the correction is installed and the exact test is rerun.
3. An authorized user must separately decide and record:

   - final permitted values;
   - semantic attribute pairs;
   - titles and character limits;
   - bullet rules;
   - `.xlsx` versus true `.xls` requirements;
   - white padding versus background removal;
   - retention period;
   - production hosting;
   - authentication;
   - approved models;
   - approved pricing; and
   - auto-accept thresholds.

4. Record each decision in **User Sign-Off** with approver, date, source, version, and rollback
   instruction.
5. Do not treat completed engineering checks or this workbook as production approval.

## Regenerating this pack

Maintainers can regenerate the workbooks and `expected_headers.json` from the active
registry with:

```bash
python uat/scripts/build_uat_pack.py
```

Do not hand-edit `expected_headers.json`; it is generated from `config/attribute_registry.xlsx`.
