from __future__ import annotations

from difflib import SequenceMatcher
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from fashion_cms.registry import DataType, Registry, normalize_value


class MatchMethod(StrEnum):
    EXACT_CANONICAL = "exact_canonical"
    NORMALIZED_CANONICAL = "normalized_canonical"
    APPROVED_ALIAS = "approved_alias"
    FUZZY_SUGGESTION = "fuzzy_suggestion"
    FREE_TEXT = "free_text"
    UNMAPPED = "unmapped"


class NormalizationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_value: str | None
    normalized_value: str
    canonical_value: str | None
    method: MatchMethod
    alias_used: str | None = None
    fuzzy_suggestion: str | None = None
    fuzzy_score: float | None = Field(default=None, ge=0, le=1)
    ambiguous: bool = False


def normalize_attribute_value(
    registry: Registry,
    header: str,
    raw_value: str | None,
    *,
    fuzzy_threshold: float = 0.72,
    ambiguity_margin: float = 0.03,
) -> NormalizationResult:
    if header not in registry.definitions_by_header:
        raise ValueError(f"Unknown registry header {header!r}.")
    raw = raw_value.strip() if raw_value is not None else ""
    comparison = normalize_value(raw)
    if not raw:
        return NormalizationResult(
            raw_value=None,
            normalized_value="",
            canonical_value=None,
            method=MatchMethod.UNMAPPED,
        )

    definition = registry.definitions_by_header[header]
    if definition.data_type != DataType.ENUM:
        return NormalizationResult(
            raw_value=raw,
            normalized_value=comparison,
            canonical_value=raw,
            method=MatchMethod.FREE_TEXT,
        )

    permitted = registry.permitted_values_by_header[header]
    if raw in permitted:
        return NormalizationResult(
            raw_value=raw,
            normalized_value=comparison,
            canonical_value=raw,
            method=MatchMethod.EXACT_CANONICAL,
        )
    canonical = {normalize_value(value): value for value in permitted}.get(comparison)
    if canonical is not None:
        return NormalizationResult(
            raw_value=raw,
            normalized_value=comparison,
            canonical_value=canonical,
            method=MatchMethod.NORMALIZED_CANONICAL,
        )
    alias = registry.aliases_by_header.get(header, {}).get(comparison)
    if alias is not None:
        return NormalizationResult(
            raw_value=raw,
            normalized_value=comparison,
            canonical_value=alias,
            method=MatchMethod.APPROVED_ALIAS,
            alias_used=raw,
        )

    scores = sorted(
        (
            (SequenceMatcher(None, comparison, normalize_value(value)).ratio(), value)
            for value in permitted
        ),
        reverse=True,
    )
    best_score, best = scores[0] if scores else (0.0, None)
    ambiguous = bool(
        best
        and len(scores) > 1
        and best_score - scores[1][0] <= ambiguity_margin
    )
    suggestion = best if best_score >= fuzzy_threshold and not ambiguous else None
    return NormalizationResult(
        raw_value=raw,
        normalized_value=comparison,
        canonical_value=None,
        method=(
            MatchMethod.FUZZY_SUGGESTION
            if suggestion is not None
            else MatchMethod.UNMAPPED
        ),
        fuzzy_suggestion=suggestion,
        fuzzy_score=round(best_score, 3) if best is not None else None,
        ambiguous=ambiguous,
    )
