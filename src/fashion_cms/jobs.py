from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from fashion_cms.database import InvalidStateTransition, JobDatabase, JobRecord, WorkItemRecord
from fashion_cms.llm_service import sanitize_error
from fashion_cms.models import AnalysisMode, InputRow, JobStatus, UploadedImage, WorkItemStatus
from fashion_cms.variant_service import (
    CacheContext,
    ImageAsset,
    RequestPlan,
    build_request_plan,
    build_variant_groups,
)


PROMPT_VERSION = "phase4-fake-v1"
RESULT_SCHEMA_VERSION = "phase4-fake-result-v1"
MODEL_IDENTIFIER = "phase4-fake"
IMAGE_DETAIL = "auto"
MAX_ERROR_CHARACTERS = 1_000

Extractor = Callable[[WorkItemRecord], Mapping[str, Any]]
ResultValidator = Callable[
    [WorkItemRecord, Mapping[str, object]], Mapping[str, object]
]
ProgressCallback = Callable[[int, int, WorkItemRecord], None]


def fake_extract(item: WorkItemRecord) -> Mapping[str, Any]:
    """Return a deterministic orchestration result without making an API request."""
    return {
        "result_type": "FAKE_EXTRACTION",
        "schema_version": RESULT_SCHEMA_VERSION,
        "cache_key": item.cache_key,
        "analysis_mode": item.analysis_mode.value,
        "represented_skus": list(item.represented_skus),
        "representative_sku": item.representative_sku,
        "review_required": False,
    }


def _safe_error(exc: Exception) -> str:
    detail = sanitize_error(str(exc), (os.environ.get("OPENAI_API_KEY", ""),))
    message = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    return message[:MAX_ERROR_CHARACTERS]


def _safe_request_metadata(value: object, depth: int = 0) -> object:
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return sanitize_error(value, (os.environ.get("OPENAI_API_KEY", ""),))
    if isinstance(value, Mapping):
        return {
            sanitize_error(
                str(key), (os.environ.get("OPENAI_API_KEY", ""),)
            )[:100]: _safe_request_metadata(item, depth + 1)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_request_metadata(item, depth + 1) for item in value[:100]]
    return None


class JobService:
    def __init__(self, database: JobDatabase) -> None:
        self.database = database

    def create_job(
        self,
        rows: Sequence[InputRow],
        images: Sequence[UploadedImage | ImageAsset] = (),
        *,
        attribute_set: str,
        registry_version: str,
        product_profile: str | None = None,
        prompt_version: str = PROMPT_VERSION,
        schema_version: str = RESULT_SCHEMA_VERSION,
        model_identifier: str = MODEL_IDENTIFIER,
        image_detail: str = IMAGE_DETAIL,
        modes: Mapping[str, AnalysisMode | str] | None = None,
        representatives: Mapping[str, str] | None = None,
        job_type: str = "CMS_GENERATION",
    ) -> str:
        groups = build_variant_groups(
            rows,
            images,
            modes=modes,
            representatives=representatives,
        )
        context = CacheContext(
            attribute_set=attribute_set,
            product_profile=product_profile,
            registry_version=registry_version,
            prompt_version=prompt_version,
            schema_version=schema_version,
            model_identifier=model_identifier,
            image_detail=image_detail,
        )
        job_id = self.database.create_job(groups, context, job_type=job_type)
        self.database.transition_job(job_id, JobStatus.VALIDATING)
        self.database.transition_job(job_id, JobStatus.READY)
        self.plan_job(job_id)
        return job_id

    def plan_job(self, job_id: str) -> RequestPlan:
        job = self.database.get_job(job_id)
        if job.status != JobStatus.READY:
            raise InvalidStateTransition("Only a ready job can be planned.")
        plan = build_request_plan(self.database.load_groups(job_id), job.context)
        self.database.replace_work_items(job_id, plan.items)
        return plan

    def update_group(
        self,
        job_id: str,
        group_key: str,
        *,
        analysis_mode: AnalysisMode | str | None = None,
        representative_sku: str | None = None,
    ) -> RequestPlan:
        self.database.update_group(
            job_id,
            group_key,
            analysis_mode=analysis_mode,
            representative_sku=representative_sku,
        )
        return self.plan_job(job_id)

    def bulk_update_mode(
        self, job_id: str, analysis_mode: AnalysisMode | str
    ) -> RequestPlan:
        self.database.bulk_update_mode(job_id, analysis_mode)
        return self.plan_job(job_id)

    def run_job(
        self,
        job_id: str,
        extractor: Extractor = fake_extract,
        *,
        result_validator: ResultValidator | None = None,
        progress: ProgressCallback | None = None,
    ) -> JobRecord:
        job = self.database.get_job(job_id)
        if job.status == JobStatus.COMPLETED:
            return job
        if job.status != JobStatus.RUNNING:
            if job.status not in {
                JobStatus.READY,
                JobStatus.PARTIAL_FAILURE,
                JobStatus.FAILED,
                JobStatus.REVIEW_REQUIRED,
            }:
                raise InvalidStateTransition(
                    f"Job {job_id} cannot run from {job.status.value}."
                )
            self.database.transition_job(job_id, JobStatus.RUNNING)

        items = self.database.list_work_items(
            job_id,
            (WorkItemStatus.PENDING, WorkItemStatus.RUNNING),
        )
        for position, item in enumerate(items, start=1):
            self.database.mark_item_running(job_id, item.key)
            try:
                cached = self.database.get_cached_result(
                    item.cache_key, item.cache_payload_json
                )
                if cached is not None and result_validator is not None:
                    try:
                        cached = dict(result_validator(item, cached))
                    except Exception:
                        self.database.delete_cached_result(
                            item.cache_key, item.cache_payload_json
                        )
                        cached = None
                if cached is not None:
                    self.database.complete_item_with_result(
                        item,
                        cached,
                        cache_hit=True,
                        review_required=bool(cached.get("review_required")),
                    )
                    continue
                result = extractor(item)
                if not isinstance(result, Mapping):
                    raise TypeError("Fake extractor must return a mapping.")
                if result_validator is not None:
                    result = result_validator(item, result)
                self.database.complete_item_with_result(
                    item,
                    result,
                    cache_hit=False,
                    review_required=bool(result.get("review_required")),
                )
            except Exception as exc:
                metadata = getattr(exc, "request_metadata", None)
                error = _safe_error(exc)
                safe_metadata = (
                    _safe_request_metadata(metadata)
                    if isinstance(metadata, Mapping)
                    else None
                )
                if isinstance(safe_metadata, dict):
                    safe_metadata["error"] = error
                self.database.fail_item(
                    item,
                    error,
                    safe_metadata,
                )
            if progress is not None:
                progress(position, len(items), item)
        return self._finalize(job_id)

    def retry_failed_items(
        self,
        job_id: str,
        extractor: Extractor = fake_extract,
        *,
        result_validator: ResultValidator | None = None,
        progress: ProgressCallback | None = None,
    ) -> JobRecord:
        if not self.database.prepare_failed_retry(job_id):
            return self.database.get_job(job_id)
        return self.run_job(
            job_id,
            extractor,
            result_validator=result_validator,
            progress=progress,
        )

    def resume_job(
        self,
        job_id: str,
        extractor: Extractor = fake_extract,
        *,
        result_validator: ResultValidator | None = None,
        progress: ProgressCallback | None = None,
    ) -> JobRecord:
        job = self.database.get_job(job_id)
        if job.status == JobStatus.UPLOADED:
            job = self.database.transition_job(job_id, JobStatus.VALIDATING)
        if job.status == JobStatus.VALIDATING:
            job = self.database.transition_job(job_id, JobStatus.READY)
        if job.status == JobStatus.READY and not self.database.list_work_items(job_id):
            self.plan_job(job_id)
        return self.run_job(
            job_id,
            extractor,
            result_validator=result_validator,
            progress=progress,
        )

    def _finalize(self, job_id: str) -> JobRecord:
        items = self.database.list_work_items(job_id)
        counts = {
            status: sum(item.status == status for item in items) for status in WorkItemStatus
        }
        successful = (
            counts[WorkItemStatus.COMPLETED] + counts[WorkItemStatus.REVIEW_REQUIRED]
        )
        if counts[WorkItemStatus.FAILED] and successful:
            target = JobStatus.PARTIAL_FAILURE
        elif counts[WorkItemStatus.FAILED]:
            target = JobStatus.FAILED
        elif counts[WorkItemStatus.REVIEW_REQUIRED]:
            target = JobStatus.REVIEW_REQUIRED
        elif items and counts[WorkItemStatus.COMPLETED] == len(items):
            target = JobStatus.COMPLETED
        else:
            target = JobStatus.FAILED
        self.database.transition_job(job_id, target)
        return self.database.get_job(job_id)


def create_job(database: JobDatabase, *args: Any, **kwargs: Any) -> str:
    return JobService(database).create_job(*args, **kwargs)


def run_job(
    database: JobDatabase,
    job_id: str,
    extractor: Extractor = fake_extract,
) -> JobRecord:
    return JobService(database).run_job(job_id, extractor)


def retry_failed_items(
    database: JobDatabase,
    job_id: str,
    extractor: Extractor = fake_extract,
) -> JobRecord:
    return JobService(database).retry_failed_items(job_id, extractor)


def resume_job(
    database: JobDatabase,
    job_id: str,
    extractor: Extractor = fake_extract,
) -> JobRecord:
    return JobService(database).resume_job(job_id, extractor)
