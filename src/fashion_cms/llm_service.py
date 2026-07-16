from __future__ import annotations

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
from pydantic import BaseModel, ConfigDict, Field, SecretStr


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_SAFE_ERROR_CHARACTERS = 500
MAX_REQUEST_ID_CHARACTERS = 128


class LLMSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr | None = Field(default=None, repr=False)
    model: str | None = None
    image_detail: str = "high"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMSettings:
        source = os.environ if environ is None else environ
        key = source.get("OPENAI_API_KEY", "").strip()
        model = source.get("OPENAI_MODEL", "").strip()
        detail = source.get("OPENAI_IMAGE_DETAIL", "high").strip().lower() or "high"
        if detail not in {"auto", "high", "low"}:
            raise ValueError("OPENAI_IMAGE_DETAIL must be auto, high, or low.")
        return cls(
            api_key=SecretStr(key) if key else None,
            model=model or None,
            image_detail=detail,
        )

    @property
    def enabled(self) -> bool:
        return self.api_key is not None and self.model is not None

    @property
    def disabled_reason(self) -> str | None:
        missing = [
            name
            for name, value in (
                ("OPENAI_API_KEY", self.api_key),
                ("OPENAI_MODEL", self.model),
            )
            if value is None
        ]
        return f"Configure {', '.join(missing)} to enable live extraction." if missing else None


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
    message = re.sub(r"(?i)authorization\s*:\s*bearer\s+\S+", "Authorization: [redacted]", message)
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


def _request_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", value)[:MAX_REQUEST_ID_CHARACTERS]
    return cleaned or None


def _safe_usage(value: object, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key)[:100]: _safe_usage(item, depth + 1)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, list):
        return [_safe_usage(item, depth + 1) for item in value[:100]]
    return None


class OpenAIResponsesClient:
    def __init__(
        self,
        settings: LLMSettings,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not settings.enabled:
            raise ValueError(settings.disabled_reason or "Live extraction is not configured.")
        self.settings = settings
        self._client = http_client or httpx.Client(
            timeout=httpx.Timeout(60, connect=10),
        )
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def create(self, request: LLMRequest) -> LLMResponse:
        key = self.settings.api_key
        assert key is not None  # guarded by __init__
        try:
            response = self._client.post(
                OPENAI_RESPONSES_URL,
                headers={"Authorization": f"Bearer {key.get_secret_value()}"},
                json=request.payload,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RetryableLLMError(
                "Temporary OpenAI connection failure.",
                request_metadata={"status": "connection_error"},
            ) from exc

        response_request_id = _request_id(response.headers.get("x-request-id"))
        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise RetryableLLMError(
                f"Temporary OpenAI service failure (HTTP {response.status_code}).",
                request_metadata={
                    "request_id": response_request_id,
                    "status": "retryable_error",
                },
                retry_after=_retry_after(response.headers.get("retry-after")),
            )
        if response.is_error:
            raise LLMError(
                f"OpenAI request was rejected (HTTP {response.status_code}).",
                request_metadata={
                    "request_id": response_request_id,
                    "status": "rejected",
                },
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise InvalidLLMResponse(
                "OpenAI returned malformed response data.",
                request_metadata={"request_id": response_request_id, "status": "malformed"},
            ) from exc
        if not isinstance(body, dict):
            raise InvalidLLMResponse("OpenAI returned malformed response data.")

        request_id = _request_id(body.get("id")) or response_request_id
        model = body.get("model")
        usage = _safe_usage(body.get("usage"))
        metadata: dict[str, object] = {
            "request_id": request_id,
            "model": model if isinstance(model, str) else self.settings.model,
            "usage": usage if isinstance(usage, dict) else {},
        }
        status = body.get("status")
        if status != "completed":
            metadata["status"] = "incomplete" if status == "incomplete" else "failed"
            error_type = IncompleteLLMResponse if status == "incomplete" else InvalidLLMResponse
            raise error_type("OpenAI did not return a complete response.", request_metadata=metadata)

        outputs = body.get("output")
        if not isinstance(outputs, list):
            metadata["status"] = "malformed"
            raise InvalidLLMResponse(
                "OpenAI returned incomplete structured output.",
                request_metadata=metadata,
            )
        output_texts: list[str] = []
        for output in outputs:
            if not isinstance(output, dict) or output.get("type") != "message":
                continue
            contents = output.get("content")
            if not isinstance(contents, list):
                continue
            for content in contents:
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "refusal":
                    metadata["status"] = "refused"
                    raise LLMRefusalError(
                        "The model refused the extraction request.",
                        request_metadata=metadata,
                    )
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    output_texts.append(content["text"])
        if len(output_texts) != 1 or not isinstance(model, str) or not model:
            metadata["status"] = "malformed"
            raise InvalidLLMResponse(
                "OpenAI returned incomplete structured output.",
                request_metadata=metadata,
            )
        return LLMResponse(
            request_id=request_id,
            model=model,
            status="completed",
            output_text=output_texts[0],
            usage=metadata["usage"],  # type: ignore[arg-type]
        )


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
