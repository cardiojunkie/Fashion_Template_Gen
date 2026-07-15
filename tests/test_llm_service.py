from __future__ import annotations

import json
import os

import httpx
import pytest

from fashion_cms.llm_service import (
    FakeLLMClient,
    IncompleteLLMResponse,
    InvalidLLMResponse,
    LLMError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMSettings,
    OpenAIResponsesClient,
    RetryableLLMError,
    call_with_retry,
    sanitize_error,
)


API_KEY = "sk-test-secret-must-not-leak"


def llm_request() -> LLMRequest:
    return LLMRequest(
        work_item_key="item-1",
        payload={
            "model": "requested-model",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "facts"}]}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "topwear",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"observations": {"type": "array"}},
                        "required": ["observations"],
                        "additionalProperties": False,
                    },
                }
            },
        },
    )


def completed_response(
    *,
    response_id: str = "resp_actual_123",
    model: str = "actual-model-2026-07-15",
) -> dict[str, object]:
    return {
        "id": response_id,
        "model": model,
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": '{"observations":[]}'},
                ],
            }
        ],
        "usage": {
            "input_tokens": 21,
            "output_tokens": 7,
            "total_tokens": 28,
            "input_tokens_details": {"cached_tokens": 3},
        },
    }


def configured_settings() -> LLMSettings:
    return LLMSettings.from_env(
        {
            "OPENAI_API_KEY": API_KEY,
            "OPENAI_MODEL": "configured-model",
            "OPENAI_IMAGE_DETAIL": "low",
        }
    )


def test_settings_are_optional_default_to_high_and_keep_secrets_out_of_output() -> None:
    disabled = LLMSettings.from_env({})

    assert disabled.api_key is None
    assert disabled.model is None
    assert disabled.image_detail == "high"
    assert disabled.enabled is False
    assert disabled.disabled_reason == (
        "Configure OPENAI_API_KEY, OPENAI_MODEL to enable live extraction."
    )

    configured = configured_settings()

    assert configured.enabled is True
    assert configured.disabled_reason is None
    assert configured.image_detail == "low"
    assert configured.api_key is not None
    assert configured.api_key.get_secret_value() == API_KEY
    assert API_KEY not in repr(configured)
    assert API_KEY not in str(configured)
    assert API_KEY not in configured.model_dump_json()


def test_settings_reject_invalid_image_detail_without_echoing_the_key() -> None:
    with pytest.raises(ValueError, match="must be auto, high, or low") as raised:
        LLMSettings.from_env(
            {
                "OPENAI_API_KEY": API_KEY,
                "OPENAI_MODEL": "configured-model",
                "OPENAI_IMAGE_DETAIL": "maximum",
            }
        )

    assert API_KEY not in str(raised.value)


def test_live_client_requires_both_key_and_model() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIResponsesClient(LLMSettings.from_env({"OPENAI_MODEL": "configured-model"}))

    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        OpenAIResponsesClient(LLMSettings.from_env({"OPENAI_API_KEY": API_KEY}))


def test_responses_client_sends_exact_payload_and_parses_actual_metadata() -> None:
    request = llm_request()
    seen: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.append(http_request)
        return httpx.Response(
            200,
            headers={"x-request-id": "header-id-is-secondary"},
            json=completed_response(),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        response = OpenAIResponsesClient(configured_settings(), http_client).create(request)

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert str(seen[0].url) == "https://api.openai.com/v1/responses"
    assert json.loads(seen[0].content) == request.payload
    assert seen[0].headers["authorization"] == f"Bearer {API_KEY}"
    assert response == LLMResponse(
        request_id="resp_actual_123",
        model="actual-model-2026-07-15",
        status="completed",
        output_text='{"observations":[]}',
        usage={
            "input_tokens": 21,
            "output_tokens": 7,
            "total_tokens": 28,
            "input_tokens_details": {"cached_tokens": 3},
        },
    )
    assert response.model != configured_settings().model
    assert API_KEY not in repr(request)
    assert API_KEY not in repr(response)


def test_response_request_id_falls_back_to_sanitized_header() -> None:
    body = completed_response(response_id="")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"x-request-id": " req/<safe>-42\n"}, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        response = OpenAIResponsesClient(configured_settings(), http_client).create(llm_request())

    assert response.request_id == "reqsafe-42"


def test_fake_client_returns_configured_results_deterministically_and_records_calls() -> None:
    first = LLMResponse(model="fake", status="completed", output_text="first")
    second = LLMResponse(model="fake", status="completed", output_text="second")
    request = llm_request()
    client = FakeLLMClient({request.work_item_key: [first, second]})

    assert client.create(request) is first
    assert client.create(request) is second
    assert client.create(request) is second
    assert client.calls == [request, request, request]

    missing = LLMRequest(work_item_key="missing", payload={})
    with pytest.raises(InvalidLLMResponse, match="No fake response"):
        client.create(missing)
    assert client.calls[-1] == missing


def _temporary_failure(kind: str, request: httpx.Request) -> httpx.Response:
    if kind == "429":
        return httpx.Response(429, json={"error": "rate limited"})
    if kind == "503":
        return httpx.Response(503, json={"error": "temporarily unavailable"})
    if kind == "timeout":
        raise httpx.ReadTimeout("read timed out", request=request)
    if kind == "connect":
        raise httpx.ConnectError("connection failed", request=request)
    raise AssertionError(kind)


@pytest.mark.parametrize("failure_kind", ["429", "503", "timeout", "connect"])
def test_temporary_failures_use_bounded_retry_and_can_recover(failure_kind: str) -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return _temporary_failure(failure_kind, request)
        return httpx.Response(200, json=completed_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        response, retry_count = call_with_retry(
            OpenAIResponsesClient(configured_settings(), http_client),
            llm_request(),
            max_retries=2,
            base_delay=0.5,
            max_delay=4,
            sleep=sleeps.append,
            jitter=lambda: 0.5,
        )

    assert response.status == "completed"
    assert retry_count == 2
    assert attempts == 3
    assert sleeps == [0.5, 1.0]


def test_retry_limit_is_total_attempts_minus_one() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(RetryableLLMError) as raised:
            call_with_retry(
                OpenAIResponsesClient(configured_settings(), http_client),
                llm_request(),
                max_retries=2,
                sleep=sleeps.append,
                jitter=lambda: 0.5,
            )

    assert attempts == 3
    assert len(sleeps) == 2
    assert raised.value.retry_count == 2
    assert raised.value.request_metadata["retry_count"] == 2


def test_non_retryable_failure_after_retry_records_the_actual_retry_count() -> None:
    request = llm_request()
    client = FakeLLMClient(
        {
            request.work_item_key: [
                RetryableLLMError("temporary"),
                LLMRefusalError("refused"),
            ]
        }
    )
    sleeps: list[float] = []

    with pytest.raises(LLMRefusalError) as raised:
        call_with_retry(
            client,
            request,
            max_retries=2,
            sleep=sleeps.append,
            jitter=lambda: 0.5,
        )

    assert len(client.calls) == 2
    assert sleeps == [0.5]
    assert raised.value.retry_count == 1
    assert raised.value.request_metadata["retry_count"] == 1


def _non_retryable_response(kind: str) -> httpx.Response:
    if kind == "permanent":
        return httpx.Response(400, json={"error": f"bad input {API_KEY}"})
    if kind == "refusal":
        body = completed_response()
        body["output"] = [
            {
                "type": "message",
                "content": [{"type": "refusal", "refusal": "cannot comply"}],
            }
        ]
        return httpx.Response(200, json=body)
    if kind == "incomplete":
        body = completed_response()
        body["status"] = "incomplete"
        return httpx.Response(200, json=body)
    if kind == "malformed-json":
        return httpx.Response(200, content=b"{")
    if kind == "malformed-output":
        body = completed_response()
        body["output"] = []
        return httpx.Response(200, json=body)
    if kind == "null-output":
        body = completed_response()
        body["output"] = None
        return httpx.Response(200, json=body)
    raise AssertionError(kind)


@pytest.mark.parametrize(
    ("kind", "expected_error"),
    [
        ("permanent", LLMError),
        ("refusal", LLMRefusalError),
        ("incomplete", IncompleteLLMResponse),
        ("malformed-json", InvalidLLMResponse),
        ("malformed-output", InvalidLLMResponse),
        ("null-output", InvalidLLMResponse),
    ],
)
def test_permanent_refusal_incomplete_and_malformed_results_are_not_retried(
    kind: str,
    expected_error: type[LLMError],
) -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return _non_retryable_response(kind)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(expected_error) as raised:
            call_with_retry(
                OpenAIResponsesClient(configured_settings(), http_client),
                llm_request(),
                max_retries=5,
                sleep=sleeps.append,
            )

    assert attempts == 1
    assert sleeps == []
    assert raised.value.retryable is False
    assert API_KEY not in str(raised.value)
    assert API_KEY not in repr(raised.value.request_metadata)


def test_error_sanitization_redacts_auth_secrets_flattens_and_bounds_output() -> None:
    message = (
        f" request failed\nAuthorization: Bearer {API_KEY} "
        f"upstream echoed {API_KEY} " + "x" * 1_000
    )

    sanitized = sanitize_error(message, (API_KEY,))

    assert API_KEY not in sanitized
    assert "Authorization: [redacted]" in sanitized
    assert "\n" not in sanitized
    assert len(sanitized) == 500
    assert sanitize_error("  \n\t ") == "Request failed."


@pytest.mark.live
def test_live_responses_api_with_small_strict_schema() -> None:
    if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("set RUN_LIVE_LLM_TESTS=1 to allow the opt-in live request")
    try:
        settings = LLMSettings.from_env()
    except ValueError:
        pytest.skip("valid OPENAI_API_KEY, OPENAI_MODEL, and OPENAI_IMAGE_DETAIL are required")
    if not settings.enabled:
        pytest.skip("OPENAI_API_KEY and OPENAI_MODEL are required")

    request = LLMRequest(
        work_item_key="live-minimal-safe-fixture",
        payload={
            "model": settings.model,
            "input": "Return true for ok.",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "phase5_llm_service_smoke",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"],
                        "additionalProperties": False,
                    },
                }
            },
            "max_output_tokens": 50,
        },
    )
    client = OpenAIResponsesClient(settings)
    try:
        response = client.create(request)
    finally:
        client.close()

    assert response.status == "completed"
    assert response.request_id
    assert response.model
    assert json.loads(response.output_text) == {"ok": True}
    assert isinstance(response.usage, dict)
