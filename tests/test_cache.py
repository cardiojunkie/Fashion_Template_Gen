from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fashion_cms.database import JobDatabase
from fashion_cms.jobs import JobService
from fashion_cms.llm_service import FakeLLMClient, InvalidLLMResponse, LLMResponse
from fashion_cms.models import AnalysisMode, InputRow, JobStatus, UploadedImage, WorkItemStatus
from fashion_cms.registry import Registry, load_registry
from fashion_cms.topwear_extraction import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    TOPWEAR_PROFILE_ID,
    cached_item_keys,
    fake_topwear_client,
    fake_topwear_response,
    run_topwear_job,
)
from fashion_cms.variant_service import CacheContext, ImageAsset, build_cache_key


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def registry() -> Registry:
    return load_registry(ROOT / "config" / "attribute_registry.xlsx")


def row(
    sku: str,
    description: str | None = "color: Red; size: M",
    *,
    base_code: str = "BASE",
    row_number: int = 2,
) -> InputRow:
    return InputRow(
        row_number=row_number,
        sku=sku,
        base_code=base_code,
        attributes__lulu_ean=f"EAN-{sku}",
        attributes__shipping_weight="1.0",
        input_data=description,
    )


def image(sku: str, marker: bytes = b"a") -> UploadedImage:
    return UploadedImage(
        source_name=f"{sku}-1.jpg",
        filename=f"{sku}-1.jpg",
        sku=sku,
        ordinal=1,
        image_format="jpeg",
        width=100,
        height=100,
        content=b"deterministic-image-" + marker,
    )


def create_job(
    database: JobDatabase,
    registry: Registry,
    rows: tuple[InputRow, ...],
    images: tuple[UploadedImage, ...] = (),
    **changes: Any,
) -> str:
    options: dict[str, Any] = {
        "attribute_set": "topwear",
        "product_profile": TOPWEAR_PROFILE_ID,
        "registry_version": registry.fingerprint,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "model_identifier": "fake-topwear-model",
        "image_detail": "high",
    }
    options.update(changes)
    return JobService(database).create_job(rows, images, **options)


def test_valid_cache_hit_avoids_a_second_fake_llm_call(registry: Registry) -> None:
    database = JobDatabase(":memory:")
    rows = (row("SKU-1"),)
    images = (image("SKU-1"),)
    first_job = create_job(database, registry, rows, images)
    first_client = fake_topwear_client()

    run_topwear_job(database, first_job, first_client, images, registry)

    second_job = create_job(database, registry, rows, images)
    second_item = database.list_work_items(second_job)[0]
    second_client = fake_topwear_client()

    assert cached_item_keys(database, second_job, registry) == {second_item.key}
    run_topwear_job(database, second_job, second_client, images, registry)

    first_item = database.list_work_items(first_job)[0]
    second_item = database.list_work_items(second_job)[0]
    cached_result = database.get_work_item_result(second_item)
    assert len(first_client.calls) == 1
    assert second_client.calls == []
    assert first_item.cache_hit is False
    assert second_item.cache_hit is True
    assert cached_result is not None
    assert cached_result["job_id"] == first_job


def test_semantically_invalid_cached_evidence_is_deleted_and_recomputed(
    registry: Registry,
) -> None:
    database = JobDatabase(":memory:")
    rows = (row("SKU-1"),)
    images = (image("SKU-1"),)
    first_job = create_job(database, registry, rows, images)
    run_topwear_job(database, first_job, fake_topwear_client(), images, registry)
    first_item = database.list_work_items(first_job)[0]
    cached = database.get_work_item_result(first_item)
    assert cached is not None
    cached["raw_output"]["sku_attributes"][0]["observations"][0][
        "evidence_refs"
    ] = ["UNKNOWN-99"]
    cached["vision_result"]["sku_attributes"]["SKU-1"][0][
        "evidence_refs"
    ] = ["UNKNOWN-99"]
    with database.connection() as connection, connection:
        connection.execute(
            "UPDATE result_cache SET result_json = ? WHERE cache_key = ?",
            (json.dumps(cached), first_item.cache_key),
        )

    second_job = create_job(database, registry, rows, images)
    second_item = database.list_work_items(second_job)[0]
    assert cached_item_keys(database, second_job, registry) == frozenset()
    assert database.get_cached_result(
        second_item.cache_key, second_item.cache_payload_json
    ) is None

    client = fake_topwear_client()
    run_topwear_job(database, second_job, client, images, registry)

    assert len(client.calls) == 1
    assert database.list_work_items(second_job)[0].cache_hit is False


def test_invalid_model_result_is_not_cached_as_success(registry: Registry) -> None:
    database = JobDatabase(":memory:")
    rows = (row("SKU-1"),)
    images = (image("SKU-1"),)
    job_id = create_job(database, registry, rows, images)
    invalid = FakeLLMClient(
        responder=lambda request: LLMResponse(
            request_id="req-invalid",
            model=str(request.payload["model"]),
            status="completed",
            output_text='{"missing":"required structure"}',
            usage={"total_tokens": 1},
        )
    )

    result = run_topwear_job(database, job_id, invalid, images, registry)
    failed_item = database.list_work_items(job_id)[0]

    assert result.status == JobStatus.FAILED
    assert failed_item.status == WorkItemStatus.FAILED
    assert len(invalid.calls) == 1
    assert failed_item.request_metadata is not None
    assert failed_item.request_metadata["job_id"] == job_id
    assert failed_item.request_metadata["work_item_key"] == failed_item.key
    assert failed_item.request_metadata["model"] == "fake-topwear-model"
    assert failed_item.request_metadata["prompt_version"] == PROMPT_VERSION
    assert failed_item.request_metadata["schema_version"] == SCHEMA_VERSION
    assert failed_item.request_metadata["registry_version"] == registry.fingerprint
    assert failed_item.request_metadata["image_detail"] == "high"
    assert failed_item.request_metadata["status"] == "invalid"
    assert failed_item.request_metadata["retry_count"] == 0
    assert failed_item.request_metadata["usage"] == {"total_tokens": 1}
    assert "structured validation" in str(failed_item.request_metadata["error"])
    assert database.get_cached_result(
        failed_item.cache_key, failed_item.cache_payload_json
    ) is None

    valid = fake_topwear_client()
    run_topwear_job(
        database,
        job_id,
        valid,
        images,
        registry,
        retry_failed=True,
    )
    completed_item = database.list_work_items(job_id)[0]

    assert len(valid.calls) == 1
    assert completed_item.status == WorkItemStatus.REVIEW_REQUIRED
    assert database.get_cached_result(
        completed_item.cache_key, completed_item.cache_payload_json
    ) is not None


@pytest.mark.parametrize(
    ("component", "change"),
    [
        ("analysis mode", {"analysis_mode": AnalysisMode.BASE_CODE_SIZE_ONLY}),
        ("ordered identifiers", {"ordered_identifiers": ("base:BASE", "SKU-2")}),
        ("input data", {"input_data": (("SKU-1", "color: Blue"),)}),
        (
            "EAN",
            {"row_specific_data": (("SKU-1", "BASE", "EAN-CHANGED", "1.0"),)},
        ),
        (
            "shipping weight",
            {"row_specific_data": (("SKU-1", "BASE", "EAN-SKU-1", "2.0"),)},
        ),
        ("image", {"image_assets": ("changed",)}),
        ("representative SKU", {"representative_sku": "SKU-2"}),
        ("attribute set", {"context": {"attribute_set": "bottomwear"}}),
        ("product profile", {"context": {"product_profile": "shirts"}}),
        ("registry", {"context": {"registry_version": "registry-2"}}),
        ("prompt", {"context": {"prompt_version": "prompt-2"}}),
        ("schema", {"context": {"schema_version": "schema-2"}}),
        ("model", {"context": {"model_identifier": "model-2"}}),
        ("image detail", {"context": {"image_detail": "low"}}),
    ],
)
def test_every_cache_input_change_invalidates_the_key(
    component: str,
    change: dict[str, object],
) -> None:
    context = CacheContext(
        attribute_set="topwear",
        product_profile=TOPWEAR_PROFILE_ID,
        registry_version="registry-1",
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        model_identifier="model-1",
        image_detail="high",
    )
    asset = ImageAsset.from_upload(image("SKU-1"))
    inputs: dict[str, object] = {
        "analysis_mode": AnalysisMode.PER_SKU,
        "ordered_identifiers": ("base:BASE", "SKU-1"),
        "input_data": (("SKU-1", "color: Red"),),
        "row_specific_data": (("SKU-1", "BASE", "EAN-SKU-1", "1.0"),),
        "image_assets": (asset,),
        "context": context,
        "representative_sku": "SKU-1",
    }
    baseline = build_cache_key(**inputs)  # type: ignore[arg-type]

    if change.get("image_assets") == ("changed",):
        change = {"image_assets": (ImageAsset.from_upload(image("SKU-1", b"b")),)}
    context_change = change.pop("context", None)
    if isinstance(context_change, dict):
        change["context"] = context.model_copy(update=context_change)
    inputs.update(change)

    assert build_cache_key(**inputs) != baseline, component  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "component",
    ["input_data", "ean", "shipping_weight", "representative", "image_detail"],
)
def test_phase5_cache_misses_for_changed_runtime_input(
    registry: Registry,
    component: str,
) -> None:
    database = JobDatabase(":memory:")
    rows = (
        row("SKU-1", "color: Red; size: S"),
        row("SKU-2", "color: Red; size: M", row_number=3),
    )
    images = (image("SKU-1"), image("SKU-2", b"b"))
    baseline_options: dict[str, Any] = {
        "modes": {"base:BASE": AnalysisMode.BASE_CODE_SIZE_ONLY},
        "representatives": {"base:BASE": "SKU-1"},
    }
    baseline_job = create_job(database, registry, rows, images, **baseline_options)
    run_topwear_job(database, baseline_job, fake_topwear_client(), images, registry)

    changed_rows = rows
    changed_options = dict(baseline_options)
    if component == "input_data":
        changed_rows = (rows[0].model_copy(update={"input_data": "color: Blue"}), rows[1])
    elif component == "ean":
        changed_rows = (
            rows[0].model_copy(update={"attributes__lulu_ean": "EAN-CHANGED"}),
            rows[1],
        )
    elif component == "shipping_weight":
        changed_rows = (
            rows[0].model_copy(update={"attributes__shipping_weight": "2.0"}),
            rows[1],
        )
    elif component == "representative":
        changed_options["representatives"] = {"base:BASE": "SKU-2"}
    else:
        changed_options["image_detail"] = "low"

    changed_job = create_job(
        database,
        registry,
        changed_rows,
        images,
        **changed_options,
    )
    client = fake_topwear_client()
    run_topwear_job(database, changed_job, client, images, registry)
    baseline_item = database.list_work_items(baseline_job)[0]
    changed_item = database.list_work_items(changed_job)[0]

    assert len(client.calls) == 1, component
    assert changed_item.cache_hit is False
    assert changed_item.cache_key != baseline_item.cache_key


def test_partial_failure_preserves_success_and_retry_only_calls_failed_item(
    registry: Registry,
) -> None:
    database = JobDatabase(":memory:")
    rows = (row("SKU-1"), row("SKU-2", row_number=3))
    images = (image("SKU-1"), image("SKU-2", b"b"))
    job_id = create_job(database, registry, rows, images)

    def fail_second(request) -> LLMResponse:
        if request.contract.representative_sku == "SKU-2":
            raise InvalidLLMResponse("Invalid structured result for SKU-2.")
        return fake_topwear_response(request)

    first_client = FakeLLMClient(responder=fail_second)
    first_result = run_topwear_job(database, job_id, first_client, images, registry)
    first_items = database.list_work_items(job_id)
    successful_result = database.get_work_item_result(first_items[0])

    assert first_result.status == JobStatus.PARTIAL_FAILURE
    assert [item.status for item in first_items] == [
        WorkItemStatus.REVIEW_REQUIRED,
        WorkItemStatus.FAILED,
    ]

    retry_client = fake_topwear_client()
    retried = run_topwear_job(
        database,
        job_id,
        retry_client,
        images,
        registry,
        retry_failed=True,
    )
    final_items = database.list_work_items(job_id)

    assert retried.status == JobStatus.REVIEW_REQUIRED
    assert [call.contract.representative_sku for call in retry_client.calls] == ["SKU-2"]
    assert [item.retry_count for item in final_items] == [0, 1]
    assert database.get_work_item_result(final_items[0]) == successful_result
    assert all(item.status == WorkItemStatus.REVIEW_REQUIRED for item in final_items)


@pytest.mark.parametrize(
    ("mode", "expected_count"),
    [
        (AnalysisMode.PER_SKU, 2),
        (AnalysisMode.BASE_CODE_SIZE_ONLY, 1),
    ],
)
def test_actual_execution_count_matches_the_stored_plan(
    registry: Registry,
    mode: AnalysisMode,
    expected_count: int,
) -> None:
    database = JobDatabase(":memory:")
    rows = (
        row("SKU-1", "color: Red; size: S"),
        row("SKU-2", "color: Red; size: M", row_number=3),
    )
    images = (image("SKU-1"), image("SKU-2", b"b"))
    job_id = create_job(
        database,
        registry,
        rows,
        images,
        modes={"base:BASE": mode},
    )
    client = fake_topwear_client()

    run_topwear_job(database, job_id, client, images, registry)

    assert len(database.list_work_items(job_id)) == expected_count
    assert len(client.calls) == expected_count


def test_corrupt_stored_plan_blocks_all_execution(registry: Registry) -> None:
    database = JobDatabase(":memory:")
    rows = (row("SKU-1"),)
    images = (image("SKU-1"),)
    job_id = create_job(database, registry, rows, images)
    with database.connection() as connection, connection:
        connection.execute(
            "UPDATE work_items SET cache_key = ? WHERE job_id = ?",
            ("0" * 64, job_id),
        )
    client = fake_topwear_client()

    with pytest.raises(ValueError, match="request-plan mismatch"):
        run_topwear_job(database, job_id, client, images, registry)

    assert client.calls == []


def test_stale_registry_blocks_cache_lookup_and_execution(registry: Registry) -> None:
    database = JobDatabase(":memory:")
    rows = (row("SKU-1"),)
    images = (image("SKU-1"),)
    job_id = create_job(database, registry, rows, images)
    changed_registry = registry.model_copy(update={"fingerprint": "changed-registry"})
    client = fake_topwear_client()

    with pytest.raises(ValueError, match="registry changed"):
        cached_item_keys(database, job_id, changed_registry)
    with pytest.raises(ValueError, match="registry changed"):
        run_topwear_job(database, job_id, client, images, changed_registry)

    assert client.calls == []


def test_request_metadata_and_actual_model_survive_database_restart(
    tmp_path: Path,
    registry: Registry,
) -> None:
    path = tmp_path / "metadata.sqlite3"
    database = JobDatabase(path)
    rows = (row("SKU-1"),)
    images = (image("SKU-1"),)
    job_id = create_job(database, registry, rows, images)

    def response_with_metadata(request) -> LLMResponse:
        return fake_topwear_response(request).model_copy(
            update={
                "request_id": "req-safe-123",
                "model": "actual-model-snapshot",
                "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            }
        )

    run_topwear_job(
        database,
        job_id,
        FakeLLMClient(responder=response_with_metadata),
        images,
        registry,
    )
    database.close()

    item = JobDatabase(path).list_work_items(job_id)[0]
    assert item.request_metadata == {
        "error": None,
        "image_detail": "high",
        "model": "actual-model-snapshot",
        "prompt_version": PROMPT_VERSION,
        "registry_version": registry.fingerprint,
        "request_id": "req-safe-123",
        "retry_count": 0,
        "schema_version": SCHEMA_VERSION,
        "status": "completed",
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    }


def test_api_key_is_absent_from_requests_errors_and_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: Registry,
) -> None:
    secret = "nvapi-phase5-do-not-store-this-value"
    monkeypatch.setenv("NVIDIA_API_KEY", secret)
    path = tmp_path / "secret-check.sqlite3"
    database = JobDatabase(path)
    rows = (row("SKU-1"),)
    images = (image("SKU-1"),)
    job_id = create_job(database, registry, rows, images)

    def fail_with_secret(_request) -> LLMResponse:
        raise InvalidLLMResponse(f"Authorization: Bearer {secret}")

    client = FakeLLMClient(responder=fail_with_secret)
    run_topwear_job(database, job_id, client, images, registry)
    item = database.list_work_items(job_id)[0]
    database.close()

    assert secret not in json.dumps(client.calls[0].payload)
    assert item.error is not None and "[redacted]" in item.error
    assert secret not in item.error
    assert secret not in json.dumps(item.request_metadata)
    for sqlite_file in tmp_path.iterdir():
        assert secret.encode() not in sqlite_file.read_bytes(), sqlite_file.name
