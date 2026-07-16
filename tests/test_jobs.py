from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import threading
from typing import Any

import pytest

from fashion_cms.database import JobDatabase, WorkItemRecord
from fashion_cms.jobs import JobService, fake_extract
from fashion_cms.models import AnalysisMode, InputRow, JobStatus, WorkItemStatus
from fashion_cms.variant_service import ImageAsset


def row(
    sku: str,
    base_code: str | None = "BASE",
    description: str | None = None,
    row_number: int = 2,
) -> InputRow:
    return InputRow(
        row_number=row_number,
        sku=sku,
        base_code=base_code,
        input_data=description,
    )


def image(sku: str, digest: str = "a") -> ImageAsset:
    return ImageAsset(
        sku=sku,
        ordinal=1,
        filename=f"{sku}-1.jpg",
        sha256=digest * 64,
        width=100,
        height=100,
    )


def test_partial_failure_is_isolated_and_retry_does_not_repeat_success() -> None:
    database = JobDatabase(":memory:")
    service = JobService(database)
    job_id = service.create_job(
        (row("SKU-1"), row("SKU-2", row_number=3)),
        attribute_set="topwear",
        registry_version="registry-1",
    )
    first_calls: list[str] = []

    def fail_second(item: WorkItemRecord) -> Mapping[str, Any]:
        first_calls.append(item.representative_sku)
        if item.representative_sku == "SKU-2":
            raise RuntimeError("temporary extraction failure")
        return fake_extract(item)

    result = service.run_job(job_id, fail_second)
    first_items = database.list_work_items(job_id)
    successful_ref = first_items[0].result_ref

    assert result.status == JobStatus.PARTIAL_FAILURE
    assert first_calls == ["SKU-1", "SKU-2"]
    assert [item.status for item in first_items] == [
        WorkItemStatus.COMPLETED,
        WorkItemStatus.FAILED,
    ]
    assert first_items[1].error == "RuntimeError: temporary extraction failure"

    retry_calls: list[str] = []

    def succeed(item: WorkItemRecord) -> Mapping[str, Any]:
        retry_calls.append(item.representative_sku)
        return fake_extract(item)

    retried = service.retry_failed_items(job_id, succeed)
    final_items = database.list_work_items(job_id)

    assert retried.status == JobStatus.COMPLETED
    assert retry_calls == ["SKU-2"]
    assert [item.status for item in final_items] == [
        WorkItemStatus.COMPLETED,
        WorkItemStatus.COMPLETED,
    ]
    assert final_items[0].result_ref == successful_ref
    assert [item.retry_count for item in final_items] == [0, 1]

    summary = database.list_job_summaries()[0]
    assert summary.status == JobStatus.COMPLETED
    assert summary.planned_request_count == 2
    assert summary.completed_item_count == 2
    assert summary.failed_item_count == 0
    assert summary.review_required_count == 0


def test_identical_work_uses_cache_without_repeating_fake_extraction() -> None:
    database = JobDatabase(":memory:")
    service = JobService(database)
    rows = (row("SKU-1", description="Red shirt"),)
    assets = (image("SKU-1"),)
    first_job = service.create_job(
        rows,
        assets,
        attribute_set="topwear",
        registry_version="registry-1",
    )
    calls: list[str] = []

    def counted(item: WorkItemRecord) -> Mapping[str, Any]:
        calls.append(item.key)
        return fake_extract(item)

    service.run_job(first_job, counted)
    second_job = service.create_job(
        rows,
        assets,
        attribute_set="topwear",
        registry_version="registry-1",
    )
    service.run_job(second_job, counted)

    assert len(calls) == 1
    assert database.list_work_items(first_job)[0].cache_hit is False
    assert database.list_work_items(second_job)[0].cache_hit is True
    assert (
        database.list_work_items(second_job)[0].result_ref
        == database.list_work_items(first_job)[0].result_ref
    )


@pytest.mark.parametrize(
    "component",
    [
        "image",
        "registry",
        "mode",
        "prompt",
        "schema",
        "model",
    ],
)
def test_changed_execution_inputs_invalidate_persistent_cache(
    tmp_path: Path, component: str
) -> None:
    database = JobDatabase(tmp_path / f"{component}.sqlite3")
    service = JobService(database)
    rows = (row("SKU-1", description="Red shirt"),)
    assets = (image("SKU-1"),)
    baseline_options: dict[str, object] = {
        "attribute_set": "topwear",
        "registry_version": "registry-1",
        "prompt_version": "prompt-1",
        "schema_version": "schema-1",
        "model_identifier": "model-1",
    }
    baseline = service.create_job(rows, assets, **baseline_options)  # type: ignore[arg-type]
    service.run_job(baseline)

    changed_rows = rows
    changed_assets = assets
    changed_options = dict(baseline_options)
    if component == "image":
        changed_assets = (image("SKU-1", "b"),)
    elif component == "registry":
        changed_options["registry_version"] = "registry-2"
    elif component == "mode":
        changed_options["modes"] = {"base:BASE": AnalysisMode.BASE_CODE_SIZE_ONLY}
    elif component == "prompt":
        changed_options["prompt_version"] = "prompt-2"
    elif component == "schema":
        changed_options["schema_version"] = "schema-2"
    elif component == "model":
        changed_options["model_identifier"] = "model-2"

    changed_job = service.create_job(
        changed_rows,
        changed_assets,
        **changed_options,  # type: ignore[arg-type]
    )
    calls = 0

    def counted(item: WorkItemRecord) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return fake_extract(item)

    service.run_job(changed_job, counted)
    baseline_item = database.list_work_items(baseline)[0]
    changed_item = database.list_work_items(changed_job)[0]

    assert calls == 1, component
    assert changed_item.cache_hit is False
    assert changed_item.cache_key != baseline_item.cache_key


def test_interrupted_running_items_can_be_resumed() -> None:
    database = JobDatabase(":memory:")
    service = JobService(database)
    job_id = service.create_job(
        (row("SKU-1"), row("SKU-2", row_number=3)),
        attribute_set="topwear",
        registry_version="registry-1",
    )
    first_item = database.list_work_items(job_id)[0]
    database.transition_job(job_id, JobStatus.RUNNING)
    database.mark_item_running(job_id, first_item.key)
    calls: list[str] = []

    def counted(item: WorkItemRecord) -> Mapping[str, Any]:
        calls.append(item.representative_sku)
        return fake_extract(item)

    result = service.resume_job(job_id, counted)

    assert result.status == JobStatus.COMPLETED
    assert calls == ["SKU-1", "SKU-2"]
    assert all(
        item.status == WorkItemStatus.COMPLETED
        for item in database.list_work_items(job_id)
    )


def test_concurrent_run_does_not_submit_duplicate_work() -> None:
    database = JobDatabase(":memory:")
    service = JobService(database)
    job_id = service.create_job(
        (row("SKU-1"),),
        attribute_set="topwear",
        registry_version="registry-1",
    )
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    results: list[JobStatus] = []
    errors: list[BaseException] = []

    def blocking(item: WorkItemRecord) -> Mapping[str, Any]:
        calls.append(item.key)
        started.set()
        assert release.wait(5)
        return fake_extract(item)

    def run_first() -> None:
        try:
            results.append(service.run_job(job_id, blocking).status)
        except BaseException as exc:  # pragma: no cover - surfaced by the assertion below
            errors.append(exc)

    thread = threading.Thread(target=run_first)
    thread.start()
    try:
        assert started.wait(5)
        assert JobService(database).run_job(job_id, blocking).status == JobStatus.RUNNING
    finally:
        release.set()
        thread.join(5)

    assert not thread.is_alive()
    assert errors == []
    assert results == [JobStatus.COMPLETED]
    assert len(calls) == 1
