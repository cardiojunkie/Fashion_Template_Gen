from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from io import BytesIO
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from fashion_cms.database import JobDatabase
from fashion_cms.image_downloader import (
    Resolver,
    _DownloadFailure,
    _pinned_url,
    _validate_connected_peer,
    _validated_destination,
    resolve_host,
)
from fashion_cms.llm_service import (
    IncompleteLLMResponse,
    InvalidLLMResponse,
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMRefusalError,
    sanitize_error,
)


ADAPTER_VERSION = "openai-compatible-adapter-v1"
PROVIDER_DIAGNOSTIC_PROMPT_VERSION = "byo-provider-diagnostics-v1"
PROVIDER_DIAGNOSTIC_SCHEMA_VERSION = "byo-provider-diagnostics-schema-v1"
MAX_BASE_URL_CHARACTERS = 2_048
MAX_MODEL_ID_CHARACTERS = 300
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
DISCOVERY_CACHE_SECONDS = 300
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}$")
BLOCKED_AUTH_HEADERS = frozenset(
    {
        "authorization",
        "accept",
        "accept-encoding",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "cookie",
        "host",
        "forwarded",
        "origin",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "user-agent",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
    }
)
METADATA_HOSTS = frozenset(
    {
        "instance-data",
        "instance-data.ec2.internal",
        "metadata",
        "metadata.azure.internal",
        "metadata.google.internal",
    }
)


class ProviderProtocol(StrEnum):
    OPENAI_RESPONSES = "OPENAI_RESPONSES"
    OPENAI_CHAT_COMPLETIONS = "OPENAI_CHAT_COMPLETIONS"


class AuthenticationMode(StrEnum):
    BEARER_TOKEN = "BEARER_TOKEN"
    API_KEY_HEADER = "API_KEY_HEADER"
    NO_AUTH = "NO_AUTH"


class SecretStorageMode(StrEnum):
    SESSION_ONLY = "SESSION_ONLY"
    ENV_REFERENCE = "ENV_REFERENCE"
    ENCRYPTED_DATABASE = "ENCRYPTED_DATABASE"


class RoutePurpose(StrEnum):
    VISION_EXTRACTION = "VISION_EXTRACTION"
    CATALOG_COPY = "CATALOG_COPY"


class ProviderTestStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    TESTING = "TESTING"
    PASSED_TEXT = "PASSED_TEXT"
    PASSED_TEXT_AND_STRUCTURED = "PASSED_TEXT_AND_STRUCTURED"
    PASSED_VISION = "PASSED_VISION"
    FAILED = "FAILED"
    STALE = "STALE"


class StructuredCapability(StrEnum):
    VERIFIED_NATIVE_STRUCTURED_OUTPUT = "VERIFIED_NATIVE_STRUCTURED_OUTPUT"
    VERIFIED_JSON_OUTPUT = "VERIFIED_JSON_OUTPUT"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"
    NOT_TESTED = "NOT_TESTED"


class VisionCapability(StrEnum):
    VISION_VERIFIED = "VISION_VERIFIED"
    VISION_FAILED = "VISION_FAILED"
    VISION_UNSUPPORTED = "VISION_UNSUPPORTED"
    VISION_NOT_TESTED = "VISION_NOT_TESTED"


class FailureCategory(StrEnum):
    INVALID_URL = "invalid_url"
    DNS_FAILURE = "dns_failure"
    BLOCKED_ENDPOINT = "blocked_endpoint"
    TIMEOUT = "timeout"
    TLS_FAILURE = "tls_failure"
    AUTHENTICATION_FAILURE = "authentication_failure"
    AUTHORIZATION_FAILURE = "authorization_failure"
    UNKNOWN_MODEL = "unknown_model"
    RATE_LIMIT = "rate_limit"
    UNSUPPORTED_ENDPOINT = "unsupported_endpoint"
    MALFORMED_RESPONSE = "malformed_response"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN_FAILURE = "unknown_failure"


class CheckState(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ProviderDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    display_name: str = Field(min_length=1, max_length=100)
    protocol: ProviderProtocol
    base_url: str = Field(min_length=1, max_length=MAX_BASE_URL_CHARACTERS)
    authentication_mode: AuthenticationMode = AuthenticationMode.BEARER_TOKEN
    api_key_header_name: str | None = None
    secret_storage_mode: SecretStorageMode = SecretStorageMode.SESSION_ONLY
    secret_reference: str | None = Field(default=None, repr=False)
    request_timeout: float = Field(default=30.0, gt=0, le=300)

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Provider name contains control characters.")
        return value


class ProviderConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    display_name: str
    protocol: ProviderProtocol
    base_url: str
    authentication_mode: AuthenticationMode
    api_key_header_name: str | None = None
    secret_storage_mode: SecretStorageMode
    secret_reference: str | None = Field(default=None, repr=False)
    enabled: bool
    retired: bool
    configuration_version: int
    adapter_version: str
    request_timeout: float
    created_at: str
    updated_at: str
    last_tested_at: str | None = None
    last_test_status: ProviderTestStatus
    last_test_summary: dict[str, object] | None = None

    @property
    def base_url_fingerprint(self) -> str:
        return hashlib.sha256(self.base_url.encode()).hexdigest()[:16]

    @property
    def cache_key(self) -> str:
        return hashlib.sha256(
            "\0".join(
                (
                    self.id,
                    str(self.configuration_version),
                    self.protocol.value,
                    self.base_url,
                    self.authentication_mode.value,
                    self.api_key_header_name or "",
                    self.adapter_version,
                )
            ).encode()
        ).hexdigest()


class ModelRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    purpose: RoutePurpose
    provider_id: str
    model_id: str = Field(min_length=1, max_length=MAX_MODEL_ID_CHARACTERS)
    active: bool
    enabled: bool
    timeout: float
    maximum_output_tokens: int
    image_detail: str | None
    configuration_version: int
    created_at: str
    updated_at: str


class CapabilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    model_id: str
    provider_configuration_version: int
    text_passed: bool | None = None
    structured_status: StructuredCapability
    vision_status: VisionCapability
    last_tested_at: str | None = None
    summary: dict[str, object] | None = None


def capability_supports_route(
    capability: CapabilityRecord, purpose: RoutePurpose
) -> bool:
    structured = capability.structured_status in {
        StructuredCapability.VERIFIED_NATIVE_STRUCTURED_OUTPUT,
        StructuredCapability.VERIFIED_JSON_OUTPUT,
    }
    return bool(
        capability.text_passed
        and structured
        and (
            purpose != RoutePurpose.VISION_EXTRACTION
            or capability.vision_status == VisionCapability.VISION_VERIFIED
        )
    )


class ProviderTestResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    test: str
    passed: bool
    connectivity: CheckState
    authentication: CheckState
    model_found: CheckState
    expected_result: CheckState
    latency_ms: int = Field(ge=0)
    request_id: str | None = None
    usage: dict[str, object] = Field(default_factory=dict)
    tested_at: str
    error_category: FailureCategory | None = None
    cost: str = "unavailable"
    prompt_version: str = PROVIDER_DIAGNOSTIC_PROMPT_VERSION
    schema_version: str = PROVIDER_DIAGNOSTIC_SCHEMA_VERSION
    adapter_version: str = ADAPTER_VERSION
    structured_status: StructuredCapability = StructuredCapability.NOT_TESTED
    vision_status: VisionCapability = VisionCapability.VISION_NOT_TESTED
    details: dict[str, object] = Field(default_factory=dict)

    def public_summary(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class EndpointPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allow_private: bool = False
    allow_insecure_http: bool = False
    allowed_hosts: frozenset[str] = frozenset()
    production: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> EndpointPolicy:
        source = os.environ if environ is None else environ

        def enabled(name: str) -> bool:
            return source.get(name, "").strip().casefold() in {"1", "true", "yes", "on"}

        production = source.get("FASHION_CMS_ENVIRONMENT", "development").strip().casefold() == (
            "production"
        )
        hosts = frozenset(
            value.strip().rstrip(".").casefold()
            for value in source.get("FASHION_CMS_LLM_ENDPOINT_ALLOWLIST", "").split(",")
            if value.strip()
        )
        return cls(
            allow_private=enabled("ALLOW_PRIVATE_LLM_ENDPOINTS") and not production,
            allow_insecure_http=enabled("ALLOW_INSECURE_LLM_HTTP") and not production,
            allowed_hosts=hosts,
            production=production,
        )


class ProviderRequestError(LLMError):
    def __init__(
        self,
        category: FailureCategory,
        message: str,
        *,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        super().__init__(
            sanitize_error(message),
            request_metadata={
                "status": "failed",
                "error_category": category.value,
                "request_id": request_id,
            },
        )
        self.category = category
        self.retryable = retryable


def _now() -> str:
    return datetime.now(UTC).isoformat()


def validate_header_name(value: str | None) -> str:
    name = (value or "").strip()
    if not HEADER_NAME.fullmatch(name) or name.casefold() in BLOCKED_AUTH_HEADERS:
        raise ValueError("API key header name is invalid or reserved.")
    return name


def _normalized_host(host: str) -> str:
    try:
        return host.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ValueError("Base URL hostname is invalid.") from exc


def normalize_base_url(
    value: str,
    *,
    policy: EndpointPolicy | None = None,
    resolver: Resolver = resolve_host,
) -> str:
    policy = policy or EndpointPolicy()
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_BASE_URL_CHARACTERS:
        raise ValueError("Base URL is empty or too long.")
    value = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Base URL contains control characters.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("Base URL is malformed.") from exc
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise ValueError("Base URL must use HTTPS.")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("Base URL must contain a hostname and no credentials.")
    if parsed.fragment or parsed.query:
        raise ValueError("Base URL must not contain a query string or fragment.")
    if port == 0:
        raise ValueError("Base URL port is invalid.")
    host = _normalized_host(parsed.hostname)
    if host in METADATA_HOSTS or host.endswith(".localhost") or host.endswith(".local"):
        if host not in policy.allowed_hosts:
            raise ValueError("Base URL points to a blocked local or metadata hostname.")
    if scheme != "https" and not (
        policy.allow_insecure_http and host in policy.allowed_hosts
    ):
        raise ValueError("Base URL must use HTTPS unless an allowlisted development endpoint is enabled.")
    if any(segment in {".", ".."} for segment in parsed.path.split("/")):
        raise ValueError("Base URL path is invalid.")
    allowed_private = (
        policy.allowed_hosts if policy.allow_private and host in policy.allowed_hosts else frozenset()
    )
    try:
        _validated_destination(
            value,
            resolver,
            allowed_private_hosts=allowed_private,
        )
    except _DownloadFailure as exc:
        message = str(exc)
        if "DNS" in message:
            raise ValueError("Base URL DNS lookup failed.") from exc
        raise ValueError("Base URL points to a blocked network destination.") from exc
    raw_host = f"[{host}]" if ":" in host else host
    if port is not None:
        raw_host += f":{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, raw_host, path, "", ""))


def endpoint_url(base_url: str, endpoint_path: str) -> str:
    if endpoint_path not in {"models", "responses", "chat/completions"}:
        raise ValueError("Unknown provider endpoint path.")
    return f"{base_url.rstrip('/')}/{endpoint_path}"


def encrypted_mode_available(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    encoded = source.get("FASHION_CMS_LLM_MASTER_KEY", "").strip()
    try:
        key = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError):
        return False
    return (
        len(key) == 32
        and source.get("FASHION_CMS_ENVIRONMENT", "development").strip().casefold()
        != "production"
    )


def _master_key(environ: Mapping[str, str] | None = None) -> bytes:
    source = os.environ if environ is None else environ
    if not encrypted_mode_available(source):
        raise ValueError("Encrypted database secret storage is unavailable.")
    encoded = source.get("FASHION_CMS_LLM_MASTER_KEY", "").strip()
    try:
        key = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("LLM master key is invalid.") from exc
    if len(key) != 32:
        raise ValueError("LLM master key must decode to exactly 32 bytes.")
    return key


def encrypt_secret(secret: str, provider_id: str, environ: Mapping[str, str] | None = None) -> str:
    if not secret:
        raise ValueError("An API key is required for encrypted storage.")
    nonce = os.urandom(12)
    ciphertext = AESGCM(_master_key(environ)).encrypt(
        nonce,
        secret.encode(),
        provider_id.encode(),
    )
    return "v1:" + base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt_secret(
    value: str,
    provider_id: str,
    environ: Mapping[str, str] | None = None,
) -> SecretStr:
    try:
        version, encoded = value.split(":", 1)
        payload = base64.urlsafe_b64decode(encoded)
        if version != "v1" or len(payload) < 29:
            raise ValueError
        plaintext = AESGCM(_master_key(environ)).decrypt(
            payload[:12], payload[12:], provider_id.encode()
        )
        return SecretStr(plaintext.decode())
    except Exception as exc:
        raise ValueError("Stored provider secret cannot be decrypted.") from exc


def rotate_encrypted_secret(
    value: str,
    provider_id: str,
    old_environ: Mapping[str, str],
    new_environ: Mapping[str, str],
) -> str:
    plaintext = decrypt_secret(value, provider_id, old_environ).get_secret_value()
    return encrypt_secret(plaintext, provider_id, new_environ)


class ProviderStore:
    def __init__(self, database: JobDatabase) -> None:
        self.database = database
        now = _now()
        with self.database.connection() as connection, connection:
            outdated = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM provider_configurations WHERE adapter_version != ?",
                    (ADAPTER_VERSION,),
                )
            )
            for provider_id in outdated:
                connection.execute(
                    """
                    UPDATE provider_configurations
                    SET adapter_version = ?,
                        configuration_version = configuration_version + 1,
                        last_test_status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        ADAPTER_VERSION,
                        ProviderTestStatus.STALE.value,
                        now,
                        provider_id,
                    ),
                )
                connection.execute(
                    "UPDATE provider_routes SET active = 0 WHERE provider_id = ?",
                    (provider_id,),
                )
                connection.execute(
                    "DELETE FROM provider_discovery_cache WHERE provider_id = ?",
                    (provider_id,),
                )

    @staticmethod
    def _provider(row: sqlite3.Row) -> ProviderConfiguration:
        return ProviderConfiguration(
            id=row["id"],
            display_name=row["display_name"],
            protocol=row["protocol"],
            base_url=row["base_url"],
            authentication_mode=row["authentication_mode"],
            api_key_header_name=row["api_key_header_name"],
            secret_storage_mode=row["secret_storage_mode"],
            secret_reference=row["secret_reference"],
            enabled=bool(row["enabled"]),
            retired=bool(row["retired"]),
            configuration_version=row["configuration_version"],
            adapter_version=row["adapter_version"],
            request_timeout=row["request_timeout"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_tested_at=row["last_tested_at"],
            last_test_status=row["last_test_status"],
            last_test_summary=(
                json.loads(row["last_test_summary_json"])
                if row["last_test_summary_json"]
                else None
            ),
        )

    def list_providers(self, *, include_retired: bool = False) -> tuple[ProviderConfiguration, ...]:
        query = "SELECT * FROM provider_configurations"
        if not include_retired:
            query += " WHERE retired = 0"
        query += " ORDER BY display_name COLLATE NOCASE, id"
        with self.database.connection() as connection:
            rows = connection.execute(query).fetchall()
        return tuple(self._provider(row) for row in rows)

    def get_provider(self, provider_id: str) -> ProviderConfiguration:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM provider_configurations WHERE id = ?", (provider_id,)
            ).fetchone()
        if row is None:
            raise KeyError(provider_id)
        return self._provider(row)

    def save_provider(
        self,
        draft: ProviderDraft,
        *,
        provider_id: str | None = None,
        api_key: str | None = None,
        environ: Mapping[str, str] | None = None,
        policy: EndpointPolicy | None = None,
        resolver: Resolver = resolve_host,
    ) -> ProviderConfiguration:
        identifier = provider_id or uuid.uuid4().hex
        existing = self.get_provider(identifier) if provider_id is not None else None
        base_url = normalize_base_url(draft.base_url, policy=policy, resolver=resolver)
        header = (
            validate_header_name(draft.api_key_header_name)
            if draft.authentication_mode == AuthenticationMode.API_KEY_HEADER
            else None
        )
        secret_reference: str | None = None
        if draft.authentication_mode != AuthenticationMode.NO_AUTH:
            if draft.secret_storage_mode == SecretStorageMode.ENV_REFERENCE:
                secret_reference = (draft.secret_reference or "").strip()
                if not ENV_NAME.fullmatch(secret_reference):
                    raise ValueError("Environment secret name is invalid.")
            elif draft.secret_storage_mode == SecretStorageMode.ENCRYPTED_DATABASE:
                if not encrypted_mode_available(environ):
                    raise ValueError("Encrypted database secret storage is unavailable.")
                if api_key:
                    secret_reference = encrypt_secret(api_key, identifier, environ)
                elif (
                    existing is not None
                    and existing.secret_storage_mode == SecretStorageMode.ENCRYPTED_DATABASE
                ):
                    secret_reference = existing.secret_reference
                else:
                    raise ValueError("An API key is required for encrypted storage.")
        values = (
            draft.display_name,
            draft.protocol.value,
            base_url,
            draft.authentication_mode.value,
            header,
            draft.secret_storage_mode.value,
            secret_reference,
            draft.request_timeout,
        )
        now = _now()
        try:
            with self.database.connection() as connection, connection:
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO provider_configurations (
                            id, display_name, protocol, base_url, authentication_mode,
                            api_key_header_name, secret_storage_mode, secret_reference,
                            adapter_version, request_timeout, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (identifier, *values[:7], ADAPTER_VERSION, values[7], now, now),
                    )
                else:
                    previous = (
                        existing.display_name,
                        existing.protocol.value,
                        existing.base_url,
                        existing.authentication_mode.value,
                        existing.api_key_header_name,
                        existing.secret_storage_mode.value,
                        existing.secret_reference,
                        existing.request_timeout,
                    )
                    changed = previous != values or bool(api_key)
                    version = existing.configuration_version + int(changed)
                    status = (
                        ProviderTestStatus.STALE.value
                        if changed and existing.last_test_status != ProviderTestStatus.UNVERIFIED
                        else existing.last_test_status.value
                    )
                    connection.execute(
                        """
                        UPDATE provider_configurations SET
                            display_name = ?, protocol = ?, base_url = ?,
                            authentication_mode = ?, api_key_header_name = ?,
                            secret_storage_mode = ?, secret_reference = ?,
                            request_timeout = ?, configuration_version = ?,
                            last_test_status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (*values, version, status, now, identifier),
                    )
                    if changed:
                        connection.execute(
                            "DELETE FROM provider_discovery_cache WHERE provider_id = ?",
                            (identifier,),
                        )
                        connection.execute(
                            "UPDATE provider_routes SET active = 0 WHERE provider_id = ?",
                            (identifier,),
                        )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Provider name already exists or configuration is invalid.") from exc
        return self.get_provider(identifier)

    def clear_secret(self, provider_id: str) -> ProviderConfiguration:
        provider = self.get_provider(provider_id)
        with self.database.connection() as connection, connection:
            connection.execute(
                """
                UPDATE provider_configurations
                SET secret_reference = NULL, configuration_version = configuration_version + 1,
                    last_test_status = ?, updated_at = ? WHERE id = ?
                """,
                (ProviderTestStatus.STALE.value, _now(), provider_id),
            )
            connection.execute(
                "DELETE FROM provider_discovery_cache WHERE provider_id = ?", (provider_id,)
            )
            connection.execute(
                "UPDATE provider_routes SET active = 0 WHERE provider_id = ?", (provider_id,)
            )
        return self.get_provider(provider.id)

    def set_enabled(self, provider_id: str, enabled: bool) -> ProviderConfiguration:
        self.get_provider(provider_id)
        with self.database.connection() as connection, connection:
            connection.execute(
                "UPDATE provider_configurations SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), _now(), provider_id),
            )
            if not enabled:
                connection.execute(
                    "UPDATE provider_routes SET active = 0 WHERE provider_id = ?", (provider_id,)
                )
        return self.get_provider(provider_id)

    def delete_or_retire(self, provider_id: str) -> str:
        self.get_provider(provider_id)
        with self.database.connection() as connection, connection:
            referenced = connection.execute(
                "SELECT 1 FROM job_provider_snapshots WHERE provider_id = ? LIMIT 1",
                (provider_id,),
            ).fetchone()
            if referenced:
                connection.execute(
                    """
                    UPDATE provider_configurations
                    SET retired = 1, enabled = 0, updated_at = ? WHERE id = ?
                    """,
                    (_now(), provider_id),
                )
                connection.execute(
                    "UPDATE provider_routes SET active = 0 WHERE provider_id = ?",
                    (provider_id,),
                )
                return "RETIRED"
            connection.execute("DELETE FROM provider_configurations WHERE id = ?", (provider_id,))
        return "DELETED"

    @staticmethod
    def _route(row: sqlite3.Row) -> ModelRoute:
        return ModelRoute(
            id=row["id"],
            purpose=row["purpose"],
            provider_id=row["provider_id"],
            model_id=row["model_id"],
            active=bool(row["active"]),
            enabled=bool(row["enabled"]),
            timeout=row["timeout"],
            maximum_output_tokens=row["maximum_output_tokens"],
            image_detail=row["image_detail"],
            configuration_version=row["configuration_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save_route(
        self,
        provider_id: str,
        purpose: RoutePurpose,
        model_id: str,
        *,
        timeout: float,
        maximum_output_tokens: int,
        image_detail: str | None = None,
    ) -> ModelRoute:
        provider = self.get_provider(provider_id)
        model_id = model_id.strip()
        if not model_id or len(model_id) > MAX_MODEL_ID_CHARACTERS or any(
            ord(character) < 32 or ord(character) == 127 for character in model_id
        ):
            raise ValueError("Model ID is invalid.")
        if not 0 < timeout <= 300 or not 0 < maximum_output_tokens <= 100_000:
            raise ValueError("Route timeout or maximum output tokens is invalid.")
        if purpose == RoutePurpose.VISION_EXTRACTION:
            if image_detail not in {"auto", "low", "high"}:
                raise ValueError("Vision image detail must be auto, low, or high.")
        else:
            image_detail = None
        now = _now()
        with self.database.connection() as connection, connection:
            existing = connection.execute(
                "SELECT * FROM provider_routes WHERE provider_id = ? AND purpose = ?",
                (provider.id, purpose.value),
            ).fetchone()
            if existing is None:
                route_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO provider_routes (
                        id, purpose, provider_id, model_id, timeout,
                        maximum_output_tokens, image_detail, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        route_id,
                        purpose.value,
                        provider.id,
                        model_id,
                        timeout,
                        maximum_output_tokens,
                        image_detail,
                        now,
                        now,
                    ),
                )
            else:
                route_id = existing["id"]
                changed = (
                    existing["model_id"],
                    existing["timeout"],
                    existing["maximum_output_tokens"],
                    existing["image_detail"],
                ) != (model_id, timeout, maximum_output_tokens, image_detail)
                connection.execute(
                    """
                    UPDATE provider_routes SET model_id = ?, timeout = ?,
                        maximum_output_tokens = ?, image_detail = ?, active = ?,
                        configuration_version = configuration_version + ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        model_id,
                        timeout,
                        maximum_output_tokens,
                        image_detail,
                        0 if changed else existing["active"],
                        int(changed),
                        now,
                        route_id,
                    ),
                )
                if changed:
                    connection.execute(
                        """
                        UPDATE provider_configurations
                        SET configuration_version = configuration_version + 1,
                            last_test_status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (ProviderTestStatus.STALE.value, now, provider.id),
                    )
                    connection.execute(
                        "UPDATE provider_routes SET active = 0 WHERE provider_id = ?",
                        (provider.id,),
                    )
        return self.get_route(route_id)

    def get_route(self, route_id: str) -> ModelRoute:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM provider_routes WHERE id = ?", (route_id,)
            ).fetchone()
        if row is None:
            raise KeyError(route_id)
        return self._route(row)

    def list_routes(self, provider_id: str | None = None) -> tuple[ModelRoute, ...]:
        query = "SELECT * FROM provider_routes"
        parameters: tuple[object, ...] = ()
        if provider_id is not None:
            query += " WHERE provider_id = ?"
            parameters = (provider_id,)
        query += " ORDER BY purpose, provider_id"
        with self.database.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(self._route(row) for row in rows)

    def active_route(self, purpose: RoutePurpose) -> ModelRoute | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM provider_routes WHERE purpose = ? AND active = 1 AND enabled = 1",
                (purpose.value,),
            ).fetchone()
        return self._route(row) if row else None

    def capability(self, provider_id: str, model_id: str) -> CapabilityRecord:
        provider = self.get_provider(provider_id)
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM provider_capability_tests
                WHERE provider_id = ? AND model_id = ?
                  AND provider_configuration_version = ?
                """,
                (provider.id, model_id, provider.configuration_version),
            ).fetchone()
        if row is None:
            return CapabilityRecord(
                provider_id=provider.id,
                model_id=model_id,
                provider_configuration_version=provider.configuration_version,
                structured_status=StructuredCapability.NOT_TESTED,
                vision_status=VisionCapability.VISION_NOT_TESTED,
            )
        return CapabilityRecord(
            provider_id=row["provider_id"],
            model_id=row["model_id"],
            provider_configuration_version=row["provider_configuration_version"],
            text_passed=None if row["text_passed"] is None else bool(row["text_passed"]),
            structured_status=row["structured_status"],
            vision_status=row["vision_status"],
            last_tested_at=row["last_tested_at"],
            summary=json.loads(row["summary_json"]) if row["summary_json"] else None,
        )

    def record_test(
        self,
        provider_id: str,
        model_id: str,
        result: ProviderTestResult,
    ) -> CapabilityRecord:
        provider = self.get_provider(provider_id)
        existing = self.capability(provider_id, model_id)
        text_passed = existing.text_passed
        structured = existing.structured_status
        vision = existing.vision_status
        if result.test == "TEXT":
            text_passed = result.passed
        elif result.test == "STRUCTURED":
            structured = result.structured_status
        elif result.test == "VISION":
            vision = result.vision_status
        status = ProviderTestStatus.FAILED
        if text_passed and vision == VisionCapability.VISION_VERIFIED:
            status = ProviderTestStatus.PASSED_VISION
        elif text_passed and structured in {
            StructuredCapability.VERIFIED_NATIVE_STRUCTURED_OUTPUT,
            StructuredCapability.VERIFIED_JSON_OUTPUT,
        }:
            status = ProviderTestStatus.PASSED_TEXT_AND_STRUCTURED
        elif text_passed:
            status = ProviderTestStatus.PASSED_TEXT
        summary = json.dumps(result.public_summary(), separators=(",", ":"), sort_keys=True)
        with self.database.connection() as connection, connection:
            connection.execute(
                """
                INSERT INTO provider_capability_tests (
                    provider_id, model_id, provider_configuration_version,
                    text_passed, structured_status, vision_status,
                    last_tested_at, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (provider_id, model_id, provider_configuration_version)
                DO UPDATE SET text_passed = excluded.text_passed,
                    structured_status = excluded.structured_status,
                    vision_status = excluded.vision_status,
                    last_tested_at = excluded.last_tested_at,
                    summary_json = excluded.summary_json
                """,
                (
                    provider.id,
                    model_id,
                    provider.configuration_version,
                    None if text_passed is None else int(text_passed),
                    structured.value,
                    vision.value,
                    result.tested_at,
                    summary,
                ),
            )
            connection.execute(
                """
                UPDATE provider_configurations SET last_tested_at = ?,
                    last_test_status = ?, last_test_summary_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (result.tested_at, status.value, summary, _now(), provider.id),
            )
        return self.capability(provider.id, model_id)

    def activate_route(
        self,
        route_id: str,
        *,
        secret_available: bool,
        confirm_replace: bool = False,
    ) -> ModelRoute:
        route = self.get_route(route_id)
        provider = self.get_provider(route.provider_id)
        if not provider.enabled or provider.retired or not route.enabled or not secret_available:
            raise ValueError("Provider route is disabled or its API key is unavailable.")
        capability = self.capability(provider.id, route.model_id)
        if not capability_supports_route(capability, route.purpose):
            raise ValueError("Required provider capability tests have not passed.")
        current = self.active_route(route.purpose)
        if current is not None and current.id != route.id and not confirm_replace:
            raise ValueError("Replacing the active route requires confirmation.")
        with self.database.connection() as connection, connection:
            connection.execute(
                "UPDATE provider_routes SET active = 0 WHERE purpose = ?",
                (route.purpose.value,),
            )
            connection.execute(
                "UPDATE provider_routes SET active = 1, updated_at = ? WHERE id = ?",
                (_now(), route.id),
            )
        return self.get_route(route.id)

    def cached_models(self, provider: ProviderConfiguration) -> tuple[str, ...] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM provider_discovery_cache WHERE provider_id = ?",
                (provider.id,),
            ).fetchone()
        if row is None or row["provider_configuration_version"] != provider.configuration_version:
            return None
        fetched = datetime.fromisoformat(row["fetched_at"])
        if datetime.now(UTC) - fetched > timedelta(seconds=DISCOVERY_CACHE_SECONDS):
            return None
        return tuple(json.loads(row["model_ids_json"]))

    def cache_models(self, provider: ProviderConfiguration, model_ids: Sequence[str]) -> None:
        with self.database.connection() as connection, connection:
            connection.execute(
                """
                INSERT INTO provider_discovery_cache (
                    provider_id, provider_configuration_version, model_ids_json, fetched_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    provider_configuration_version = excluded.provider_configuration_version,
                    model_ids_json = excluded.model_ids_json,
                    fetched_at = excluded.fetched_at
                """,
                (
                    provider.id,
                    provider.configuration_version,
                    json.dumps(list(model_ids), separators=(",", ":")),
                    _now(),
                ),
            )

    def record_job_snapshot(
        self,
        job_id: str,
        purpose: RoutePurpose,
        *,
        provider: ProviderConfiguration | None,
        display_name: str,
        protocol: str,
        base_url_fingerprint: str,
        model_id: str,
        provider_configuration_version: int,
        adapter_version: str,
        prompt_version: str,
        schema_version: str,
    ) -> None:
        self.database.get_job(job_id)
        with self.database.connection() as connection, connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO job_provider_snapshots (
                    id, job_id, purpose, provider_id, provider_display_name, protocol,
                    base_url_fingerprint, model_id, provider_configuration_version,
                    adapter_version, prompt_version, schema_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    job_id,
                    purpose.value,
                    provider.id if provider else None,
                    display_name,
                    protocol,
                    base_url_fingerprint,
                    model_id,
                    provider_configuration_version,
                    adapter_version,
                    prompt_version,
                    schema_version,
                    _now(),
                ),
            )

    def job_snapshots(self, job_id: str) -> tuple[dict[str, object], ...]:
        self.database.get_job(job_id)
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT purpose, provider_id, provider_display_name, protocol,
                    base_url_fingerprint, model_id, provider_configuration_version,
                    adapter_version, prompt_version, schema_version, created_at
                FROM job_provider_snapshots WHERE job_id = ? ORDER BY created_at, id
                """,
                (job_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)


def resolve_provider_secret(
    provider: ProviderConfiguration,
    *,
    session_secrets: Mapping[str, SecretStr | str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> SecretStr | None:
    if provider.authentication_mode == AuthenticationMode.NO_AUTH:
        return None
    source = os.environ if environ is None else environ
    if provider.secret_storage_mode == SecretStorageMode.SESSION_ONLY:
        value = (session_secrets or {}).get(provider.id)
        if isinstance(value, SecretStr):
            return value
        return SecretStr(value) if isinstance(value, str) and value else None
    if provider.secret_storage_mode == SecretStorageMode.ENV_REFERENCE:
        if not provider.secret_reference:
            return None
        value = source.get(provider.secret_reference, "")
        return SecretStr(value) if value else None
    if not provider.secret_reference:
        return None
    return decrypt_secret(provider.secret_reference, provider.id, source)


def provider_secret_available(
    provider: ProviderConfiguration,
    *,
    session_secrets: Mapping[str, SecretStr | str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    try:
        return provider.authentication_mode == AuthenticationMode.NO_AUTH or (
            resolve_provider_secret(
                provider, session_secrets=session_secrets, environ=environ
            )
            is not None
        )
    except ValueError:
        return False


def provider_public_row(
    provider: ProviderConfiguration,
    routes: Sequence[ModelRoute],
    *,
    secret_available: bool,
) -> dict[str, object]:
    return {
        "Provider": provider.display_name,
        "Protocol": provider.protocol.value,
        "Base URL": provider.base_url,
        "Enabled": provider.enabled and not provider.retired,
        "Secret": (
            "API key configured"
            if provider.authentication_mode != AuthenticationMode.NO_AUTH and secret_available
            else "API key not configured"
        ),
        "Last test": provider.last_test_status.value,
        "Last tested": provider.last_tested_at or "",
        "Active purposes": ", ".join(
            route.purpose.value for route in routes if route.active
        ),
    }


class ProviderAdapter(LLMClient, Protocol):
    provider: ProviderConfiguration
    route: ModelRoute

    def discover_models(self) -> tuple[str, ...]: ...

    def close(self) -> None: ...


class OpenAICompatibleAdapter:
    def __init__(
        self,
        provider: ProviderConfiguration,
        route: ModelRoute,
        secret: SecretStr | None,
        *,
        policy: EndpointPolicy | None = None,
        http_client: httpx.Client | None = None,
        resolver: Resolver = resolve_host,
    ) -> None:
        self.provider = provider
        self.route = route
        self.secret = secret
        self.policy = policy or EndpointPolicy.from_env()
        self.resolver = resolver
        self._client = http_client or httpx.Client(
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Fashion-CMS-Provider-Adapter/1",
        }
        if self.provider.authentication_mode == AuthenticationMode.NO_AUTH:
            return headers
        if self.secret is None:
            raise ProviderRequestError(
                FailureCategory.AUTHENTICATION_FAILURE,
                "Provider API key is unavailable.",
            )
        value = self.secret.get_secret_value()
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ProviderRequestError(
                FailureCategory.AUTHENTICATION_FAILURE,
                "Provider API key contains invalid control characters.",
            )
        if self.provider.authentication_mode == AuthenticationMode.BEARER_TOKEN:
            headers["Authorization"] = f"Bearer {value}"
        else:
            headers[validate_header_name(self.provider.api_key_header_name)] = value
        return headers

    def _request(
        self,
        method: str,
        endpoint_path: str,
        payload: Mapping[str, object] | None = None,
    ) -> tuple[dict[str, object], str | None]:
        url = endpoint_url(self.provider.base_url, endpoint_path)
        host = _normalized_host(urlsplit(url).hostname or "")
        allowed_private = (
            self.policy.allowed_hosts
            if self.policy.allow_private and host in self.policy.allowed_hosts
            else frozenset()
        )
        try:
            destination_host, addresses = _validated_destination(
                url,
                self.resolver,
                allowed_private_hosts=allowed_private,
            )
            request_url, pinned_headers, expected_address = _pinned_url(
                url, destination_host, addresses, 0
            )
        except _DownloadFailure as exc:
            category = (
                FailureCategory.DNS_FAILURE
                if "DNS" in str(exc)
                else FailureCategory.BLOCKED_ENDPOINT
            )
            raise ProviderRequestError(category, "Provider endpoint validation failed.") from exc
        timeout = httpx.Timeout(
            min(self.route.timeout, self.provider.request_timeout),
            connect=min(10.0, self.route.timeout, self.provider.request_timeout),
        )
        deadline = time.monotonic() + min(self.route.timeout, self.provider.request_timeout)
        try:
            with self._client.stream(
                method,
                request_url,
                headers={**self._headers(), **pinned_headers},
                json=payload,
                extensions={"sni_hostname": destination_host}
                if request_url.scheme == "https"
                else None,
                timeout=timeout,
            ) as response:
                _validate_connected_peer(
                    response,
                    expected_address,
                    required=self._owns_client,
                    allow_private=bool(allowed_private),
                )
                request_id = _safe_request_id(
                    response.headers.get("x-request-id")
                    or response.headers.get("request-id")
                )
                status = response.status_code
                if 300 <= status < 400:
                    raise ProviderRequestError(
                        FailureCategory.BLOCKED_ENDPOINT,
                        "Provider redirects are not allowed.",
                        request_id=request_id,
                    )
                if status == 401:
                    raise ProviderRequestError(
                        FailureCategory.AUTHENTICATION_FAILURE,
                        "Provider authentication failed.",
                        request_id=request_id,
                    )
                if status == 403:
                    raise ProviderRequestError(
                        FailureCategory.AUTHORIZATION_FAILURE,
                        "Provider authorization failed.",
                        request_id=request_id,
                    )
                if status == 404:
                    category = (
                        FailureCategory.UNSUPPORTED_ENDPOINT
                        if method == "GET"
                        else FailureCategory.UNKNOWN_MODEL
                    )
                    raise ProviderRequestError(
                        category,
                        "Provider endpoint or model was not found.",
                        request_id=request_id,
                    )
                if method == "GET" and status in {405, 501}:
                    raise ProviderRequestError(
                        FailureCategory.UNSUPPORTED_ENDPOINT,
                        "Provider model listing is unsupported.",
                        request_id=request_id,
                    )
                if status == 429:
                    raise ProviderRequestError(
                        FailureCategory.RATE_LIMIT,
                        "Provider rate limit was reached.",
                        retryable=True,
                        request_id=request_id,
                    )
                if status >= 500:
                    raise ProviderRequestError(
                        FailureCategory.PROVIDER_ERROR,
                        "Provider service is temporarily unavailable.",
                        retryable=True,
                        request_id=request_id,
                    )
                if not 200 <= status < 300:
                    raise ProviderRequestError(
                        FailureCategory.PROVIDER_ERROR,
                        f"Provider rejected the request (HTTP {status}).",
                        request_id=request_id,
                    )
                length = response.headers.get("content-length")
                if length is not None:
                    try:
                        if int(length) > MAX_PROVIDER_RESPONSE_BYTES:
                            raise ProviderRequestError(
                                FailureCategory.MALFORMED_RESPONSE,
                                "Provider response exceeded the size limit.",
                                request_id=request_id,
                            )
                    except ValueError as exc:
                        raise ProviderRequestError(
                            FailureCategory.MALFORMED_RESPONSE,
                            "Provider response length was invalid.",
                            request_id=request_id,
                        ) from exc
                body = bytearray()
                for chunk in response.iter_bytes(64 * 1024):
                    if time.monotonic() > deadline:
                        raise ProviderRequestError(
                            FailureCategory.TIMEOUT,
                            "Provider request timed out.",
                            retryable=True,
                            request_id=request_id,
                        )
                    if len(body) + len(chunk) > MAX_PROVIDER_RESPONSE_BYTES:
                        raise ProviderRequestError(
                            FailureCategory.MALFORMED_RESPONSE,
                            "Provider response exceeded the size limit.",
                            request_id=request_id,
                        )
                    body.extend(chunk)
        except ProviderRequestError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderRequestError(
                FailureCategory.TIMEOUT, "Provider request timed out.", retryable=True
            ) from exc
        except httpx.TransportError as exc:
            category = (
                FailureCategory.TLS_FAILURE
                if any(word in str(exc).casefold() for word in ("ssl", "tls", "certificate"))
                else FailureCategory.DNS_FAILURE
            )
            raise ProviderRequestError(category, "Provider connection failed.", retryable=True) from exc
        try:
            document = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderRequestError(
                FailureCategory.MALFORMED_RESPONSE,
                "Provider returned malformed JSON.",
                request_id=request_id,
            ) from exc
        if not isinstance(document, dict):
            raise ProviderRequestError(
                FailureCategory.MALFORMED_RESPONSE,
                "Provider returned malformed JSON.",
                request_id=request_id,
            )
        return document, request_id

    def discover_models(self) -> tuple[str, ...]:
        document, _ = self._request("GET", "models")
        data = document.get("data")
        if not isinstance(data, list) or len(data) > 10_000:
            raise ProviderRequestError(
                FailureCategory.MALFORMED_RESPONSE,
                "Provider model listing was malformed.",
            )
        identifiers = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ProviderRequestError(
                    FailureCategory.MALFORMED_RESPONSE,
                    "Provider model listing was malformed.",
                )
            identifier = item["id"].strip()
            if not identifier or len(identifier) > MAX_MODEL_ID_CHARACTERS or any(
                ord(character) < 32 or ord(character) == 127 for character in identifier
            ):
                raise ProviderRequestError(
                    FailureCategory.MALFORMED_RESPONSE,
                    "Provider model listing was malformed.",
                )
            identifiers.append(identifier)
        return tuple(sorted(set(identifiers), key=str.casefold))

    def create(self, request: LLMRequest) -> LLMResponse:
        payload = dict(request.payload)
        payload["model"] = self.route.model_id
        if self.provider.protocol == ProviderProtocol.OPENAI_RESPONSES:
            payload["store"] = False
            payload["max_output_tokens"] = min(
                int(payload.get("max_output_tokens", self.route.maximum_output_tokens)),
                self.route.maximum_output_tokens,
            )
            document, header_request_id = self._request("POST", "responses", payload)
            return _parse_responses(document, header_request_id, self.route.model_id)
        chat_payload = _to_chat_payload(payload, self.route.maximum_output_tokens)
        document, header_request_id = self._request("POST", "chat/completions", chat_payload)
        return _parse_chat(document, header_request_id, self.route.model_id)


ADAPTER_REGISTRY: dict[ProviderProtocol, type[OpenAICompatibleAdapter]] = {
    ProviderProtocol.OPENAI_RESPONSES: OpenAICompatibleAdapter,
    ProviderProtocol.OPENAI_CHAT_COMPLETIONS: OpenAICompatibleAdapter,
}


def create_adapter(
    provider: ProviderConfiguration,
    route: ModelRoute,
    secret: SecretStr | None,
    **kwargs: object,
) -> OpenAICompatibleAdapter:
    adapter = ADAPTER_REGISTRY.get(provider.protocol)
    if adapter is None:
        raise ValueError("Provider protocol requires a dedicated adapter.")
    return adapter(provider, route, secret, **kwargs)  # type: ignore[arg-type]


def discover_models(
    store: ProviderStore,
    adapter: ProviderAdapter,
    *,
    refresh: bool = False,
) -> tuple[str, ...]:
    if not refresh and (cached := store.cached_models(adapter.provider)) is not None:
        return cached
    models = adapter.discover_models()
    store.cache_models(adapter.provider, models)
    return models


def _safe_request_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", value)[:128]
    return cleaned or None


def _safe_usage(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key)[:100]: item
        for key, item in list(value.items())[:100]
        if isinstance(item, (bool, int, float)) or item is None
    }


def _parse_responses(
    body: Mapping[str, object], request_id: str | None, fallback_model: str
) -> LLMResponse:
    model = body.get("model") if isinstance(body.get("model"), str) else fallback_model
    metadata = {
        "request_id": _safe_request_id(body.get("id")) or request_id,
        "model": model,
        "usage": _safe_usage(body.get("usage")),
    }
    status = body.get("status")
    if status != "completed":
        error = IncompleteLLMResponse if status == "incomplete" else InvalidLLMResponse
        raise error("Provider did not return a complete response.", request_metadata=metadata)
    output = body.get("output")
    if not isinstance(output, list):
        raise InvalidLLMResponse("Provider returned malformed response data.", request_metadata=metadata)
    texts = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == "refusal":
                raise LLMRefusalError("Provider refused the request.", request_metadata=metadata)
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    if len(texts) != 1:
        raise InvalidLLMResponse("Provider returned malformed response data.", request_metadata=metadata)
    return LLMResponse(
        request_id=metadata["request_id"],  # type: ignore[arg-type]
        model=model,
        status="completed",
        output_text=texts[0],
        usage=metadata["usage"],  # type: ignore[arg-type]
    )


def _chat_content(content: object) -> object:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("OpenAI-compatible message content is invalid.")
    result = []
    for part in content:
        if not isinstance(part, Mapping):
            raise ValueError("OpenAI-compatible message content is invalid.")
        if part.get("type") == "input_text":
            result.append({"type": "text", "text": part.get("text", "")})
        elif part.get("type") == "input_image":
            image = {"url": part.get("image_url")}
            if part.get("detail") is not None:
                image["detail"] = part["detail"]
            result.append({"type": "image_url", "image_url": image})
        else:
            raise ValueError("OpenAI-compatible message content type is unsupported.")
    return result


def _to_chat_payload(payload: Mapping[str, object], maximum_output_tokens: int) -> dict[str, object]:
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
                raise ValueError("OpenAI-compatible message input is invalid.")
            messages.append(
                {"role": message["role"], "content": _chat_content(message.get("content"))}
            )
    else:
        raise ValueError("OpenAI-compatible request input is invalid.")
    result: dict[str, object] = {
        "model": payload["model"],
        "messages": messages,
        "store": False,
        "max_tokens": min(
            int(payload.get("max_output_tokens", maximum_output_tokens)),
            maximum_output_tokens,
        ),
    }
    text = payload.get("text")
    if isinstance(text, Mapping) and isinstance(text.get("format"), Mapping):
        format_value = dict(text["format"])
        if format_value.pop("type", None) == "json_schema":
            result["response_format"] = {
                "type": "json_schema",
                "json_schema": format_value,
            }
    return result


def _parse_chat(
    body: Mapping[str, object], request_id: str | None, fallback_model: str
) -> LLMResponse:
    model = body.get("model") if isinstance(body.get("model"), str) else fallback_model
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        raise InvalidLLMResponse("Provider returned malformed response data.")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise InvalidLLMResponse("Provider returned malformed response data.")
    if message.get("refusal"):
        raise LLMRefusalError("Provider refused the request.")
    content = message.get("content")
    if not isinstance(content, str):
        raise InvalidLLMResponse("Provider returned malformed response data.")
    usage = _safe_usage(body.get("usage"))
    if "input_tokens" not in usage and isinstance(usage.get("prompt_tokens"), int):
        usage["input_tokens"] = usage["prompt_tokens"]
    if "output_tokens" not in usage and isinstance(usage.get("completion_tokens"), int):
        usage["output_tokens"] = usage["completion_tokens"]
    return LLMResponse(
        request_id=_safe_request_id(body.get("id")) or request_id,
        model=model,
        status="completed",
        output_text=content,
        usage=usage,
    )


def _result_from_error(test: str, started: float, exc: BaseException) -> ProviderTestResult:
    category = (
        exc.category if isinstance(exc, ProviderRequestError) else FailureCategory.MALFORMED_RESPONSE
    )
    authentication = (
        CheckState.FAIL
        if category
        in {FailureCategory.AUTHENTICATION_FAILURE, FailureCategory.AUTHORIZATION_FAILURE}
        else CheckState.UNKNOWN
    )
    model_found = CheckState.FAIL if category == FailureCategory.UNKNOWN_MODEL else CheckState.UNKNOWN
    request_id = None
    if isinstance(exc, LLMError):
        request_id = _safe_request_id(exc.request_metadata.get("request_id"))
    return ProviderTestResult(
        test=test,
        passed=False,
        connectivity=(
            CheckState.FAIL
            if category
            in {
                FailureCategory.DNS_FAILURE,
                FailureCategory.BLOCKED_ENDPOINT,
                FailureCategory.TIMEOUT,
                FailureCategory.TLS_FAILURE,
            }
            else CheckState.PASS
        ),
        authentication=authentication,
        model_found=model_found,
        expected_result=CheckState.FAIL,
        latency_ms=max(0, round((time.monotonic() - started) * 1_000)),
        request_id=request_id,
        tested_at=_now(),
        error_category=category,
        structured_status=(
            StructuredCapability.UNSUPPORTED
            if test == "STRUCTURED" and category == FailureCategory.UNSUPPORTED_ENDPOINT
            else StructuredCapability.FAILED
            if test == "STRUCTURED"
            else StructuredCapability.NOT_TESTED
        ),
        vision_status=(
            VisionCapability.VISION_UNSUPPORTED
            if test == "VISION" and category == FailureCategory.UNSUPPORTED_ENDPOINT
            else VisionCapability.VISION_FAILED
            if test == "VISION"
            else VisionCapability.VISION_NOT_TESTED
        ),
    )


def test_text_connection(adapter: ProviderAdapter) -> ProviderTestResult:
    started = time.monotonic()
    request = LLMRequest(
        work_item_key="provider-text-diagnostic",
        payload={
            "model": adapter.route.model_id,
            "store": False,
            "input": "Return exactly: BYO_LLM_OK",
            "max_output_tokens": 16,
        },
    )
    try:
        response = adapter.create(request)
        exact = response.output_text == "BYO_LLM_OK"
        return ProviderTestResult(
            test="TEXT",
            passed=exact,
            connectivity=CheckState.PASS,
            authentication=CheckState.PASS,
            model_found=CheckState.PASS,
            expected_result=CheckState.PASS if exact else CheckState.FAIL,
            latency_ms=round((time.monotonic() - started) * 1_000),
            request_id=response.request_id,
            usage=response.usage,
            tested_at=_now(),
            error_category=None if exact else FailureCategory.MALFORMED_RESPONSE,
            details={"Text generation": "PASS", "Expected token returned": exact},
        )
    except (LLMError, TypeError, ValueError) as exc:
        return _result_from_error("TEXT", started, exc)


def _structured_request(model_id: str, *, strict: bool) -> LLMRequest:
    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["ok"]},
            "value": {"type": "string", "enum": ["BYO_LLM_STRUCTURED_OK"]},
        },
        "required": ["status", "value"],
        "additionalProperties": False,
    }
    payload: dict[str, object] = {
        "model": model_id,
        "store": False,
        "input": (
            "Return only JSON with status ok and value BYO_LLM_STRUCTURED_OK. "
            "Do not include extra keys."
        ),
        "max_output_tokens": 64,
    }
    if strict:
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": "byo_llm_structured_test",
                "strict": True,
                "schema": schema,
            }
        }
    return LLMRequest(work_item_key="provider-structured-diagnostic", payload=payload)


def _expected_structured(text: str) -> bool:
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return False
    return value == {"status": "ok", "value": "BYO_LLM_STRUCTURED_OK"}


def test_structured_output(adapter: ProviderAdapter) -> ProviderTestResult:
    started = time.monotonic()
    first_error: BaseException | None = None
    request_rejections = 0
    for strict in (True, False):
        try:
            response = adapter.create(_structured_request(adapter.route.model_id, strict=strict))
        except (LLMError, TypeError, ValueError) as exc:
            first_error = first_error or exc
            if isinstance(exc, ProviderRequestError) and exc.category in {
                FailureCategory.PROVIDER_ERROR,
                FailureCategory.UNSUPPORTED_ENDPOINT,
            } and not exc.retryable:
                request_rejections += 1
            if isinstance(exc, ProviderRequestError) and exc.category in {
                FailureCategory.AUTHENTICATION_FAILURE,
                FailureCategory.AUTHORIZATION_FAILURE,
                FailureCategory.DNS_FAILURE,
                FailureCategory.BLOCKED_ENDPOINT,
                FailureCategory.TIMEOUT,
                FailureCategory.TLS_FAILURE,
                FailureCategory.RATE_LIMIT,
                FailureCategory.UNKNOWN_MODEL,
            }:
                break
            continue
        if _expected_structured(response.output_text):
            capability = (
                StructuredCapability.VERIFIED_NATIVE_STRUCTURED_OUTPUT
                if strict
                else StructuredCapability.VERIFIED_JSON_OUTPUT
            )
            return ProviderTestResult(
                test="STRUCTURED",
                passed=True,
                connectivity=CheckState.PASS,
                authentication=CheckState.PASS,
                model_found=CheckState.PASS,
                expected_result=CheckState.PASS,
                latency_ms=round((time.monotonic() - started) * 1_000),
                request_id=response.request_id,
                usage=response.usage,
                tested_at=_now(),
                structured_status=capability,
                details={"Valid JSON": True, "Exact schema": True},
            )
        first_error = InvalidLLMResponse("Provider structured output did not match the schema.")
    result = _result_from_error(
        "STRUCTURED",
        started,
        first_error or InvalidLLMResponse("Provider structured output test failed."),
    )
    if request_rejections == 2:
        return result.model_copy(
            update={
                "error_category": FailureCategory.UNSUPPORTED_ENDPOINT,
                "structured_status": StructuredCapability.UNSUPPORTED,
            }
        )
    return result


def diagnostic_image() -> bytes:
    image = Image.new("RGB", (96, 96), "white")
    ImageDraw.Draw(image).rectangle((20, 20, 75, 75), fill="blue")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def test_vision(adapter: ProviderAdapter) -> ProviderTestResult:
    started = time.monotonic()
    encoded = base64.b64encode(diagnostic_image()).decode("ascii")
    schema = {
        "type": "object",
        "properties": {
            "shape": {"type": "string"},
            "color": {"type": "string"},
        },
        "required": ["shape", "color"],
        "additionalProperties": False,
    }
    request = LLMRequest(
        work_item_key="provider-vision-diagnostic",
        payload={
            "model": adapter.route.model_id,
            "store": False,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Identify the broad color and geometric shape in this test image.",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{encoded}",
                            "detail": adapter.route.image_detail or "low",
                        },
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "byo_llm_vision_test",
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": 64,
        },
    )
    try:
        response = adapter.create(request)
        value = json.loads(response.output_text)
        valid = isinstance(value, dict) and set(value) == {"shape", "color"}
        shape = re.sub(r"[^a-z]", "", str(value.get("shape", "")).casefold()) if valid else ""
        color = re.sub(r"[^a-z]", "", str(value.get("color", "")).casefold()) if valid else ""
        passed = valid and shape == "square" and color == "blue"
        return ProviderTestResult(
            test="VISION",
            passed=passed,
            connectivity=CheckState.PASS,
            authentication=CheckState.PASS,
            model_found=CheckState.PASS,
            expected_result=CheckState.PASS if passed else CheckState.FAIL,
            latency_ms=round((time.monotonic() - started) * 1_000),
            request_id=response.request_id,
            usage=response.usage,
            tested_at=_now(),
            error_category=None if passed else FailureCategory.MALFORMED_RESPONSE,
            vision_status=(
                VisionCapability.VISION_VERIFIED if passed else VisionCapability.VISION_FAILED
            ),
            details={
                "Image request accepted": True,
                "Structured response valid": valid,
                "Expected shape detected": shape == "square",
                "Expected color detected": color == "blue",
            },
        )
    except (LLMError, TypeError, ValueError, json.JSONDecodeError) as exc:
        result = _result_from_error("VISION", started, exc)
        if (
            isinstance(exc, ProviderRequestError)
            and exc.category == FailureCategory.PROVIDER_ERROR
            and not exc.retryable
        ):
            return result.model_copy(
                update={
                    "error_category": FailureCategory.UNSUPPORTED_ENDPOINT,
                    "vision_status": VisionCapability.VISION_UNSUPPORTED,
                }
            )
        return result
