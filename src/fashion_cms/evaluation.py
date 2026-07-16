from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from itertools import combinations
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fashion_cms.config import ModelPricing, usage_cost
from fashion_cms.models import AnalysisMode


class DatasetKind(StrEnum):
    ENGINEERING_FIXTURE = "ENGINEERING_FIXTURE"
    HUMAN_APPROVED_GOLDEN = "HUMAN_APPROVED_GOLDEN"


class ApprovalStatus(StrEnum):
    APPROVED = "APPROVED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ImageReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str = Field(min_length=1, max_length=1_000)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")


class VariantRelationship(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: str = Field(min_length=1, max_length=200)
    differing_headers: tuple[str, ...] = Field(min_length=1)


class GoldenCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1, max_length=200)
    attribute_set: str = Field(min_length=1, max_length=100)
    product_profile: str = Field(min_length=1, max_length=100)
    sku: str = Field(min_length=1, max_length=32_767)
    base_code: str | None = Field(default=None, max_length=32_767)
    analysis_mode: AnalysisMode
    input_description: str | None = Field(default=None, max_length=32_767)
    images: tuple[ImageReference, ...] = ()
    expected_values: dict[str, str] = Field(default_factory=dict)
    unknown_fields: tuple[str, ...] = ()
    expected_blank_fields: tuple[str, ...] = ()
    expected_evidence_policy: dict[str, str] = Field(default_factory=dict)
    variant_relationship: VariantRelationship | None = None
    scenario_tags: tuple[str, ...] = ()
    annotator_status: str = Field(min_length=1, max_length=100)
    reviewer_status: str = Field(min_length=1, max_length=100)
    approval_status: ApprovalStatus

    @model_validator(mode="after")
    def disjoint_expectations(self) -> GoldenCase:
        groups = (
            set(self.expected_values),
            set(self.unknown_fields),
            set(self.expected_blank_fields),
        )
        if any(first & second for first, second in combinations(groups, 2)):
            raise ValueError("Expected, unknown, and blank fields must be disjoint")
        return self


class GoldenDataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_version: str = Field(min_length=1, max_length=100)
    dataset_kind: DatasetKind
    approval_status: ApprovalStatus
    cases: tuple[GoldenCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dataset(self) -> GoldenDataset:
        identifiers = [case.case_id for case in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Golden case IDs must be unique")
        if self.dataset_kind == DatasetKind.HUMAN_APPROVED_GOLDEN:
            if self.approval_status != ApprovalStatus.APPROVED or any(
                case.approval_status != ApprovalStatus.APPROVED for case in self.cases
            ):
                raise ValueError("Human-approved golden data requires every approval")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump_json(exclude_none=False)
        return hashlib.sha256(payload.encode()).hexdigest()


class EvaluationPrediction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    values: dict[str, str | None] = Field(default_factory=dict)
    conflict_fields: tuple[str, ...] = ()
    invalid_enum_fields: tuple[str, ...] = ()
    review_required_fields: tuple[str, ...] = ()
    unsupported_claim_fields: tuple[str, ...] = ()
    extraction_failed: bool = False
    latency_seconds: float = Field(default=0, ge=0)
    request_count: int = Field(default=0, ge=0)
    usage: dict[str, int] = Field(default_factory=dict)


class ModelEvaluationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str = Field(min_length=1, max_length=200)
    configuration: dict[str, object] = Field(default_factory=dict)
    prompt_version: str
    schema_version: str
    registry_version: str
    image_detail: str


class Metric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float | None
    numerator: float
    denominator: float
    sample_count: int = Field(ge=0)


class MetricGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: str
    key: str
    metrics: dict[str, Metric]


class ModelRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    config: ModelEvaluationConfig
    predictions: tuple[EvaluationPrediction, ...]


class EvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_version: str
    dataset_version: str
    dataset_fingerprint: str
    dataset_kind: DatasetKind
    dataset_approval_status: ApprovalStatus
    runs: tuple[ModelRun, ...]
    groups: tuple[MetricGroup, ...]

    @property
    def overall(self) -> MetricGroup:
        return next(group for group in self.groups if group.dimension == "overall")


class ThresholdPolicy(StrEnum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    EXPLICIT_INPUT_ONLY = "EXPLICIT_INPUT_ONLY"
    DISABLED = "DISABLED"


class FieldThreshold(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum_precision: float | None = Field(default=None, ge=0, le=1)
    minimum_coverage: float | None = Field(default=None, ge=0, le=1)
    maximum_unsupported_claim_rate: float | None = Field(default=None, ge=0, le=1)
    maximum_variant_leakage_rate: float | None = Field(default=None, ge=0, le=1)
    failure_policy: ThresholdPolicy = ThresholdPolicy.REVIEW_REQUIRED


class ThresholdConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    approval_status: ApprovalStatus
    default_policy: ThresholdPolicy
    fields: dict[str, FieldThreshold] = Field(default_factory=dict)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    header: str
    policy: ThresholdPolicy
    reasons: tuple[str, ...]


def load_dataset(path: str | Path) -> GoldenDataset:
    return GoldenDataset.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_thresholds(path: str | Path) -> ThresholdConfiguration:
    return ThresholdConfiguration.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _ratio(numerator: float, denominator: float, samples: int) -> Metric:
    return Metric(
        value=numerator / denominator if denominator else None,
        numerator=numerator,
        denominator=denominator,
        sample_count=samples,
    )


def _sum(value: float, samples: int) -> Metric:
    return Metric(value=value if samples else None, numerator=value, denominator=1, sample_count=samples)


def _metric_set(
    pairs: Sequence[tuple[GoldenCase, EvaluationPrediction, str, ModelPricing | None]],
    *,
    header: str | None = None,
) -> dict[str, Metric]:
    fields: list[tuple[GoldenCase, EvaluationPrediction, str, str | None, str]] = []
    for case, prediction, _, _ in pairs:
        annotated = {
            **{name: "expected" for name in case.expected_values},
            **{name: "unknown" for name in case.unknown_fields},
            **{name: "blank" for name in case.expected_blank_fields},
        }
        for name in set(annotated) | set(prediction.values):
            if header is None or header == name:
                fields.append((case, prediction, name, prediction.values.get(name), annotated.get(name, "unknown")))

    nonblank = [field for field in fields if field[3] not in {None, ""}]
    correct = sum(
        kind == "expected" and value == case.expected_values.get(name)
        for case, _, name, value, kind in nonblank
    )
    expected = [field for field in fields if field[4] == "expected"]
    covered = sum(field[3] not in {None, ""} for field in expected)
    blanks = sum(field[3] in {None, ""} for field in fields)
    conflicts = sum(name in prediction.conflict_fields for _, prediction, name, _, _ in fields)
    invalid = sum(name in prediction.invalid_enum_fields for _, prediction, name, _, _ in fields)
    reviews = sum(name in prediction.review_required_fields for _, prediction, name, _, _ in fields)
    unsupported_fields = [field for field in fields if field[4] in {"unknown", "blank"}]
    unsupported = sum(
        value not in {None, ""} or name in prediction.unsupported_claim_fields
        for _, prediction, name, value, _ in unsupported_fields
    )

    leakage_numerator = 0
    leakage_denominator = 0
    grouped: dict[tuple[str, str], list[tuple[GoldenCase, EvaluationPrediction]]] = defaultdict(list)
    for case, prediction, model_id, _ in pairs:
        if case.variant_relationship is not None:
            grouped[(model_id, case.variant_relationship.group_id)].append((case, prediction))
    for related in grouped.values():
        for (first, first_prediction), (second, second_prediction) in combinations(related, 2):
            relationship = first.variant_relationship
            assert relationship is not None
            for name in relationship.differing_headers:
                if header is not None and header != name:
                    continue
                first_expected = first.expected_values.get(name)
                second_expected = second.expected_values.get(name)
                if first_expected is None or second_expected is None or first_expected == second_expected:
                    continue
                leakage_denominator += 1
                first_value = first_prediction.values.get(name)
                second_value = second_prediction.values.get(name)
                leakage_numerator += first_value not in {None, ""} and first_value == second_value

    cases = {(model_id, case.case_id): (case, prediction) for case, prediction, model_id, _ in pairs}
    failures = sum(prediction.extraction_failed for _, prediction in cases.values())
    latency = sum(prediction.latency_seconds for _, prediction in cases.values())
    requests = sum(prediction.request_count for _, prediction in cases.values())
    bases = {
        (model_id, case.base_code or case.sku)
        for case, _, model_id, _ in pairs
    }
    usage_samples = [prediction for _, prediction in cases.values() if prediction.usage]
    input_tokens = sum(item.usage.get("input_tokens", 0) for item in usage_samples)
    output_tokens = sum(item.usage.get("output_tokens", 0) for item in usage_samples)
    priced = [
        usage_cost(pricing, prediction.usage, image_count=len(case.images))
        for case, prediction, _, pricing in pairs
        if prediction.usage
    ]
    valid_costs = [cost for cost in priced if cost is not None]
    total_cost = float(sum(valid_costs)) if valid_costs else 0

    return {
        "precision": _ratio(correct, len(nonblank), len(nonblank)),
        "coverage": _ratio(covered, len(expected), len(expected)),
        "blank_rate": _ratio(blanks, len(fields), len(fields)),
        "conflict_rate": _ratio(conflicts, len(fields), len(fields)),
        "invalid_enum_rate": _ratio(invalid, len(nonblank), len(nonblank)),
        "review_required_rate": _ratio(reviews, len(fields), len(fields)),
        "unsupported_claim_rate": _ratio(
            unsupported, len(unsupported_fields), len(unsupported_fields)
        ),
        "variant_leakage_rate": _ratio(
            leakage_numerator, leakage_denominator, leakage_denominator
        ),
        "extraction_failure_rate": _ratio(failures, len(cases), len(cases)),
        "latency_per_request_seconds": _ratio(latency, requests, requests),
        "latency_per_sku_seconds": _ratio(latency, len(cases), len(cases)),
        "request_count_per_sku": _ratio(requests, len(cases), len(cases)),
        "request_count_per_base_code": _ratio(requests, len(bases), len(bases)),
        "input_tokens": _sum(input_tokens, len(usage_samples)),
        "output_tokens": _sum(output_tokens, len(usage_samples)),
        "cost": _sum(total_cost, len(valid_costs)),
    }


def compare_models(
    dataset: GoldenDataset,
    configurations: Sequence[ModelEvaluationConfig],
    runner: Callable[[ModelEvaluationConfig, GoldenCase], EvaluationPrediction],
    *,
    pricing: Mapping[str, ModelPricing] | None = None,
) -> EvaluationReport:
    configs = tuple(configurations)
    if len(configs) < 2 or len({config.model_id for config in configs}) != len(configs):
        raise ValueError("Model comparison requires at least two distinct configured model IDs")
    runs = []
    pairs = []
    for config in configs:
        predictions = tuple(runner(config, case) for case in dataset.cases)
        if [prediction.case_id for prediction in predictions] != [
            case.case_id for case in dataset.cases
        ]:
            raise ValueError("Every model must evaluate the same frozen cases in order")
        runs.append(ModelRun(config=config, predictions=predictions))
        pairs.extend(
            (
                case,
                prediction,
                config.model_id,
                (pricing or {}).get(config.model_id),
            )
            for case, prediction in zip(dataset.cases, predictions, strict=True)
        )

    groups = [MetricGroup(dimension="overall", key="all", metrics=_metric_set(pairs))]
    dimensions: tuple[tuple[str, Callable[[GoldenCase, str], str]], ...] = (
        ("attribute_set", lambda case, _: case.attribute_set),
        ("product_profile", lambda case, _: case.product_profile),
        ("model", lambda _, model_id: model_id),
        ("analysis_mode", lambda case, _: case.analysis_mode.value),
    )
    for dimension, key_for in dimensions:
        keys = sorted({key_for(case, model_id) for case, _, model_id, _ in pairs})
        for key in keys:
            selected = [
                pair for pair in pairs if key_for(pair[0], pair[2]) == key
            ]
            groups.append(MetricGroup(dimension=dimension, key=key, metrics=_metric_set(selected)))
    headers = sorted(
        {
            header
            for case in dataset.cases
            for header in (
                set(case.expected_values)
                | set(case.unknown_fields)
                | set(case.expected_blank_fields)
            )
        }
    )
    groups.extend(
        MetricGroup(
            dimension="attribute_header",
            key=header,
            metrics=_metric_set(pairs, header=header),
        )
        for header in headers
    )
    return EvaluationReport(
        report_version="1",
        dataset_version=dataset.dataset_version,
        dataset_fingerprint=dataset.fingerprint,
        dataset_kind=dataset.dataset_kind,
        dataset_approval_status=dataset.approval_status,
        runs=tuple(runs),
        groups=tuple(groups),
    )


def route_threshold_policies(
    report: EvaluationReport,
    configuration: ThresholdConfiguration,
) -> tuple[PolicyDecision, ...]:
    per_header = {
        group.key: group.metrics
        for group in report.groups
        if group.dimension == "attribute_header"
    }
    decisions = []
    for header, metrics in per_header.items():
        rule = configuration.fields.get(header)
        if configuration.approval_status != ApprovalStatus.APPROVED:
            decisions.append(
                PolicyDecision(
                    header=header,
                    policy=configuration.default_policy,
                    reasons=("Evaluation thresholds are not user-approved.",),
                )
            )
            continue
        if rule is None:
            decisions.append(
                PolicyDecision(
                    header=header,
                    policy=configuration.default_policy,
                    reasons=("No approved field threshold is configured.",),
                )
            )
            continue
        checks = (
            ("precision", rule.minimum_precision, False),
            ("coverage", rule.minimum_coverage, False),
            ("unsupported_claim_rate", rule.maximum_unsupported_claim_rate, True),
            ("variant_leakage_rate", rule.maximum_variant_leakage_rate, True),
        )
        reasons = []
        for metric_name, threshold, maximum in checks:
            if threshold is None:
                continue
            value = metrics[metric_name].value
            if value is None:
                reasons.append(f"{metric_name} has no evaluable samples.")
            elif (value > threshold if maximum else value < threshold):
                reasons.append(f"{metric_name}={value:.4f} failed {threshold:.4f}.")
        if reasons:
            decisions.append(
                PolicyDecision(header=header, policy=rule.failure_policy, reasons=tuple(reasons))
            )
    return tuple(decisions)


def engineering_echo_prediction(
    config: ModelEvaluationConfig, case: GoldenCase
) -> EvaluationPrediction:
    """Deterministic framework check; never accuracy evidence for a live model."""
    return EvaluationPrediction(
        case_id=case.case_id,
        values={
            **case.expected_values,
            **{header: None for header in (*case.unknown_fields, *case.expected_blank_fields)},
        },
        review_required_fields=tuple(case.expected_values),
        latency_seconds=0.01,
        request_count=1,
        usage={"input_tokens": 0, "output_tokens": 0},
    )


def report_json(report: EvaluationReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
