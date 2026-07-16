from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from fashion_cms.database import MIGRATIONS, SCHEMA_VERSION, JobDatabase
from fashion_cms.jobs import JobService
from fashion_cms.models import InputRow
from fashion_cms.provider_service import (
    ADAPTER_VERSION,
    AuthenticationMode,
    CheckState,
    EndpointPolicy,
    FailureCategory,
    ProviderDraft,
    ProviderProtocol,
    ProviderRequestError,
    ProviderStore,
    ProviderTestResult,
    RoutePurpose,
    SecretStorageMode,
    StructuredCapability,
    VisionCapability,
    create_adapter,
    diagnostic_image,
    discover_models,
    encrypted_mode_available,
    normalize_base_url,
    provider_public_row,
    provider_secret_available,
    resolve_provider_secret,
    rotate_encrypted_secret,
    test_structured_output as run_structured_test,
    test_text_connection as run_text_test,
    test_vision as run_vision_test,
)


SECRET = "fake-provider-secret-must-not-leak"
PUBLIC_IP = ("93.184.216.34",)


def resolver(_: str, __: int) -> tuple[str, ...]:
    return PUBLIC_IP


def draft(**updates: object) -> ProviderDraft:
    values: dict[str, object] = {
        "display_name": "Compatible provider",
        "protocol": ProviderProtocol.OPENAI_RESPONSES,
        "base_url": "https://llm.example/v1/",
        "authentication_mode": AuthenticationMode.BEARER_TOKEN,
        "secret_storage_mode": SecretStorageMode.SESSION_ONLY,
        "request_timeout": 15,
    }
    values.update(updates)
    return ProviderDraft.model_validate(values)


def configured(
    database: JobDatabase,
    *,
    protocol: ProviderProtocol = ProviderProtocol.OPENAI_RESPONSES,
    purpose: RoutePurpose = RoutePurpose.CATALOG_COPY,
):
    store = ProviderStore(database)
    provider = store.save_provider(draft(protocol=protocol), api_key=SECRET, resolver=resolver)
    route = store.save_route(
        provider.id,
        purpose,
        "model-test",
        timeout=15,
        maximum_output_tokens=128,
        image_detail="low" if purpose == RoutePurpose.VISION_EXTRACTION else None,
    )
    return store, provider, route


def response_body(text: str, *, model: str = "model-test") -> dict[str, object]:
    return {
        "id": "resp-safe-1",
        "model": model,
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    }


def chat_body(text: str) -> dict[str, object]:
    return {
        "id": "chat-safe-1",
        "model": "model-test",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


def successful_result(
    test: str,
    *,
    structured: StructuredCapability = StructuredCapability.NOT_TESTED,
    vision: VisionCapability = VisionCapability.VISION_NOT_TESTED,
) -> ProviderTestResult:
    return ProviderTestResult(
        test=test,
        passed=True,
        connectivity=CheckState.PASS,
        authentication=CheckState.PASS,
        model_found=CheckState.PASS,
        expected_result=CheckState.PASS,
        latency_ms=1,
        tested_at="2026-07-16T00:00:00+00:00",
        structured_status=structured,
        vision_status=vision,
    )


@pytest.mark.parametrize(
    "url",
    (
        "http://public.example/v1",
        "file:///tmp/model",
        "https://user:password@llm.example/v1",
        "https://llm.example/v1?key=secret",
        "https://llm.example/v1#fragment",
        "https://llm.example/v1\nother",
    ),
)
def test_base_url_rejects_unsafe_shapes(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_base_url(url, resolver=resolver)


@pytest.mark.parametrize(
    ("url", "addresses"),
    (
        ("https://127.0.0.1/v1", ("127.0.0.1",)),
        ("https://localhost/v1", ("127.0.0.1",)),
        ("https://169.254.169.254/v1", ("169.254.169.254",)),
        ("https://metadata.google.internal/v1", ("169.254.169.254",)),
    ),
)
def test_private_local_and_metadata_destinations_are_blocked(
    url: str, addresses: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="blocked"):
        normalize_base_url(url, resolver=lambda *_: addresses)


def test_allowlisted_local_http_requires_both_development_flags() -> None:
    policy = EndpointPolicy(
        allow_private=True,
        allow_insecure_http=True,
        allowed_hosts=frozenset({"localhost"}),
    )

    assert normalize_base_url(
        "http://localhost:11434/v1/",
        policy=policy,
        resolver=lambda *_: ("127.0.0.1",),
    ) == "http://localhost:11434/v1"

    with pytest.raises(ValueError, match="HTTPS"):
        normalize_base_url(
            "http://localhost:11434/v1",
            policy=policy.model_copy(update={"allow_insecure_http": False}),
            resolver=lambda *_: ("127.0.0.1",),
        )


@pytest.mark.parametrize(
    ("url", "address"),
    (
        ("http://169.254.169.254/v1", "169.254.169.254"),
        ("http://multicast.test/v1", "224.0.0.1"),
        ("http://reserved.test/v1", "240.0.0.1"),
    ),
)
def test_development_allowlist_never_allows_metadata_multicast_or_reserved(
    url: str, address: str
) -> None:
    host = httpx.URL(url).host
    policy = EndpointPolicy(
        allow_private=True,
        allow_insecure_http=True,
        allowed_hosts=frozenset({host}),
    )
    with pytest.raises(ValueError, match="blocked"):
        normalize_base_url(url, policy=policy, resolver=lambda *_: (address,))


def test_provider_crud_secret_modes_and_public_rows_never_expose_keys() -> None:
    database = JobDatabase(":memory:")
    store = ProviderStore(database)
    provider = store.save_provider(draft(), api_key=SECRET, resolver=resolver)

    assert provider.secret_reference is None
    assert resolve_provider_secret(provider, session_secrets={provider.id: SECRET}) == SecretStr(
        SECRET
    )
    assert resolve_provider_secret(provider, session_secrets={}) is None
    assert SECRET not in repr(provider)
    assert SECRET not in json.dumps(
        provider_public_row(provider, (), secret_available=True), default=str
    )

    env_provider = store.save_provider(
        draft(
            display_name="Environment provider",
            secret_storage_mode=SecretStorageMode.ENV_REFERENCE,
            secret_reference="TEST_PROVIDER_KEY",
        ),
        resolver=resolver,
    )
    assert env_provider.secret_reference == "TEST_PROVIDER_KEY"
    assert resolve_provider_secret(
        env_provider, environ={"TEST_PROVIDER_KEY": SECRET}
    ) == SecretStr(SECRET)
    with pytest.raises(ValueError, match="already exists"):
        store.save_provider(draft(display_name=" compatible PROVIDER "), resolver=resolver)

    assert store.set_enabled(provider.id, False).enabled is False
    assert store.delete_or_retire(provider.id) == "DELETED"

    no_auth = store.save_provider(
        draft(
            display_name="Local no-auth provider",
            authentication_mode=AuthenticationMode.NO_AUTH,
        ),
        resolver=resolver,
    )
    assert provider_public_row(no_auth, (), secret_available=True)["Secret"] == (
        "API key not configured"
    )


def test_api_key_header_is_strictly_validated() -> None:
    store = ProviderStore(JobDatabase(":memory:"))
    valid = store.save_provider(
        draft(
            authentication_mode=AuthenticationMode.API_KEY_HEADER,
            api_key_header_name="x-api-key",
        ),
        api_key=SECRET,
        resolver=resolver,
    )
    assert valid.api_key_header_name == "x-api-key"

    for name in (
        "Authorization",
        "Accept",
        "Forwarded",
        "Host",
        "X-Forwarded-For",
        "bad\nheader",
        "bad header",
    ):
        with pytest.raises(ValueError, match="invalid or reserved"):
            store.save_provider(
                draft(
                    display_name=f"Invalid {name!r}",
                    authentication_mode=AuthenticationMode.API_KEY_HEADER,
                    api_key_header_name=name,
                ),
                api_key=SECRET,
                resolver=resolver,
            )


def test_authenticated_encryption_preserve_clear_rotation_and_no_plaintext(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "providers.sqlite3"
    database = JobDatabase(database_path)
    store = ProviderStore(database)
    old_key = base64.urlsafe_b64encode(b"o" * 32).decode()
    new_key = base64.urlsafe_b64encode(b"n" * 32).decode()
    old_env = {"FASHION_CMS_LLM_MASTER_KEY": old_key}
    new_env = {"FASHION_CMS_LLM_MASTER_KEY": new_key}
    provider = store.save_provider(
        draft(secret_storage_mode=SecretStorageMode.ENCRYPTED_DATABASE),
        api_key=SECRET,
        environ=old_env,
        resolver=resolver,
    )

    assert provider.secret_reference and provider.secret_reference.startswith("v1:")
    assert SECRET.encode() not in database_path.read_bytes()
    assert resolve_provider_secret(provider, environ=old_env) == SecretStr(SECRET)
    preserved = store.save_provider(
        draft(secret_storage_mode=SecretStorageMode.ENCRYPTED_DATABASE),
        provider_id=provider.id,
        environ=old_env,
        resolver=resolver,
    )
    assert preserved.secret_reference == provider.secret_reference
    rotated = rotate_encrypted_secret(provider.secret_reference, provider.id, old_env, new_env)
    assert SECRET not in rotated

    reencrypted = store.save_provider(
        draft(secret_storage_mode=SecretStorageMode.ENCRYPTED_DATABASE),
        provider_id=provider.id,
        api_key=SECRET,
        environ=new_env,
        resolver=resolver,
    )
    assert resolve_provider_secret(reencrypted, environ=new_env) == SecretStr(SECRET)
    with pytest.raises(ValueError, match="cannot be decrypted"):
        resolve_provider_secret(reencrypted, environ=old_env)

    cleared = store.clear_secret(provider.id)
    assert cleared.secret_reference is None
    assert not provider_secret_available(cleared, environ=old_env)

    with pytest.raises(ValueError, match="unavailable"):
        store.save_provider(
            draft(
                display_name="No master key",
                secret_storage_mode=SecretStorageMode.ENCRYPTED_DATABASE,
            ),
            api_key=SECRET,
            environ={},
            resolver=resolver,
        )
    assert encrypted_mode_available(
        {
            "FASHION_CMS_LLM_MASTER_KEY": old_key,
            "FASHION_CMS_ENVIRONMENT": "production",
            "FASHION_CMS_AUTHENTICATION_ENABLED": "true",
        }
    ) is False
    with pytest.raises(ValueError, match="unavailable"):
        store.save_provider(
            draft(secret_storage_mode=SecretStorageMode.ENCRYPTED_DATABASE),
            provider_id=provider.id,
            environ={
                "FASHION_CMS_LLM_MASTER_KEY": new_key,
                "FASHION_CMS_ENVIRONMENT": "production",
            },
            resolver=resolver,
        )


def test_route_activation_requires_current_text_structured_and_vision_tests() -> None:
    store, provider, catalog = configured(JobDatabase(":memory:"))
    vision = store.save_route(
        provider.id,
        RoutePurpose.VISION_EXTRACTION,
        "vision-model",
        timeout=20,
        maximum_output_tokens=256,
        image_detail="high",
    )
    with pytest.raises(ValueError, match="capability"):
        store.activate_route(catalog.id, secret_available=True)

    store.record_test(provider.id, catalog.model_id, successful_result("TEXT"))
    store.record_test(
        provider.id,
        catalog.model_id,
        successful_result(
            "STRUCTURED",
            structured=StructuredCapability.VERIFIED_NATIVE_STRUCTURED_OUTPUT,
        ),
    )
    assert store.activate_route(catalog.id, secret_available=True).active

    store.record_test(provider.id, vision.model_id, successful_result("TEXT"))
    store.record_test(
        provider.id,
        vision.model_id,
        successful_result(
            "STRUCTURED",
            structured=StructuredCapability.VERIFIED_JSON_OUTPUT,
        ),
    )
    with pytest.raises(ValueError, match="capability"):
        store.activate_route(vision.id, secret_available=True)
    store.record_test(
        provider.id,
        vision.model_id,
        successful_result("VISION", vision=VisionCapability.VISION_VERIFIED),
    )
    assert store.activate_route(vision.id, secret_available=True).active

    changed = store.save_provider(
        draft(request_timeout=16), provider_id=provider.id, resolver=resolver
    )
    assert changed.last_test_status.value == "STALE"
    assert store.active_route(RoutePurpose.CATALOG_COPY) is None


def test_route_edit_stales_tests_deactivates_routes_and_changes_cache_identity() -> None:
    store, provider, route = configured(JobDatabase(":memory:"))
    store.record_test(provider.id, route.model_id, successful_result("TEXT"))
    store.record_test(
        provider.id,
        route.model_id,
        successful_result(
            "STRUCTURED",
            structured=StructuredCapability.VERIFIED_NATIVE_STRUCTURED_OUTPUT,
        ),
    )
    store.activate_route(route.id, secret_available=True)
    original_cache_key = store.get_provider(provider.id).cache_key

    changed = store.save_route(
        provider.id,
        route.purpose,
        route.model_id,
        timeout=route.timeout + 1,
        maximum_output_tokens=route.maximum_output_tokens,
    )
    current = store.get_provider(provider.id)

    assert changed.configuration_version == route.configuration_version + 1
    assert current.cache_key != original_cache_key
    assert current.last_test_status.value == "STALE"
    assert store.capability(provider.id, route.model_id).text_passed is None
    assert store.active_route(route.purpose) is None


def test_adapter_version_change_stales_configuration_and_active_route() -> None:
    database = JobDatabase(":memory:")
    store, provider, route = configured(database)
    store.record_test(provider.id, route.model_id, successful_result("TEXT"))
    store.record_test(
        provider.id,
        route.model_id,
        successful_result(
            "STRUCTURED",
            structured=StructuredCapability.VERIFIED_JSON_OUTPUT,
        ),
    )
    store.activate_route(route.id, secret_available=True)
    with database.connection() as connection, connection:
        connection.execute(
            "UPDATE provider_configurations SET adapter_version = 'old-adapter' WHERE id = ?",
            (provider.id,),
        )

    refreshed = ProviderStore(database)
    current = refreshed.get_provider(provider.id)

    assert current.adapter_version == ADAPTER_VERSION
    assert current.last_test_status.value == "STALE"
    assert refreshed.active_route(route.purpose) is None


def test_replacing_active_route_requires_confirmation() -> None:
    database = JobDatabase(":memory:")
    store, first, first_route = configured(database)
    for result in (
        successful_result("TEXT"),
        successful_result(
            "STRUCTURED",
            structured=StructuredCapability.VERIFIED_NATIVE_STRUCTURED_OUTPUT,
        ),
    ):
        store.record_test(first.id, first_route.model_id, result)
    store.activate_route(first_route.id, secret_available=True)

    second = store.save_provider(
        draft(display_name="Second provider"), api_key=SECRET, resolver=resolver
    )
    second_route = store.save_route(
        second.id,
        RoutePurpose.CATALOG_COPY,
        "second-model",
        timeout=15,
        maximum_output_tokens=128,
    )
    for result in (
        successful_result("TEXT"),
        successful_result(
            "STRUCTURED",
            structured=StructuredCapability.VERIFIED_NATIVE_STRUCTURED_OUTPUT,
        ),
    ):
        store.record_test(second.id, second_route.model_id, result)
    with pytest.raises(ValueError, match="confirmation"):
        store.activate_route(second_route.id, secret_available=True)
    assert store.activate_route(
        second_route.id, secret_available=True, confirm_replace=True
    ).active


def test_vision_and_catalog_routes_are_independent_without_fallback() -> None:
    database = JobDatabase(":memory:")
    store, catalog_provider, catalog_route = configured(database)
    vision_provider = store.save_provider(
        draft(display_name="Vision provider"), api_key=SECRET, resolver=resolver
    )
    vision_route = store.save_route(
        vision_provider.id,
        RoutePurpose.VISION_EXTRACTION,
        "vision-model",
        timeout=15,
        maximum_output_tokens=128,
        image_detail="low",
    )
    for provider, route, vision in (
        (catalog_provider, catalog_route, False),
        (vision_provider, vision_route, True),
    ):
        store.record_test(provider.id, route.model_id, successful_result("TEXT"))
        store.record_test(
            provider.id,
            route.model_id,
            successful_result(
                "STRUCTURED",
                structured=StructuredCapability.VERIFIED_JSON_OUTPUT,
            ),
        )
        if vision:
            store.record_test(
                provider.id,
                route.model_id,
                successful_result("VISION", vision=VisionCapability.VISION_VERIFIED),
            )
        store.activate_route(route.id, secret_available=True)

    assert store.active_route(RoutePurpose.CATALOG_COPY).provider_id == catalog_provider.id
    assert store.active_route(RoutePurpose.VISION_EXTRACTION).provider_id == vision_provider.id
    store.set_enabled(vision_provider.id, False)
    assert store.active_route(RoutePurpose.VISION_EXTRACTION) is None
    assert store.active_route(RoutePurpose.CATALOG_COPY).provider_id == catalog_provider.id


def test_historical_job_snapshot_contains_no_secret_and_forces_retirement() -> None:
    database = JobDatabase(":memory:")
    store, provider, route = configured(database)
    job_id = JobService(database).create_job(
        (InputRow(row_number=2, sku="SKU-1", base_code="BASE"),),
        attribute_set="topwear",
        registry_version="registry",
    )
    store.record_job_snapshot(
        job_id,
        RoutePurpose.CATALOG_COPY,
        provider=provider,
        display_name=provider.display_name,
        protocol=provider.protocol.value,
        base_url_fingerprint=provider.base_url_fingerprint,
        model_id=route.model_id,
        provider_configuration_version=provider.configuration_version,
        adapter_version=ADAPTER_VERSION,
        prompt_version="prompt",
        schema_version="schema",
    )
    snapshots = store.job_snapshots(job_id)

    assert len(snapshots) == 1
    assert SECRET not in json.dumps(snapshots)
    assert store.delete_or_retire(provider.id) == "RETIRED"
    assert store.get_provider(provider.id).retired


def test_responses_discovery_is_sorted_deduplicated_cached_and_secret_safe() -> None:
    database = JobDatabase(":memory:")
    store, provider, route = configured(database)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        return httpx.Response(
            200,
            json={"data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "z-model"}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = create_adapter(
            provider,
            route,
            SecretStr(SECRET),
            http_client=client,
            resolver=resolver,
        )
        assert discover_models(store, adapter) == ("a-model", "z-model")
        assert discover_models(store, adapter) == ("a-model", "z-model")

    assert len(requests) == 1
    assert requests[0].url.path == "/v1/models"
    assert requests[0].headers["host"] == "llm.example"
    assert SECRET not in repr(store.cached_models(provider))


@pytest.mark.parametrize(
    ("status", "category"),
    (
        (401, FailureCategory.AUTHENTICATION_FAILURE),
        (403, FailureCategory.AUTHORIZATION_FAILURE),
        (404, FailureCategory.UNSUPPORTED_ENDPOINT),
        (405, FailureCategory.UNSUPPORTED_ENDPOINT),
        (429, FailureCategory.RATE_LIMIT),
        (503, FailureCategory.PROVIDER_ERROR),
    ),
)
def test_discovery_failures_are_classified_without_raw_bodies(
    status: int, category: FailureCategory
) -> None:
    store, provider, route = configured(JobDatabase(":memory:"))

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=f"raw body {SECRET}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = create_adapter(
            provider,
            route,
            SecretStr(SECRET),
            http_client=client,
            resolver=resolver,
        )
        with pytest.raises(ProviderRequestError) as raised:
            discover_models(store, adapter, refresh=True)

    assert raised.value.category == category
    assert SECRET not in str(raised.value)


@pytest.mark.parametrize(
    ("response", "category"),
    (
        (httpx.Response(302, headers={"location": "https://127.0.0.1/models"}), FailureCategory.BLOCKED_ENDPOINT),
        (httpx.Response(200, json={"data": [{"name": "missing-id"}]}), FailureCategory.MALFORMED_RESPONSE),
        (httpx.Response(200, headers={"content-length": str(3 * 1024 * 1024)}), FailureCategory.MALFORMED_RESPONSE),
    ),
)
def test_discovery_rejects_redirects_malformed_and_oversized_responses(
    response: httpx.Response, category: FailureCategory
) -> None:
    store, provider, route = configured(JobDatabase(":memory:"))
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        adapter = create_adapter(
            provider,
            route,
            SecretStr(SECRET),
            http_client=client,
            resolver=resolver,
        )
        with pytest.raises(ProviderRequestError) as raised:
            discover_models(store, adapter, refresh=True)
    assert raised.value.category == category


@pytest.mark.parametrize(
    ("error", "category"),
    (
        (httpx.ReadTimeout("slow provider"), FailureCategory.TIMEOUT),
        (httpx.ConnectError("TLS certificate failure"), FailureCategory.TLS_FAILURE),
        (httpx.ConnectError("DNS lookup failure"), FailureCategory.DNS_FAILURE),
    ),
)
def test_connection_failures_are_sanitized(
    error: httpx.TransportError, category: FailureCategory
) -> None:
    store, provider, route = configured(JobDatabase(":memory:"))

    def fail(_: httpx.Request) -> httpx.Response:
        raise error

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        adapter = create_adapter(
            provider,
            route,
            SecretStr(SECRET),
            http_client=client,
            resolver=resolver,
        )
        result = run_text_test(adapter)
    assert result.error_category == category
    assert SECRET not in json.dumps(result.public_summary())


def test_responses_text_diagnostic_validates_exact_token() -> None:
    _, provider, route = configured(JobDatabase(":memory:"))
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=response_body("BYO_LLM_OK"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = create_adapter(
            provider,
            route,
            SecretStr(SECRET),
            http_client=client,
            resolver=resolver,
        )
        result = run_text_test(adapter)

    assert result.passed
    assert result.expected_result == CheckState.PASS
    assert seen[0]["store"] is False
    assert seen[0]["max_output_tokens"] == 16
    assert "tools" not in seen[0]


def test_control_characters_in_api_keys_are_rejected_without_leaking() -> None:
    _, provider, route = configured(JobDatabase(":memory:"))
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    ) as client:
        adapter = create_adapter(
            provider,
            route,
            SecretStr(f"{SECRET}\nInjected: value"),
            http_client=client,
            resolver=resolver,
        )
        result = run_text_test(adapter)

    assert result.error_category == FailureCategory.AUTHENTICATION_FAILURE
    assert SECRET not in json.dumps(result.public_summary())


def test_chat_adapter_maps_messages_vision_and_structured_output() -> None:
    _, provider, route = configured(
        JobDatabase(":memory:"),
        protocol=ProviderProtocol.OPENAI_CHAT_COMPLETIONS,
        purpose=RoutePurpose.VISION_EXTRACTION,
    )
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        return httpx.Response(
            200,
            json=chat_body('{"shape":"square","color":"blue"}'),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = create_adapter(
            provider,
            route,
            SecretStr(SECRET),
            http_client=client,
            resolver=resolver,
        )
        result = run_vision_test(adapter)

    assert result.passed
    assert seen[0]["store"] is False
    assert seen[0]["max_tokens"] == 64
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert result.usage["input_tokens"] == 5
    assert result.usage["output_tokens"] == 2
    image = seen[0]["messages"][0]["content"][1]
    assert image["type"] == "image_url"
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert SECRET not in json.dumps(seen)


def test_structured_test_supports_native_and_json_only_fallback() -> None:
    _, provider, route = configured(JobDatabase(":memory:"))
    expected = '{"status":"ok","value":"BYO_LLM_STRUCTURED_OK"}'

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response_body(expected))
        )
    ) as client:
        native = run_structured_test(
            create_adapter(
                provider,
                route,
                SecretStr(SECRET),
                http_client=client,
                resolver=resolver,
            )
        )
    assert native.structured_status == StructuredCapability.VERIFIED_NATIVE_STRUCTURED_OUTPUT

    calls = 0

    def fallback_handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return (
            httpx.Response(400)
            if calls == 1
            else httpx.Response(200, json=response_body(expected))
        )

    with httpx.Client(transport=httpx.MockTransport(fallback_handler)) as client:
        fallback = run_structured_test(
            create_adapter(
                provider,
                route,
                SecretStr(SECRET),
                http_client=client,
                resolver=resolver,
            )
        )
    assert fallback.structured_status == StructuredCapability.VERIFIED_JSON_OUTPUT
    assert calls == 2


@pytest.mark.parametrize(
    "output",
    (
        "not-json",
        '{"status":"ok","value":"wrong"}',
        '{"status":"ok","value":"BYO_LLM_STRUCTURED_OK","extra":true}',
    ),
)
def test_structured_test_rejects_malformed_wrong_and_extra_fields(output: str) -> None:
    _, provider, route = configured(JobDatabase(":memory:"))
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response_body(output))
        )
    ) as client:
        result = run_structured_test(
            create_adapter(
                provider,
                route,
                SecretStr(SECRET),
                http_client=client,
                resolver=resolver,
            )
        )
    assert result.structured_status == StructuredCapability.FAILED


def test_rejected_structured_and_vision_requests_are_marked_unsupported() -> None:
    database = JobDatabase(":memory:")
    _, provider, text_route = configured(database)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(400))
    ) as client:
        structured = run_structured_test(
            create_adapter(
                provider,
                text_route,
                SecretStr(SECRET),
                http_client=client,
                resolver=resolver,
            )
        )
    assert structured.structured_status == StructuredCapability.UNSUPPORTED

    _, provider, vision_route = configured(
        JobDatabase(":memory:"), purpose=RoutePurpose.VISION_EXTRACTION
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(400))
    ) as client:
        vision = run_vision_test(
            create_adapter(
                provider,
                vision_route,
                SecretStr(SECRET),
                http_client=client,
                resolver=resolver,
            )
        )
    assert vision.vision_status == VisionCapability.VISION_UNSUPPORTED


def test_vision_diagnostic_is_deterministic_and_sends_no_customer_data() -> None:
    _, provider, route = configured(
        JobDatabase(":memory:"), purpose=RoutePurpose.VISION_EXTRACTION
    )
    assert diagnostic_image() == diagnostic_image()
    seen = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen = request.content
        return httpx.Response(
            200,
            json=response_body('{"shape":"square","color":"blue"}'),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_vision_test(
            create_adapter(
                provider,
                route,
                SecretStr(SECRET),
                http_client=client,
                resolver=resolver,
            )
        )

    assert result.vision_status == VisionCapability.VISION_VERIFIED
    assert b"SKU" not in seen
    assert b"customer" not in seen.lower()
    assert SECRET.encode() not in seen


@pytest.mark.parametrize("text", ("something else", " BYO_LLM_OK", "BYO_LLM_OK\n"))
def test_wrong_text_and_vision_results_fail_instead_of_accepting_http_200(text: str) -> None:
    _, provider, text_route = configured(JobDatabase(":memory:"))
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response_body(text))
        )
    ) as client:
        result = run_text_test(
            create_adapter(
                provider,
                text_route,
                SecretStr(SECRET),
                http_client=client,
                resolver=resolver,
            )
        )
    assert not result.passed
    assert result.expected_result == CheckState.FAIL

    database = JobDatabase(":memory:")
    _, provider, vision_route = configured(database, purpose=RoutePurpose.VISION_EXTRACTION)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json=response_body('{"shape":"circle","color":"red"}')
            )
        )
    ) as client:
        vision = run_vision_test(
            create_adapter(
                provider,
                vision_route,
                SecretStr(SECRET),
                http_client=client,
                resolver=resolver,
            )
        )
    assert vision.vision_status == VisionCapability.VISION_FAILED


def test_provider_configuration_participates_in_job_cache_identity() -> None:
    database = JobDatabase(":memory:")
    service = JobService(database)
    rows = (InputRow(row_number=2, sku="SKU-1", base_code="BASE"),)
    first = service.create_job(
        rows,
        attribute_set="topwear",
        registry_version="registry",
        provider_cache_key="provider-version-1",
    )
    second = service.create_job(
        rows,
        attribute_set="topwear",
        registry_version="registry",
        provider_cache_key="provider-version-2",
    )

    assert database.list_work_items(first)[0].cache_key != database.list_work_items(second)[0].cache_key


def test_v5_migration_preserves_existing_jobs_and_adds_provider_tables(tmp_path: Path) -> None:
    path = tmp_path / "phase5.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for version, migration in enumerate(MIGRATIONS[:5], start=1):
            for statement in migration:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {version}")
        connection.execute(
            """
            INSERT INTO jobs (
                id, job_type, attribute_set, status, registry_version,
                prompt_version, schema_version, model_identifier, image_detail,
                created_at, updated_at
            ) VALUES ('old-job', 'CMS_GENERATION', 'topwear', 'UPLOADED',
                'registry', 'prompt', 'schema', 'model', 'high', 'now', 'now')
            """
        )

    database = JobDatabase(path)

    assert database.schema_version == SCHEMA_VERSION == 6
    assert database.get_job("old-job").context.provider_cache_key == ""
    with database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM provider_configurations"
        ).fetchone()[0] == 0
