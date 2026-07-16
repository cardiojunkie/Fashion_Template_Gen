# LLM provider configuration

The **LLM Providers** page configures OpenAI-compatible endpoints without changing the catalog
pipeline. Custom provider configuration currently supports OpenAI-compatible endpoints. Providers
using a different API protocol require a dedicated adapter.

## Supported protocols and routes

- `OPENAI_RESPONSES` calls `{base URL}/responses`.
- `OPENAI_CHAT_COMPLETIONS` calls `{base URL}/chat/completions`.
- Both protocols discover models from `{base URL}/models` when the provider supports listing.
- `VISION_EXTRACTION` and `CATALOG_COPY` are independent active routes and may use the same or
  different providers/models. No automatic fallback or paid retry on another provider occurs.

“OpenAI-compatible” means the endpoint accepts one of those request/response shapes. It does not
guarantee model listing, strict JSON schemas, image input, usage reporting, or identical error
codes. Anthropic Messages, Gemini, Bedrock, Vertex AI, and other native protocols need dedicated
adapters and are not implemented.

## Add, test, and activate a provider

1. Keep the application private and open **LLM Providers**.
2. Enter a unique provider name, protocol, exact API base URL, authentication mode, secret-storage
   mode, and timeout. The application adds only the known endpoint path; it never adds `/v1`.
3. For `API_KEY_HEADER`, enter the provider's documented header name. Reserved or malformed
   headers are rejected. For `NO_AUTH`, no key is used.
4. Enter vision and catalog model IDs manually, or save the provider and select **Fetch Models /
   Refresh**. A discovered model is never selected or activated automatically. If the page says
   **Model listing unsupported**, use the manual fields.
5. Select a route and run **Test Connection**. It sends only `Return exactly: BYO_LLM_OK` with a
   low output limit. Then run **Test Structured Output**. For vision, also run **Test Vision**;
   this sends only an in-memory blue-square diagnostic image.
6. Review the sanitized result, selected provider/base URL/protocol/model, capability state, last
   test time, secret mode, and pricing status. Tests may incur provider charges; configured model
   pricing is not assumed.
7. Select the purpose or purposes to activate, confirm any active-route replacement, and choose
   **Save and Activate**. Catalog activation requires text plus structured output because the
   current catalog service uses a structured response. Vision additionally requires the vision
   diagnostic. Saving an unverified provider is allowed; activating one is not.

The diagnostics prove basic protocol compatibility only, not fashion-attribute accuracy. Paid
fashion evaluation and user-approved ground truth remain separate release gates.

## Secret-storage modes

- `SESSION_ONLY` is the default. The API key stays as a masked server-session value, is never
  written to SQLite, and disappears on session/server restart. Re-enter it before resuming live
  work.
- `ENV_REFERENCE` stores only a validated environment-variable name. Put the secret value in
  Codespaces Secrets, the process environment, or the deployment secret manager. The resolved
  value is never displayed.
- `ENCRYPTED_DATABASE` is offered only in private development when
  `FASHION_CMS_LLM_MASTER_KEY` contains a URL-safe base64-encoded 32-byte key. AES-GCM ciphertext
  is stored; the master key stays outside SQLite. It remains disabled in production until real
  application authentication exists. A blank edit keeps the ciphertext; **Clear API key** requires
  separate confirmation.

Provider lists show only **API key configured** or **API key not configured**. They never show a
prefix, suffix, resolved environment value, ciphertext, authorization header, or raw response.

### Rotate keys

Back up SQLite first. Provider API-key rotation is the shortest safe path: create the replacement
key at the provider, enter it in the password field, rerun all capability tests, reactivate the
routes, then revoke the old key. For master-key rotation, inventory the encrypted providers, stop
the application, replace the master key, restart privately, and re-enter each provider API key so
its ciphertext is overwritten under the new master before resuming live work. Verify the backup
and do not delete it until every route passes. The application never sends decrypted keys to the
browser and has no insecure fallback when decryption fails.

## Endpoint security and local development

Public endpoints require HTTPS, verified TLS, no URL credentials/query/fragment, and DNS resolving
only to public addresses. Requests are IP-pinned and peer-checked; redirects, private/local,
link-local, reserved, multicast, metadata-service, non-HTTP, malformed, and oversized responses
are blocked. Certificate verification cannot be disabled in the website.

Local Ollama, LM Studio, or vLLM endpoints require all of the following server-side settings:

```bash
ALLOW_PRIVATE_LLM_ENDPOINTS=true
ALLOW_INSECURE_LLM_HTTP=true        # only when the endpoint itself is HTTP
FASHION_CMS_LLM_ENDPOINT_ALLOWLIST=localhost
```

These flags are ignored in `FASHION_CMS_ENVIRONMENT=production`; only exact allowlisted hosts are
accepted. In Codespaces, localhost means the Codespace container—not the user’s Windows computer.
A model on the user's laptop is not automatically reachable from Codespaces.

## Retirement, history, and recovery

Disabling a provider deactivates its routes. Deletion is allowed only when no historical job
snapshot references it; otherwise the provider is retired. Jobs retain a non-secret provider,
protocol, base-URL fingerprint, model, route, configuration, adapter, prompt, and schema snapshot.
Provider/model/route changes stale tests and invalidate relevant caches. Correct the configuration,
retest, and explicitly reactivate; the application never silently switches providers.

## Troubleshooting

| Result | Meaning / action |
|---|---|
| 401 / authentication failure | Re-enter the key or verify the environment reference. Never paste it into chat, logs, screenshots, or Git. |
| 403 / authorization failure | Grant the key access to the selected model/endpoint. |
| 404 / unknown model | Verify the exact model ID and base URL; do not add `/v1` unless the provider documents it. |
| 429 / rate limit | Wait for the provider limit to recover, then retry explicitly. |
| Timeout / DNS | Verify reachability and the bounded timeout; Codespaces cannot automatically reach a laptop service. |
| TLS failure | Fix the provider certificate/hostname. TLS verification cannot be disabled. |
| Model listing unsupported | Enter the model ID manually and run capability tests. |
| Malformed/incompatible response | Confirm the selected protocol and provider compatibility; a native adapter may be required. |
| Text passes, structured fails | The model cannot serve the current catalog or vision route. |
| Vision unsupported/failed | Use the model only for catalog copy, or select and test a vision-capable model. |
