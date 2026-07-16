# NVIDIA Inkling runtime

The application has one live extraction and catalog-copy runtime. Operators cannot configure a
provider, endpoint, or model in the website.

## Fixed configuration

- Endpoint: `https://integrate.api.nvidia.com/v1/chat/completions`
- Model: `thinkingmachines/inkling`
- Server secret: `NVIDIA_API_KEY`
- Request settings: image detail `high`, `temperature=1`, `top_p=0.95`, `max_tokens=8192`, and
  `stream=false`
- Structured responses: NVIDIA `guided_json` with the request's exact JSON schema

Put `NVIDIA_API_KEY` in the server environment or deployment secret manager. Never put a real key
in Git, `.env.example`, a workbook, browser field, screenshot, report, URL, or chat. Revoke and
rotate any key that has been exposed.

NVIDIA recommends `guided_json` rather than unconstrained JSON-object mode for schema-shaped
responses; see the [NVIDIA structured-generation guide](https://docs.nvidia.com/nim/large-language-models/1.14.0/structured-generation.html).

## Connection gate

On **CMS Generator**, click **Test NVIDIA Connection** before extraction. The test sends no uploaded
or customer data. It creates an in-memory 96 x 96 white PNG containing a blue square and requires
exactly this two-field response:

```json
{"shape": "square", "color": "blue"}
```

A pass proves authentication, fixed-model access, image input, and guided JSON handling in one
request. It is bound to the current server session and API-key fingerprint. A missing or changed key,
server restart, authentication error, transport error, malformed response, extra field, or value
mismatch clears or fails the gate. **Run Data Extraction** remains disabled until the current gate
passes. The diagnostic can incur a provider charge; approved pricing is otherwise reported as
unavailable.

## Extraction workflow

1. Upload an `.xlsx` containing exactly the required columns `sku`, `base_code`,
   `attributes__lulu_ean`, `attributes__shipping_weight`, and `input_data`.
2. Upload the SKU-labelled product images. `base_code` groups variants; `input_data` supplies
   untrusted evidence only for its SKU.
3. Confirm the product profile and one analysis mode for each base-code group.
4. Pass **Test NVIDIA Connection**, confirm any displayed request/cost warning, then click
   **Run Data Extraction**.
5. Review every proposal, generate factual catalog copy from accepted facts, and export the exact
   CMS workbook plus its separate QC workbook.

Extraction never starts automatically after upload. There is no alternate provider/model, website
secret entry, model discovery, local-endpoint mode, or automatic fallback.

## Security, history, and recovery

The fixed route retains TLS verification, DNS/IP and peer validation, redirect rejection, bounded
timeouts and response sizes, retry classification, response/schema validation, and secret/error
redaction. The key never enters SQLite, job/cache records, logs, reports, or downloads. New jobs
record fixed NVIDIA endpoint/model fingerprints, not the key.

Schema-v6 provider, route, capability, and snapshot rows from earlier builds are preserved as inert
audit history. They are not selectable and do not affect new jobs. A retry after a process restart
requires the same validated workbook/images to be uploaded again and a fresh connection pass.

## Troubleshooting

| Result | Action |
|---|---|
| Missing key | Set `NVIDIA_API_KEY` server-side and restart the private app. |
| 401 / authentication failure | Rotate or correct the key; never paste it into chat, logs, screenshots, or Git. |
| 403 / model access failure | Grant the key access to `thinkingmachines/inkling`. |
| 404 / fixed route unavailable | Verify NVIDIA service availability; the endpoint/model cannot be changed in the UI. |
| 429 / rate limit | Wait for the NVIDIA limit to recover, then retry explicitly. |
| Timeout / DNS / TLS | Restore outbound HTTPS access to `integrate.api.nvidia.com`; TLS verification cannot be disabled. |
| Malformed or mismatched diagnostic | Leave extraction blocked and inspect sanitized server diagnostics; do not bypass the gate. |

The default test suite is offline. Run the opt-in live integration check only with an approved,
rotated key:

```bash
RUN_LIVE_NVIDIA_TESTS=1 python -m pytest -m live
```
