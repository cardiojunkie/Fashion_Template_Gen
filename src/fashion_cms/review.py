from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fashion_cms.database import JobDatabase, WorkItemRecord
from fashion_cms.models import InputRow, MAX_EXCEL_CELL_CHARACTERS
from fashion_cms.normalization import MatchMethod, normalize_attribute_value
from fashion_cms.registry import DataType, Registry, normalize_value
from fashion_cms.topwear_extraction import (
    AttributeObservation,
    Confidence,
    EvidenceType,
    ExtractionRecord,
    applicable_attribute_headers,
    structured_input_values,
)
from fashion_cms.variant_service import VariantGroup, extract_labeled_values


class ReviewAction(StrEnum):
    ACCEPT = "Accept"
    EDIT = "Edit"
    BLANK = "Blank"
    REJECT = "Reject"


class SourcePriority(IntEnum):
    STRUCTURED_INPUT = 2
    INPUT_DATA = 3
    LABEL_TEXT = 4
    IMAGE = 5
    BUSINESS_RULE = 6
    BLANK = 7


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    CONFLICT = "conflict"
    UNMAPPED = "unmapped"
    UNKNOWN = "unknown"


class ReviewDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    sku: str
    header: str
    original_proposal: str | None
    final_value: str | None
    action: ReviewAction
    reviewer_note: str | None = Field(default=None, max_length=1_000)
    reviewed_at: datetime
    registry_version: str
    prompt_version: str
    schema_version: str
    model: str
    evidence_reference: str | None = Field(default=None, max_length=2_000)


class ReviewItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    sku: str
    base_code: str | None
    product_profile: str | None
    header: str
    supplied_value: str | None
    raw_value: str | None
    normalized_value: str
    proposed_value: str | None
    matching_method: MatchMethod
    alias_used: str | None
    fuzzy_suggestion: str | None
    fuzzy_score: float | None
    evidence_type: str
    evidence_references: tuple[str, ...]
    confidence: Confidence | None
    source_priority: SourcePriority
    conflict: str | None
    warning: str | None
    proposal_status: ProposalStatus
    image_inferred_color: bool
    requires_review: bool
    review_action: ReviewAction | None = None
    final_value: str | None = None
    reviewer_note: str | None = None
    reviewed_at: datetime | None = None
    decision_valid: bool = True
    registry_version: str
    prompt_version: str
    schema_version: str
    model: str

    @property
    def safe_for_bulk_accept(self) -> bool:
        return bool(
            self.review_action is None
            and self.proposed_value
            and not self.conflict
            and not self.warning
            and not self.image_inferred_color
            and self.confidence in {Confidence.HIGH, Confidence.MEDIUM}
            and self.source_priority
            in {SourcePriority.STRUCTURED_INPUT, SourcePriority.INPUT_DATA}
            and self.matching_method != MatchMethod.FUZZY_SUGGESTION
        )


class _Candidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    header: str
    raw_value: str | None
    canonical_value: str | None = None
    evidence_type: str
    evidence_references: tuple[str, ...]
    confidence: Confidence | None
    priority: SourcePriority
    note: str | None = None
    blocked: bool = False


_LABEL_HEADERS = {
    "color": "attributes__color",
    "size": "attributes__size",
    "pattern": "attributes__pattern",
    "product_type": "attributes__product_type",
    "model_code": "attributes__model",
    "design": "attributes__design",
    "sleeve": "attributes__sleeve_length",
    "neckline": "attributes__neckline",
    "closure": "attributes__closure",
    "finish": "attributes__finish",
}
_OVERLAPPING_HEADERS = (
    ("attributes__fit", "attributes__fit_type"),
    ("attributes__pattern", "attributes__pattern_type"),
    ("attributes__closure", "attributes__fastening_type"),
    ("attributes__occasion", "attributes__occasion_type"),
    ("attributes__material", "attributes__fabric"),
    ("attributes__fabric_care", "attributes__care_instructions"),
    ("attributes__package_contents", "attributes__in_the_box"),
)
_PLACEHOLDERS = {
    "unknown",
    "n a",
    "not available",
    "not specified",
}


def _generic_definition(registry: Registry, header: str) -> bool:
    label = normalize_value(header.removeprefix("attributes__").replace("_", " "))
    description = normalize_value(registry.definitions_by_header[header].description)
    return description in {label, f"cms output field for {label}"}


def _structured_input_candidates(
    row: InputRow, registry: Registry, allowed_headers: set[str]
) -> tuple[_Candidate, ...]:
    candidates = []
    for header, raw in (
        structured_input_values(
            row.input_data, registry, allowed_headers
        )
        or {}
    ).items():
        candidates.append(
            _Candidate(
                header=header,
                raw_value=raw,
                evidence_type="structured_input",
                evidence_references=(f"input:{row.sku}:{header}",),
                confidence=Confidence.HIGH,
                priority=SourcePriority.STRUCTURED_INPUT,
            )
        )
    return tuple(candidates)


def _input_data_candidates(
    row: InputRow, allowed_headers: set[str]
) -> tuple[_Candidate, ...]:
    candidates = []
    for label, values in extract_labeled_values(row.input_data).items():
        header = _LABEL_HEADERS.get(label)
        if header not in allowed_headers:
            continue
        for value in values:
            candidates.append(
                _Candidate(
                    header=header,
                    raw_value=value,
                    evidence_type="input_data",
                    evidence_references=(f"input:{row.sku}",),
                    confidence=Confidence.HIGH,
                    priority=SourcePriority.INPUT_DATA,
                )
            )
    return tuple(candidates)


def _candidate_from_observation(observation: AttributeObservation) -> _Candidate:
    priorities = {
        EvidenceType.INPUT: SourcePriority.INPUT_DATA,
        EvidenceType.LABEL_TEXT: SourcePriority.LABEL_TEXT,
        EvidenceType.IMAGE: SourcePriority.IMAGE,
        EvidenceType.BUSINESS_RULE: SourcePriority.BUSINESS_RULE,
        EvidenceType.NONE: SourcePriority.BLANK,
    }
    return _Candidate(
        header=observation.header,
        raw_value=observation.raw_value,
        canonical_value=observation.canonical_value,
        evidence_type=observation.evidence_type.value,
        evidence_references=observation.evidence_refs,
        confidence=observation.confidence,
        priority=priorities[observation.evidence_type],
        note=observation.note,
    )


def _shared_safe(header: str, group: VariantGroup | None) -> bool:
    if group is None:
        return True
    if any(
        warning.startswith(
            (
                "Product profile conflict",
                "Descriptions differ beyond",
                "Multiple pack counts",
            )
        )
        for warning in group.size_only_warnings
    ):
        return False
    if header == "attributes__color" and len(group.detected_colors) > 1:
        return False
    if header in {"attributes__pattern", "attributes__pattern_type"} and len(
        group.detected_patterns
    ) > 1:
        return False
    if header == "attributes__product_type" and len(group.detected_product_types) > 1:
        return False
    labels = {
        "attributes__design": "Multiple design values",
        "attributes__sleeve_length": "Multiple sleeve values",
        "attributes__neckline": "Multiple neckline values",
        "attributes__closure": "Multiple closure values",
        "attributes__fastening_type": "Multiple closure values",
        "attributes__finish": "Multiple finish values",
    }
    prefix = labels.get(header)
    return prefix is None or not any(
        warning.startswith(prefix) for warning in group.size_only_warnings
    )


def _proposal(
    *,
    job_id: str,
    row: InputRow,
    header: str,
    candidates: Sequence[_Candidate],
    registry: Registry,
    product_profile: str | None,
    prompt_version: str,
    schema_version: str,
    model: str,
) -> ReviewItem:
    ranked = sorted(candidates, key=lambda candidate: (candidate.priority, candidate.blocked))
    valued = [candidate for candidate in ranked if candidate.raw_value or candidate.canonical_value]
    selected = valued[0] if valued else ranked[0]
    raw = selected.raw_value or selected.canonical_value
    normalized = normalize_attribute_value(registry, header, raw)
    values = {
        normalize_value(
            normalize_attribute_value(
                registry,
                header,
                candidate.raw_value or candidate.canonical_value,
            ).canonical_value
            or candidate.canonical_value
            or candidate.raw_value
            or ""
        )
        for candidate in valued
        if candidate.canonical_value or candidate.raw_value
    }
    conflict = None
    if len(values) > 1:
        conflict = "Conflicting sources retained for reviewer resolution."
    notes = tuple(
        dict.fromkeys(
            candidate.note
            for candidate in ranked
            if candidate.note and candidate.raw_value is not None
        )
    )
    warning = " ".join(notes) or None
    proposed = None if selected.blocked else normalized.canonical_value
    status = (
        ProposalStatus.CONFLICT
        if conflict
        else ProposalStatus.UNMAPPED
        if raw and proposed is None
        else ProposalStatus.PROPOSED
        if proposed is not None
        else ProposalStatus.UNKNOWN
    )
    supplied = next(
        (
            candidate.raw_value or candidate.canonical_value
            for candidate in ranked
            if candidate.priority
            in {SourcePriority.STRUCTURED_INPUT, SourcePriority.INPUT_DATA}
            and (candidate.raw_value or candidate.canonical_value)
        ),
        None,
    )
    image_color = bool(
        header == "attributes__color"
        and selected.priority == SourcePriority.IMAGE
        and proposed
    )
    return ReviewItem(
        job_id=job_id,
        sku=row.sku,
        base_code=row.base_code,
        product_profile=product_profile,
        header=header,
        supplied_value=supplied,
        raw_value=raw,
        normalized_value=normalized.normalized_value,
        proposed_value=proposed,
        matching_method=normalized.method,
        alias_used=normalized.alias_used,
        fuzzy_suggestion=normalized.fuzzy_suggestion,
        fuzzy_score=normalized.fuzzy_score,
        evidence_type=selected.evidence_type,
        evidence_references=selected.evidence_references,
        confidence=selected.confidence,
        source_priority=selected.priority,
        conflict=conflict,
        warning=warning,
        proposal_status=status,
        image_inferred_color=image_color,
        requires_review=bool(raw or proposed or conflict or image_color),
        registry_version=registry.fingerprint,
        prompt_version=prompt_version,
        schema_version=schema_version,
        model=model,
    )


def validate_final_value(registry: Registry, header: str, value: str) -> str:
    final = value.strip()
    if not final:
        raise ValueError("Final value cannot be empty; use the Blank action.")
    if len(final) > MAX_EXCEL_CELL_CHARACTERS:
        raise ValueError("Final value exceeds the Excel cell character limit.")
    if normalize_value(final) in _PLACEHOLDERS:
        raise ValueError("Placeholder values must remain blank.")
    definition = registry.definitions_by_header[header]
    configured_format = definition.unit_or_format or ""
    if definition.data_type == DataType.ENUM:
        if final not in registry.permitted_values_by_header[header]:
            raise ValueError("Enum edits must use an active permitted value.")
    elif definition.data_type in {DataType.INTEGER, DataType.DECIMAL}:
        numeric_text = final
        if configured_format.startswith("unit:"):
            unit = configured_format.removeprefix("unit:").strip()
            match = re.fullmatch(
                rf"([+-]?\d+(?:\.\d+)?)\s*{re.escape(unit)}", final
            )
            if match is None:
                raise ValueError("Final value does not use the configured unit.")
            numeric_text = match.group(1)
        try:
            number = Decimal(numeric_text)
        except InvalidOperation as exc:
            raise ValueError("Final value must be numeric.") from exc
        if not number.is_finite() or (
            definition.data_type == DataType.INTEGER and number != number.to_integral()
        ):
            raise ValueError("Final value has an invalid numeric format.")
    elif definition.data_type == DataType.BOOLEAN and normalize_value(final) not in {
        "true",
        "false",
    }:
        raise ValueError("Final value must be true or false.")
    if configured_format.startswith("regex:") and re.fullmatch(
        configured_format.removeprefix("regex:"), final
    ) is None:
        raise ValueError("Final value does not match the configured format.")
    return final


def make_review_decision(
    item: ReviewItem,
    action: ReviewAction | str,
    registry: Registry,
    *,
    final_value: str | None = None,
    reviewer_note: str | None = None,
) -> ReviewDecision:
    selected_action = ReviewAction(action)
    note = reviewer_note.strip() or None if reviewer_note is not None else None
    if selected_action == ReviewAction.ACCEPT:
        if item.proposed_value is None:
            raise ValueError("An unmapped proposal cannot be accepted.")
        final = validate_final_value(registry, item.header, item.proposed_value)
    elif selected_action == ReviewAction.EDIT:
        if final_value is None:
            raise ValueError("Edit requires a final value.")
        final = validate_final_value(registry, item.header, final_value)
    else:
        final = None
    return ReviewDecision(
        job_id=item.job_id,
        sku=item.sku,
        header=item.header,
        original_proposal=item.proposed_value,
        final_value=final,
        action=selected_action,
        reviewer_note=note,
        reviewed_at=datetime.now(UTC),
        registry_version=registry.fingerprint,
        prompt_version=item.prompt_version,
        schema_version=item.schema_version,
        model=item.model,
        evidence_reference=", ".join(item.evidence_references) or None,
    )


def _apply_decision(
    item: ReviewItem,
    raw_decision: Mapping[str, object] | None,
    registry: Registry,
) -> ReviewItem:
    if raw_decision is None:
        return item
    try:
        decision = ReviewDecision.model_validate(raw_decision)
    except ValidationError:
        return item.model_copy(
            update={
                "decision_valid": False,
                "warning": " ".join(
                    filter(None, (item.warning, "Stored review decision is no longer valid."))
                ),
            }
        )
    valid = (
        decision.job_id == item.job_id
        and decision.sku == item.sku
        and decision.header == item.header
        and decision.original_proposal == item.proposed_value
    )
    if decision.action == ReviewAction.ACCEPT:
        valid = valid and decision.final_value == item.proposed_value
    elif decision.action in {ReviewAction.BLANK, ReviewAction.REJECT}:
        valid = valid and decision.final_value is None
    else:
        valid = valid and decision.final_value is not None
    if decision.final_value is not None:
        try:
            validate_final_value(registry, item.header, decision.final_value)
        except ValueError:
            valid = False
    warning = item.warning
    if not valid:
        warning = " ".join(
            filter(None, (warning, "Stored review decision no longer matches the proposal."))
        )
    return item.model_copy(
        update={
            "review_action": decision.action,
            "final_value": decision.final_value,
            "reviewer_note": decision.reviewer_note,
            "reviewed_at": decision.reviewed_at,
            "decision_valid": valid,
            "warning": warning,
        }
    )


def build_review_items(
    *,
    job_id: str,
    rows: Sequence[InputRow],
    records: Sequence[tuple[WorkItemRecord, ExtractionRecord]],
    registry: Registry,
    groups: Sequence[VariantGroup] = (),
    decisions: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    attribute_set_id: str | None = None,
    product_profile: str | None = None,
) -> tuple[ReviewItem, ...]:
    if not records:
        return ()
    metadata = records[0][1]
    selected_set = attribute_set_id or metadata.vision_result.attribute_set_id
    selected_profile = product_profile or metadata.vision_result.product_profile
    if selected_profile is None:
        raise ValueError("A product profile is required for review.")
    if any(
        record.vision_result.attribute_set_id != selected_set
        or record.vision_result.product_profile != selected_profile
        for _, record in records
    ):
        raise ValueError("Extraction records do not share one attribute set and profile.")
    headers = applicable_attribute_headers(
        registry, selected_set, selected_profile
    )
    allowed_headers = set(headers)
    candidates: dict[tuple[str, str], list[_Candidate]] = defaultdict(list)
    extraction_conflicts: dict[tuple[str, str], list[str]] = defaultdict(list)
    group_by_key = {group.key: group for group in groups}
    for row in rows:
        structured = _structured_input_candidates(row, registry, allowed_headers)
        structured_headers = {candidate.header for candidate in structured}
        row_candidates = (
            *structured,
            *(
                candidate
                for candidate in _input_data_candidates(row, allowed_headers)
                if candidate.header not in structured_headers
            ),
        )
        for candidate in row_candidates:
            candidates[(row.sku, candidate.header)].append(candidate)

    for item, record in records:
        group = group_by_key.get(item.group_key)
        for message in record.vision_result.conflicts:
            conflict_headers = [header for header in headers if header in message]
            if not conflict_headers and "color" in message.casefold():
                conflict_headers = ["attributes__color"]
            conflict_skus = [sku for sku in item.represented_skus if sku in message]
            for sku in conflict_skus or item.represented_skus:
                for header in conflict_headers:
                    extraction_conflicts[(sku, header)].append(message)
        for observation in record.vision_result.shared_attributes:
            for sku in item.represented_skus:
                candidate = _candidate_from_observation(observation)
                if not _shared_safe(observation.header, group):
                    candidate = candidate.model_copy(
                        update={
                            "canonical_value": None,
                            "note": "Shared visual value was not reused across differing variants.",
                            "blocked": True,
                        }
                    )
                candidates[(sku, observation.header)].append(candidate)
        for sku, observations in record.vision_result.sku_attributes.items():
            for observation in observations:
                candidates[(sku, observation.header)].append(
                    _candidate_from_observation(observation)
                )
    built = []
    for row in rows:
        for header in headers:
            values = candidates.get((row.sku, header))
            if not values:
                continue
            built.append(
                _proposal(
                    job_id=job_id,
                    row=row,
                    header=header,
                    candidates=values,
                    registry=registry,
                    product_profile=selected_profile,
                    prompt_version=metadata.vision_result.prompt_version,
                    schema_version=metadata.vision_result.schema_version,
                    model=metadata.vision_result.model,
                )
            )

    for index, item in enumerate(built):
        messages = extraction_conflicts.get((item.sku, item.header), ())
        if messages:
            conflict = " ".join(dict.fromkeys(messages))
            built[index] = item.model_copy(
                update={
                    "conflict": conflict,
                    "warning": " ".join(filter(None, (item.warning, conflict))),
                    "proposal_status": ProposalStatus.CONFLICT,
                    "requires_review": True,
                }
            )

    by_key = {(item.sku, item.header): index for index, item in enumerate(built)}
    for row in rows:
        for first, second in _OVERLAPPING_HEADERS:
            first_index = by_key.get((row.sku, first))
            second_index = by_key.get((row.sku, second))
            present = tuple(
                index
                for index in (first_index, second_index)
                if index is not None
                and (built[index].raw_value or built[index].proposed_value)
            )
            if present and any(_generic_definition(registry, header) for header in (first, second)):
                message = (
                    f"Registry definitions do not yet distinguish {first} from {second}; "
                    "review the proposed field before accepting it."
                )
                for index in present:
                    built[index] = built[index].model_copy(
                        update={
                            "warning": " ".join(
                                filter(None, (built[index].warning, message))
                            ),
                            "requires_review": True,
                        }
                    )
            if first_index is None or second_index is None:
                continue
            first_item, second_item = built[first_index], built[second_index]
            if not first_item.proposed_value or normalize_value(
                first_item.proposed_value
            ) != normalize_value(second_item.proposed_value or ""):
                continue
            message = (
                f"The same value was proposed for overlapping fields {first} and {second}; "
                "confirm the semantic distinction."
            )
            for index in (first_index, second_index):
                built[index] = built[index].model_copy(
                    update={
                        "conflict": message,
                        "warning": " ".join(
                            filter(None, (built[index].warning, message))
                        ),
                        "proposal_status": ProposalStatus.CONFLICT,
                        "requires_review": True,
                    }
                )

    stored = decisions or {}
    return tuple(
        _apply_decision(item, stored.get((item.sku, item.header)), registry)
        for item in built
    )


def load_review_items(
    database: JobDatabase, job_id: str, registry: Registry
) -> tuple[ReviewItem, ...]:
    job = database.get_job(job_id)
    records = []
    for item in database.list_work_items(job_id):
        result = database.get_work_item_result(item)
        if result is None:
            continue
        try:
            records.append((item, ExtractionRecord.model_validate(result)))
        except ValidationError:
            continue
    return build_review_items(
        job_id=job_id,
        rows=database.load_rows(job_id),
        records=records,
        registry=registry,
        groups=database.load_groups(job_id),
        decisions=database.load_review_decisions(job_id),
        attribute_set_id=job.attribute_set,
        product_profile=job.product_profile,
    )


def persist_review_decision(
    database: JobDatabase,
    item: ReviewItem,
    action: ReviewAction | str,
    registry: Registry,
    *,
    final_value: str | None = None,
    reviewer_note: str | None = None,
) -> ReviewDecision:
    decision = make_review_decision(
        item,
        action,
        registry,
        final_value=final_value,
        reviewer_note=reviewer_note,
    )
    database.save_review_decision(
        decision.job_id,
        decision.sku,
        decision.header,
        decision.model_dump(mode="json"),
    )
    return decision


def bulk_accept_safe(
    database: JobDatabase, items: Sequence[ReviewItem], registry: Registry
) -> int:
    safe = [item for item in items if item.safe_for_bulk_accept]
    for item in safe:
        persist_review_decision(database, item, ReviewAction.ACCEPT, registry)
    return len(safe)


def unresolved_review_items(items: Sequence[ReviewItem]) -> tuple[ReviewItem, ...]:
    return tuple(
        item
        for item in items
        if item.requires_review
        and (item.review_action is None or not item.decision_valid)
    )


def accepted_facts(items: Sequence[ReviewItem]) -> dict[str, dict[str, str]]:
    facts: dict[str, dict[str, str]] = defaultdict(dict)
    for item in items:
        if (
            item.decision_valid
            and item.review_action in {ReviewAction.ACCEPT, ReviewAction.EDIT}
            and item.final_value
        ):
            facts[item.sku][item.header] = item.final_value
    return dict(facts)


def derive_topwear_occasion(
    facts: Mapping[str, str],
    registry: Registry,
    approved_rule_ids: Sequence[str] = (),
) -> tuple[dict[str, str], tuple[str, ...]]:
    product_type = normalize_value(facts.get("attributes__product_type", ""))
    pattern = normalize_value(
        " ".join(
            (
                facts.get("attributes__pattern", ""),
                facts.get("attributes__design", ""),
            )
        )
    )
    neckline = normalize_value(facts.get("attributes__neckline", ""))
    supported = (
        product_type in {"t shirt", "tee", "tshirt"}
        and "graphic" in pattern
        and "crew" in neckline
    )
    values = registry.permitted_values_by_header
    derived: dict[str, str] = {}
    warnings = []
    if "graphic_crew_tshirt_casual" in approved_rule_ids and supported:
        for header, value in (
            ("attributes__occasion", "Casual"),
            ("attributes__occasion_type", "Casual Wear"),
        ):
            if value in values.get(header, ()):
                derived[header] = value
            else:
                warnings.append(f"{value} is not an active permitted value for {header}.")
    if "basic_everyday" in approved_rule_ids and product_type and not derived:
        if "Everyday" in values.get("attributes__occasion", ()):
            derived["attributes__occasion"] = "Everyday"
        else:
            warnings.append("Everyday is not an active permitted occasion value.")
    return derived, tuple(warnings)
