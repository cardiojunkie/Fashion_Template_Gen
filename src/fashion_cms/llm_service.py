from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
import os
import random
import re
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, SecretStr


NVIDIA_CHAT_COMPLETIONS_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = "thinkingmachines/inkling"
NVIDIA_IMAGE_DETAIL = "high"
NVIDIA_MAX_TOKENS = 8_192
NVIDIA_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
NVIDIA_REQUEST_TIMEOUT_SECONDS = 60
NVIDIA_ADAPTER_VERSION = "nvidia-inkling-chat-v1"
NVIDIA_CACHE_KEY = hashlib.sha256(
    "\0".join(
        (
            NVIDIA_CHAT_COMPLETIONS_URL,
            NVIDIA_MODEL,
            NVIDIA_IMAGE_DETAIL,
            NVIDIA_ADAPTER_VERSION,
            "temperature=1",
            "top_p=0.95",
            f"max_tokens={NVIDIA_MAX_TOKENS}",
            "guided_json",
        )
    ).encode()
).hexdigest()
MAX_SAFE_ERROR_CHARACTERS = 500
MAX_REQUEST_ID_CHARACTERS = 128


class NvidiaSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr | None = Field(default=None, repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> NvidiaSettings:
        source = os.environ if environ is None else environ
        key = source.get("NVIDIA_API_KEY", "").strip()
        return cls(api_key=SecretStr(key) if key else None)

    @property
    def enabled(self) -> bool:
        return self.api_key is not None

    @property
    def disabled_reason(self) -> str | None:
        return None if self.enabled else "Configure NVIDIA_API_KEY to enable live extraction."

    @property
    def connection_fingerprint(self) -> str | None:
        if self.api_key is None:
            return None
        return hashlib.sha256(
            f"{NVIDIA_CACHE_KEY}\0{self.api_key.get_secret_value()}".encode()
        ).hexdigest()


class LLMRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    work_item_key: str
    payload: dict[str, Any] = Field(repr=False)


class LLMResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str | None = None
    model: str
    status: str
    output_text: str = Field(repr=False)
    usage: dict[str, Any] = Field(default_factory=dict)


class LLMClient(Protocol):
    def create(self, request: LLMRequest) -> LLMResponse: ...


class LLMError(RuntimeError):
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        request_metadata: Mapping[str, object] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.request_metadata = dict(request_metadata or {})
        self.retry_count = 0
        self.retry_after = retry_after


class RetryableLLMError(LLMError):
    retryable = True


class LLMRefusalError(LLMError):
    pass


class IncompleteLLMResponse(LLMError):
    pass


class InvalidLLMResponse(LLMError):
    pass


def sanitize_error(error: BaseException | str, secrets: Sequence[str] = ()) -> str:
    message = " ".join(str(error).split())
    message = re.sub(
        r"(?i)\bauthorization\s*[:=]\s*(?:(?:bearer|basic)\s+)?[^\s,;]+",
        "Authorization: [redacted]",
        message,
    )
    message = re.sub(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}", r"\1 [redacted]", message)
    message = re.sub(
        r"(?i)\b(api[_-]?key|token|secret|password)\s*[=:]\s*[^\s,;]+",
        r"\1=[redacted]",
        message,
    )
    message = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[redacted]", message)
    message = re.sub(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[redacted]@", message)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return (message or "Request failed.")[:MAX_SAFE_ERROR_CHARACTERS]


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


def _request_id(value: object, secrets: Sequence[str] = ()) -> str | None:
    if not isinstance(value, str):
        return None
    if any(secret and secret in value for secret in secrets):
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", value)[:MAX_REQUEST_ID_CHARACTERS]
    return cleaned or None


def _strict_json_loads(value: str) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=unique_object)


def _nvidia_content(role: str, content: object) -> object:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("NVIDIA message content is invalid.")
    parts: list[dict[str, object]] = []
    text_parts: list[str] = []
    for part in content:
        if not isinstance(part, Mapping):
            raise ValueError("NVIDIA message content is invalid.")
        if part.get("type") == "input_text" and isinstance(part.get("text"), str):
            text = part["text"]
            text_parts.append(text)
            parts.append({"type": "text", "text": text})
        elif part.get("type") == "input_image" and isinstance(part.get("image_url"), str):
            if role != "user":
                raise ValueError("Only user messages may contain NVIDIA image input.")
            image: dict[str, object] = {
                "url": part["image_url"],
                "detail": NVIDIA_IMAGE_DETAIL,
            }
            parts.append({"type": "image_url", "image_url": image})
        else:
            raise ValueError("NVIDIA message content type is unsupported.")
    return parts if role == "user" else "\n".join(text_parts)


def _nvidia_payload(payload: Mapping[str, object]) -> dict[str, object]:
    raw_input = payload.get("input")
    if isinstance(raw_input, str):
        messages = [{"role": "user", "content": raw_input}]
    elif isinstance(raw_input, list):
        messages = []
        for message in raw_input:
            if not isinstance(message, Mapping) or message.get("role") not in {
                "system",
                "developer",
                "user",
                "assistant",
            }:
                raise ValueError("NVIDIA message input is invalid.")
            role = "system" if message["role"] == "developer" else str(message["role"])
            messages.append(
                {"role": role, "content": _nvidia_content(role, message.get("content"))}
            )
    else:
        raise ValueError("NVIDIA request input is invalid.")

    result: dict[str, object] = {
        "model": NVIDIA_MODEL,
        "messages": messages,
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": NVIDIA_MAX_TOKENS,
        "stream": False,
    }
    text = payload.get("text")
    if isinstance(text, Mapping):
        format_value = text.get("format")
        if isinstance(format_value, Mapping) and isinstance(format_value.get("schema"), Mapping):
            result["guided_json"] = dict(format_value["schema"])
    if "guided_json" not in result:
        raise ValueError("NVIDIA requests require a guided JSON schema.")
    return result


class NvidiaInklingClient:
    def __init__(
        self,
        settings: NvidiaSettings,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not settings.enabled:
            raise ValueError(settings.disabled_reason or "NVIDIA extraction is not configured.")
        self.settings = settings
        self._client = http_client or httpx.Client(
            timeout=httpx.Timeout(NVIDIA_REQUEST_TIMEOUT_SECONDS, connect=10),
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def create(self, request: LLMRequest) -> LLMResponse:
        key = self.settings.api_key
        assert key is not None
        secret = key.get_secret_value()
        deadline = time.monotonic() + NVIDIA_REQUEST_TIMEOUT_SECONDS
        try:
            with self._client.stream(
                "POST",
                NVIDIA_CHAT_COMPLETIONS_URL,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                    "User-Agent": "Fashion-CMS-NVIDIA-Inkling/1",
                },
                json=_nvidia_payload(request.payload),
                follow_redirects=False,
                timeout=httpx.Timeout(NVIDIA_REQUEST_TIMEOUT_SECONDS, connect=10),
            ) as response:
                request_id = _request_id(
                    response.headers.get("x-request-id"), (secret,)
                )
                metadata = {"request_id": request_id}
                if time.monotonic() > deadline:
                    raise RetryableLLMError(
                        "The NVIDIA request exceeded its time limit.",
                        request_metadata={**metadata, "status": "timeout"},
                    )
                status = response.status_code
                if status == 429 or 500 <= status < 600:
                    raise RetryableLLMError(
                        f"Temporary NVIDIA service failure (HTTP {status}).",
                        request_metadata={"request_id": request_id, "status": "retryable_error"},
                        retry_after=_retry_after(response.headers.get("retry-after")),
                    )
                if status in {401, 403}:
                    raise LLMError(
                        f"NVIDIA authentication was rejected (HTTP {status}).",
                        request_metadata={"request_id": request_id, "status": "rejected"},
                    )
                if 300 <= status < 400:
                    raise LLMError(
                        "NVIDIA redirects are not accepted.",
                        request_metadata={"request_id": request_id, "status": "redirect_rejected"},
                    )
                if status >= 400:
                    raise LLMError(
                        f"NVIDIA request was rejected (HTTP {status}).",
                        request_metadata={"request_id": request_id, "status": "rejected"},
                    )
                length = response.headers.get("content-length")
                if length is not None:
                    try:
                        parsed_length = int(length)
                    except ValueError:
                        raise InvalidLLMResponse(
                            "NVIDIA response length was invalid.",
                            request_metadata={**metadata, "status": "malformed"},
                        ) from None
                    if parsed_length < 0 or parsed_length > NVIDIA_MAX_RESPONSE_BYTES:
                        raise InvalidLLMResponse(
                            "NVIDIA response exceeded the size limit.",
                            request_metadata={**metadata, "status": "oversized"},
                        )
                body = bytearray()
                for chunk in response.iter_bytes(64 * 1024):
                    if time.monotonic() > deadline:
                        raise RetryableLLMError(
                            "The NVIDIA request exceeded its time limit.",
                            request_metadata={**metadata, "status": "timeout"},
                        )
                    if len(body) + len(chunk) > NVIDIA_MAX_RESPONSE_BYTES:
                        raise InvalidLLMResponse(
                            "NVIDIA response exceeded the size limit.",
                            request_metadata={**metadata, "status": "oversized"},
                        )
                    body.extend(chunk)
        except LLMError:
            raise
        except httpx.TransportError:
            raise RetryableLLMError(
                "Temporary NVIDIA connection failure.",
                request_metadata={"status": "connection_error"},
            ) from None
        except (TypeError, ValueError) as exc:
            raise InvalidLLMResponse(
                sanitize_error(exc, (secret,)), request_metadata={"status": "invalid_request"}
            ) from None

        try:
            document = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise InvalidLLMResponse(
                "NVIDIA returned malformed response data.",
                request_metadata={"request_id": request_id, "status": "malformed"},
            ) from None
        if not isinstance(document, Mapping):
            raise InvalidLLMResponse(
                "NVIDIA returned malformed response data.",
                request_metadata={"request_id": request_id, "status": "malformed"},
            )
        request_id = _request_id(document.get("id"), (secret,)) or request_id
        metadata = {"request_id": request_id}
        choices = document.get("choices")
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], Mapping)
        ):
            raise InvalidLLMResponse(
                "NVIDIA returned malformed response data.",
                request_metadata={**metadata, "status": "malformed"},
            )
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            raise IncompleteLLMResponse(
                "NVIDIA response reached the output-token limit.",
                request_metadata={**metadata, "status": "incomplete"},
            )
        if finish_reason != "stop":
            raise InvalidLLMResponse(
                "NVIDIA did not return a complete response.",
                request_metadata={**metadata, "status": "incomplete"},
            )
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise InvalidLLMResponse(
                "NVIDIA returned malformed response data.",
                request_metadata={**metadata, "status": "malformed"},
            )
        if message.get("refusal"):
            raise LLMRefusalError(
                "The model refused the extraction request.",
                request_metadata={**metadata, "status": "refused"},
            )
        content = message.get("content")
        if not isinstance(content, str):
            raise InvalidLLMResponse(
                "NVIDIA returned malformed response data.",
                request_metadata={**metadata, "status": "malformed"},
            )
        if secret in content:
            raise InvalidLLMResponse(
                "NVIDIA returned unsafe response data.",
                request_metadata={**metadata, "status": "unsafe"},
            )
        try:
            _strict_json_loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise InvalidLLMResponse(
                "NVIDIA returned malformed structured output.",
                request_metadata={**metadata, "status": "malformed"},
            ) from None
        raw_usage = document.get("usage")
        safe_usage = (
            {
                name: value
                for name in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "input_tokens",
                    "output_tokens",
                )
                if isinstance((value := raw_usage.get(name)), int)
                and not isinstance(value, bool)
                and value >= 0
            }
            if isinstance(raw_usage, Mapping)
            else {}
        )
        if "input_tokens" not in safe_usage and isinstance(safe_usage.get("prompt_tokens"), int):
            safe_usage["input_tokens"] = safe_usage["prompt_tokens"]
        if "output_tokens" not in safe_usage and isinstance(
            safe_usage.get("completion_tokens"), int
        ):
            safe_usage["output_tokens"] = safe_usage["completion_tokens"]
        model = document.get("model")
        if model != NVIDIA_MODEL:
            raise InvalidLLMResponse(
                "NVIDIA returned an unexpected model identifier.",
                request_metadata={**metadata, "status": "model_mismatch"},
            )
        return LLMResponse(
            request_id=request_id,
            model=NVIDIA_MODEL,
            status="completed",
            output_text=content,
            usage=safe_usage,
        )


def nvidia_connection_request() -> LLMRequest:
    image = Image.new("RGB", (96, 96), "white")
    ImageDraw.Draw(image).rectangle((20, 20, 75, 75), fill="blue")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    schema = {
        "type": "object",
        "properties": {
            "shape": {"type": "string", "enum": ["square", "circle", "triangle"]},
            "color": {"type": "string", "enum": ["blue", "red", "green", "white"]},
        },
        "required": ["shape", "color"],
        "additionalProperties": False,
    }
    return LLMRequest(
        work_item_key="nvidia-connection-diagnostic",
        payload={
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Return only the requested JSON from the diagnostic image.",
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Identify the central foreground object's color and shape.",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{encoded}",
                            "detail": NVIDIA_IMAGE_DETAIL,
                        },
                    ],
                },
            ],
            "text": {"format": {"type": "json_schema", "schema": schema}},
            "max_output_tokens": 64,
        },
    )


def test_nvidia_connection(client: LLMClient) -> LLMResponse:
    response = client.create(nvidia_connection_request())
    try:
        result = _strict_json_loads(response.output_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise InvalidLLMResponse("NVIDIA connection test returned malformed JSON.") from None
    if result != {"shape": "square", "color": "blue"}:
        raise InvalidLLMResponse("NVIDIA connection test returned the wrong image result.")
    return response


FakeValue = LLMResponse | BaseException


class FakeLLMClient:
    def __init__(
        self,
        responses: Mapping[str, FakeValue | Sequence[FakeValue]] | None = None,
        *,
        responder: Callable[[LLMRequest], LLMResponse] | None = None,
    ) -> None:
        self._responses = {
            key: deque(value if isinstance(value, (list, tuple)) else (value,))
            for key, value in (responses or {}).items()
        }
        self._responder = responder
        self.calls: list[LLMRequest] = []

    def create(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if self._responder is not None:
            return self._responder(request)
        queue = self._responses.get(request.work_item_key)
        if not queue:
            raise InvalidLLMResponse("No fake response is configured for this work item.")
        value = queue.popleft() if len(queue) > 1 else queue[0]
        if isinstance(value, BaseException):
            raise value
        return value


def call_with_retry(
    client: LLMClient,
    request: LLMRequest,
    *,
    max_retries: int = 2,
    base_delay: float = 0.5,
    max_delay: float = 4.0,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    before_attempt: Callable[[], None] | None = None,
) -> tuple[LLMResponse, int]:
    for retry_count in range(max_retries + 1):
        try:
            if before_attempt is not None:
                before_attempt()
            return client.create(request), retry_count
        except LLMError as exc:
            exc.retry_count = retry_count
            exc.request_metadata["retry_count"] = retry_count
            if not exc.retryable or retry_count == max_retries:
                raise
            delay = (
                min(max_delay, exc.retry_after)
                if exc.retry_after is not None
                else min(max_delay, base_delay * (2**retry_count)) * (0.5 + jitter())
            )
            sleep(delay)
    raise AssertionError("unreachable")
