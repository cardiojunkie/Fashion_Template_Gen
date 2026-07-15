from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_EXCEL_CELL_CHARACTERS = 32_767


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    severity: Severity
    code: str
    message: str
    location: str | None = None


class InputRow(BaseModel):
    schema_version: ClassVar[str] = "1"
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    row_number: int = Field(ge=2)
    sku: str = Field(min_length=1, max_length=MAX_EXCEL_CELL_CHARACTERS)
    base_code: str | None = Field(default=None, max_length=MAX_EXCEL_CELL_CHARACTERS)
    attributes__lulu_ean: str | None = Field(default=None, max_length=MAX_EXCEL_CELL_CHARACTERS)
    attributes__shipping_weight: str | int | float | None = None
    model_code_input_data: str | None = Field(default=None, max_length=MAX_EXCEL_CELL_CHARACTERS)

    @field_validator("sku", mode="before")
    @classmethod
    def validate_sku(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("must be stored as text")
        return value.strip()

    @field_validator("base_code", "attributes__lulu_ean", "model_code_input_data", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("must be stored as text")
        return value.strip() or None

    @field_validator("attributes__shipping_weight", mode="before")
    @classmethod
    def validate_shipping_weight(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            if len(value) > MAX_EXCEL_CELL_CHARACTERS:
                raise ValueError(f"must not exceed {MAX_EXCEL_CELL_CHARACTERS:,} characters")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a non-negative number")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("must be a non-negative number") from exc
        if not number.is_finite() or number < 0:
            raise ValueError("must be a non-negative number")
        return value

    @property
    def group_key(self) -> str:
        return self.base_code or self.sku


class WorkbookResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    rows: tuple[InputRow, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return bool(self.rows) and not any(
            issue.severity == Severity.CRITICAL for issue in self.issues
        )


class UploadedImage(BaseModel):
    schema_version: ClassVar[str] = "1"
    model_config = ConfigDict(frozen=True)

    source_name: str
    filename: str
    sku: str
    ordinal: int = Field(gt=0)
    image_format: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    content: bytes = Field(repr=False)


class ImageResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    images: tuple[UploadedImage, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return not any(issue.severity == Severity.CRITICAL for issue in self.issues)
