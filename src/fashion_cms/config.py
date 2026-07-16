from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


MIB = 1024 * 1024


class ResourceLimits(BaseModel):
    """Validated production limits. Durable retention stays disabled until approved."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workbook_bytes: int = Field(default=25 * MIB, ge=1, le=100 * MIB)
    workbook_expanded_bytes: int = Field(default=100 * MIB, ge=1, le=1024 * MIB)
    workbook_members: int = Field(default=2_000, ge=1, le=10_000)
    workbook_rows: int = Field(default=100_000, ge=1, le=1_000_000)
    workbook_columns: int = Field(default=500, ge=5, le=16_384)
    cell_characters: int = Field(default=32_767, ge=1, le=32_767)
    zip_bytes: int = Field(default=100 * MIB, ge=1, le=1024 * MIB)
    zip_expanded_bytes: int = Field(default=500 * MIB, ge=1, le=4 * 1024 * MIB)
    zip_members: int = Field(default=1_000, ge=1, le=10_000)
    uploaded_image_count: int = Field(default=500, ge=1, le=10_000)
    image_bytes: int = Field(default=25 * MIB, ge=1, le=100 * MIB)
    image_pixels: int = Field(default=50_000_000, ge=1, le=100_000_000)
    image_dimension: int = Field(default=20_000, ge=1, le=65_535)
    total_upload_bytes: int = Field(default=250 * MIB, ge=1, le=4 * 1024 * MIB)
    urls_per_sku: int = Field(default=20, ge=1, le=500)
    url_redirects: int = Field(default=5, ge=0, le=20)
    url_response_bytes: int = Field(default=25 * MIB, ge=1, le=100 * MIB)
    url_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    url_read_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    url_total_deadline_seconds: float = Field(default=120.0, gt=0, le=900)
    url_retries: int = Field(default=3, ge=0, le=10)
    job_runtime_seconds: float = Field(default=3_600.0, gt=0, le=86_400)
    model_concurrency: int = Field(default=1, ge=1, le=32)
    model_retries: int = Field(default=2, ge=0, le=10)
    calls_per_job: int = Field(default=500, ge=1, le=10_000)
    maximum_estimated_cost: Decimal | None = Field(default=None, gt=0)
    confirmation_cost_threshold: Decimal | None = Field(default=None, ge=0)
    retained_jobs: int | None = Field(default=None, ge=1, le=1_000_000)
    retention_days: int | None = Field(default=None, ge=1, le=3_650)

    @model_validator(mode="after")
    def validate_related_limits(self) -> ResourceLimits:
        if self.workbook_expanded_bytes < self.workbook_bytes:
            raise ValueError("workbook_expanded_bytes must be at least workbook_bytes")
        if self.zip_expanded_bytes < self.zip_bytes:
            raise ValueError("zip_expanded_bytes must be at least zip_bytes")
        if self.total_upload_bytes < self.image_bytes:
            raise ValueError("total_upload_bytes must be at least image_bytes")
        if (
            self.maximum_estimated_cost is not None
            and self.confirmation_cost_threshold is not None
            and self.confirmation_cost_threshold > self.maximum_estimated_cost
        ):
            raise ValueError(
                "confirmation_cost_threshold must not exceed maximum_estimated_cost"
            )
        return self

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ResourceLimits:
        source = os.environ if environ is None else environ
        names = {
            field: f"FASHION_CMS_{field.upper()}" for field in cls.model_fields
        }
        values: dict[str, object] = {}
        for field, name in names.items():
            raw = source.get(name)
            if raw is None or not raw.strip():
                continue
            values[field] = raw
        return cls.model_validate(values)

    def health_rows(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "Limit": name,
                "Value": "disabled — user approval required" if value is None else str(value),
            }
            for name, value in self.model_dump().items()
        )


class ModelPricing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str = Field(min_length=1, max_length=200)
    currency: str = Field(min_length=3, max_length=3)
    effective_date: date
    source: str = Field(min_length=1, max_length=1_000)
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    image_pricing_method: str = Field(pattern="^(NONE|PER_IMAGE)$")
    image_rate: Decimal | None = Field(default=None, ge=0)
    maximum_input_tokens_per_request: int | None = Field(default=None, ge=1)
    maximum_output_tokens_per_request: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_image_pricing(self) -> ModelPricing:
        if (self.image_pricing_method == "PER_IMAGE") != (self.image_rate is not None):
            raise ValueError("PER_IMAGE pricing requires image_rate; NONE forbids it")
        return self


class PricingConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1, max_length=100)
    approval_status: str = Field(pattern="^(APPROVED|PENDING|REJECTED|SUPERSEDED)$")
    models: tuple[ModelPricing, ...] = ()

    @model_validator(mode="after")
    def unique_models(self) -> PricingConfiguration:
        identifiers = [model.model_id for model in self.models]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Pricing model IDs must be unique")
        return self

    def for_model(self, model_id: str) -> ModelPricing | None:
        if self.approval_status != "APPROVED":
            return None
        return next((model for model in self.models if model.model_id == model_id), None)


def load_pricing(path: str | Path) -> PricingConfiguration:
    return PricingConfiguration.model_validate_json(Path(path).read_text(encoding="utf-8"))


def usage_cost(
    pricing: ModelPricing | None,
    usage: Mapping[str, object],
    *,
    image_count: int = 0,
) -> Decimal | None:
    if pricing is None:
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    cost = (
        Decimal(input_tokens) * pricing.input_per_million
        + Decimal(output_tokens) * pricing.output_per_million
    ) / Decimal(1_000_000)
    if pricing.image_pricing_method == "PER_IMAGE":
        assert pricing.image_rate is not None
        cost += Decimal(image_count) * pricing.image_rate
    return cost


def maximum_job_cost(
    pricing: ModelPricing | None,
    *,
    request_count: int,
    image_count: int,
) -> Decimal | None:
    if request_count < 0 or image_count < 0:
        raise ValueError("Request and image counts must be non-negative")
    if (
        pricing is None
        or pricing.maximum_input_tokens_per_request is None
        or pricing.maximum_output_tokens_per_request is None
    ):
        return None
    return usage_cost(
        pricing,
        {
            "input_tokens": request_count * pricing.maximum_input_tokens_per_request,
            "output_tokens": request_count * pricing.maximum_output_tokens_per_request,
        },
        image_count=image_count,
    )


def load_json(path: str | Path) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a JSON object")
    return value
