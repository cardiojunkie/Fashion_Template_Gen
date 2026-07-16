from __future__ import annotations

import base64
from io import BytesIO
import json
import os
import traceback

import httpx
from PIL import Image
import pytest

from fashion_cms.llm_service import (
    FakeLLMClient,
    IncompleteLLMResponse,
    InvalidLLMResponse,
    LLMError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    NVIDIA_CHAT_COMPLETIONS_URL,
    NVIDIA_MAX_RESPONSE_BYTES,
    NVIDIA_MODEL,
    NvidiaInklingClient,
    NvidiaSettings,
    RetryableLLMError,
    call_with_retry,
    nvidia_connection_request,
    sanitize_error,
    test_nvidia_connection as run_nvidia_connection_test,
)


API_KEY = "sk-test-secret-must-not-leak"
NVIDIA_KEY = "nvapi-test-secret-must-not-leak"


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
    if kind == "protocol":
        raise httpx.RemoteProtocolError("protocol failed", request=request)
    raise AssertionError(kind)


@pytest.mark.parametrize(
    "failure_kind", ["429", "503", "timeout", "connect", "protocol"]
)
def test_temporary_failures_use_bounded_retry_and_can_recover(failure_kind: str) -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return _temporary_failure(failure_kind, request)
        return httpx.Response(200, json=nvidia_body())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        response, retry_count = call_with_retry(
            NvidiaInklingClient(nvidia_settings(), http_client),
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
                NvidiaInklingClient(nvidia_settings(), http_client),
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
        return httpx.Response(400, json={"error": f"bad input {NVIDIA_KEY}"})
    if kind == "refusal":
        body = nvidia_body()
        body["choices"][0]["message"]["refusal"] = "cannot comply"
        return httpx.Response(200, json=body)
    if kind == "incomplete":
        body = nvidia_body()
        body["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=body)
    if kind == "malformed-json":
        return httpx.Response(200, content=b"{")
    if kind == "malformed-output":
        body = nvidia_body()
        body["choices"] = []
        return httpx.Response(200, json=body)
    if kind == "null-output":
        body = nvidia_body()
        body["choices"][0]["message"]["content"] = None
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
                NvidiaInklingClient(nvidia_settings(), http_client),
                llm_request(),
                max_retries=5,
                sleep=sleeps.append,
            )

    assert attempts == 1
    assert sleeps == []
    assert raised.value.retryable is False
    assert NVIDIA_KEY not in str(raised.value)
    assert NVIDIA_KEY not in repr(raised.value.request_metadata)


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


def nvidia_settings() -> NvidiaSettings:
    return NvidiaSettings.from_env({"NVIDIA_API_KEY": NVIDIA_KEY})


def nvidia_body(content: str = '{"observations":[]}') -> dict[str, object]:
    return {
        "id": "chatcmpl-safe-1",
        "model": NVIDIA_MODEL,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }


def test_nvidia_settings_require_only_secret_and_do_not_expose_it() -> None:
    missing = NvidiaSettings.from_env({})
    configured = nvidia_settings()

    assert not missing.enabled
    assert missing.disabled_reason == "Configure NVIDIA_API_KEY to enable live extraction."
    assert configured.enabled
    assert configured.connection_fingerprint
    assert NVIDIA_KEY not in repr(configured)
    assert NVIDIA_KEY not in configured.model_dump_json()
    with pytest.raises(ValueError):
        NvidiaSettings(api_key=configured.api_key, model="other")
    with pytest.raises(ValueError, match="NVIDIA_API_KEY"):
        NvidiaInklingClient(missing)


def test_nvidia_client_sends_multimodal_response_format_and_parses_usage() -> None:
    request = LLMRequest(
        work_item_key="nvidia-item",
        payload={
            "model": "ignored",
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": "system rules"}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "facts"},
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64,AAAA",
                            "detail": "high",
                        },
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "schema": {"type": "object", "additionalProperties": False},
                }
            },
            "max_output_tokens": 99_999,
        },
    )
    seen: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.append(http_request)
        body = nvidia_body()
        body["usage"][NVIDIA_KEY] = 999
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        response = NvidiaInklingClient(nvidia_settings(), http_client).create(request)

    payload = json.loads(seen[0].content)
    assert str(seen[0].url) == NVIDIA_CHAT_COMPLETIONS_URL
    assert seen[0].headers["authorization"] == f"Bearer {NVIDIA_KEY}"
    assert payload["model"] == NVIDIA_MODEL
    assert payload["temperature"] == 1.0
    assert payload["top_p"] == 0.95
    assert payload["max_tokens"] == 8192
    assert payload["stream"] is False
    assert "store" not in payload
    assert payload["messages"][0] == {"role": "system", "content": "system rules"}
    assert payload["messages"][1]["content"][1]["type"] == "image_url"
    assert payload["response_format"] == {
        "type": "json_schema",
        "schema": '{"additionalProperties":false,"type":"object"}',
    }
    assert "guided_json" not in payload
    assert response.usage["input_tokens"] == 12
    assert response.usage["output_tokens"] == 4
    assert NVIDIA_KEY not in response.usage
    assert NVIDIA_KEY not in repr(response)


def test_nvidia_connection_is_one_production_shaped_vision_schema_call() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=nvidia_body('{"shape":"square","color":"blue"}'))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = NvidiaInklingClient(nvidia_settings(), http_client)
        response = run_nvidia_connection_test(client)

    assert response.status == "completed"
    assert len(seen) == 1
    response_schema = json.loads(seen[0]["response_format"]["schema"])
    assert response_schema["required"] == ["shape", "color"]
    assert response_schema["properties"]["shape"]["enum"] == [
        "square",
        "circle",
        "triangle",
    ]
    assert response_schema["properties"]["color"]["enum"] == [
        "blue",
        "red",
        "green",
        "white",
    ]
    assert seen[0]["max_tokens"] == 8192
    assert "guided_json" not in seen[0]
    image_part = seen[0]["messages"][1]["content"][1]["image_url"]
    assert image_part["detail"] == "high"
    image_url = image_part["url"]
    assert image_url.startswith("data:image/png;base64,")
    with Image.open(BytesIO(base64.b64decode(image_url.partition(",")[2]))) as image:
        assert image.format == "PNG"
        assert image.size == (96, 96)
        assert image.getpixel((0, 0)) == (255, 255, 255)
        assert image.getpixel((48, 48)) == (0, 0, 255)
    assert nvidia_connection_request().work_item_key == "nvidia-connection-diagnostic"


@pytest.mark.parametrize(
    ("response", "error_type", "retryable"),
    [
        (httpx.Response(401), LLMError, False),
        (httpx.Response(429, headers={"retry-after": "2"}), RetryableLLMError, True),
        (httpx.Response(503), RetryableLLMError, True),
        (httpx.Response(302, headers={"location": "https://example.com"}), LLMError, False),
        (httpx.Response(200, content=b"{"), InvalidLLMResponse, False),
    ],
)
def test_nvidia_failures_are_classified_without_leaking_secret(
    response: httpx.Response,
    error_type: type[LLMError],
    retryable: bool,
) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: response)
    ) as http_client, pytest.raises(error_type) as raised:
        NvidiaInklingClient(nvidia_settings(), http_client).create(llm_request())

    assert raised.value.retryable is retryable
    assert NVIDIA_KEY not in str(raised.value)
    assert NVIDIA_KEY not in repr(raised.value.request_metadata)


@pytest.mark.parametrize("finish_reason", [None, "content_filter", "tool_calls"])
def test_nvidia_requires_stop_finish_reason_and_retains_request_id(
    finish_reason: str | None,
) -> None:
    body = nvidia_body()
    body["choices"][0]["finish_reason"] = finish_reason
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as http_client, pytest.raises(InvalidLLMResponse) as raised:
        NvidiaInklingClient(nvidia_settings(), http_client).create(llm_request())

    assert raised.value.request_metadata["request_id"] == "chatcmpl-safe-1"
    assert raised.value.request_metadata["status"] == "incomplete"


@pytest.mark.parametrize("model", [None, "other/model", NVIDIA_KEY])
def test_nvidia_requires_the_exact_response_model_without_leaking_it(
    model: str | None,
) -> None:
    body = nvidia_body()
    body["model"] = model
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as http_client, pytest.raises(InvalidLLMResponse) as raised:
        NvidiaInklingClient(nvidia_settings(), http_client).create(llm_request())

    assert raised.value.request_metadata == {
        "request_id": "chatcmpl-safe-1",
        "status": "model_mismatch",
    }
    assert NVIDIA_KEY not in "".join(traceback.format_exception(raised.value))


def test_nvidia_drops_a_request_id_that_echoes_the_key_and_rejects_key_output() -> None:
    body = nvidia_body()
    body["id"] = NVIDIA_KEY
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as http_client:
        response = NvidiaInklingClient(nvidia_settings(), http_client).create(llm_request())
    assert response.request_id is None

    body = nvidia_body(f'{{"observations":["{NVIDIA_KEY}"]}}')
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as http_client, pytest.raises(InvalidLLMResponse) as raised:
        NvidiaInklingClient(nvidia_settings(), http_client).create(llm_request())
    assert NVIDIA_KEY not in "".join(traceback.format_exception(raised.value))


def test_nvidia_rejects_requests_without_response_schema_before_network() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json=nvidia_body())

    request = LLMRequest(
        work_item_key="missing-schema",
        payload={"input": "Return JSON."},
    )
    with httpx.Client(
        transport=httpx.MockTransport(handler)
    ) as http_client, pytest.raises(InvalidLLMResponse, match="JSON response schema"):
        NvidiaInklingClient(nvidia_settings(), http_client).create(request)

    assert attempts == 0


def test_nvidia_timeout_and_oversized_response_are_bounded() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timed out with {NVIDIA_KEY}", request=request)

    with httpx.Client(
        transport=httpx.MockTransport(timeout)
    ) as http_client, pytest.raises(RetryableLLMError) as timeout_error:
        NvidiaInklingClient(nvidia_settings(), http_client).create(llm_request())
    assert NVIDIA_KEY not in str(timeout_error.value)
    assert NVIDIA_KEY not in "".join(traceback.format_exception(timeout_error.value))
    assert timeout_error.value.__cause__ is None

    invalid_length = httpx.Response(
        200,
        headers={"content-length": f"invalid-{NVIDIA_KEY}"},
        json=nvidia_body(),
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: invalid_length)
    ) as http_client, pytest.raises(InvalidLLMResponse) as length_error:
        NvidiaInklingClient(nvidia_settings(), http_client).create(llm_request())
    assert NVIDIA_KEY not in "".join(traceback.format_exception(length_error.value))
    assert length_error.value.__cause__ is None

    oversized = b"x" * (NVIDIA_MAX_RESPONSE_BYTES + 1)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=oversized))
    ) as http_client, pytest.raises(InvalidLLMResponse, match="size limit"):
        NvidiaInklingClient(nvidia_settings(), http_client).create(llm_request())


def test_nvidia_connection_rejects_wrong_or_malformed_result() -> None:
    for content in (
        '{"shape":"circle","color":"blue"}',
        '{"shape":"circle","shape":"square","color":"blue"}',
        '```json\n{"shape":"square","color":"blue"}\n```',
        "not-json",
    ):
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _, value=content: httpx.Response(200, json=nvidia_body(value))
            )
        ) as http_client, pytest.raises(InvalidLLMResponse):
            run_nvidia_connection_test(NvidiaInklingClient(nvidia_settings(), http_client))


@pytest.mark.live
def test_live_nvidia_connection_with_vision_and_response_format() -> None:
    if os.environ.get("RUN_LIVE_NVIDIA_TESTS") != "1":
        pytest.skip("set RUN_LIVE_NVIDIA_TESTS=1 to allow the opt-in live request")
    settings = NvidiaSettings.from_env()
    if not settings.enabled:
        pytest.skip("NVIDIA_API_KEY is required")

    client = NvidiaInklingClient(settings)
    try:
        response = run_nvidia_connection_test(client)
    finally:
        client.close()

    assert response.status == "completed"
    assert response.request_id
    assert response.model == NVIDIA_MODEL
    assert json.loads(response.output_text) == {"shape": "square", "color": "blue"}
    assert isinstance(response.usage, dict)
