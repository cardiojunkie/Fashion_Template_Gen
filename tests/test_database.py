from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fashion_cms.database import (
    SCHEMA_VERSION,
    DatabaseVersionError,
    InvalidJobEdit,
    InvalidStateTransition,
    JobDatabase,
)
from fashion_cms.jobs import JobService
from fashion_cms.models import AnalysisMode, InputRow, JobStatus
from fashion_cms.variant_service import CacheContext, ImageAsset, build_variant_groups


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
        model_code_input_data=description,
    )


def image(sku: str, ordinal: int, digest: str = "a") -> ImageAsset:
    return ImageAsset(
        sku=sku,
        ordinal=ordinal,
        filename=f"{sku}-{ordinal}.jpg",
        sha256=digest * 64,
        width=100,
        height=100,
    )


def cache_context() -> CacheContext:
    return CacheContext(
        attribute_set="topwear",
        registry_version="registry-1",
        prompt_version="prompt-1",
        schema_version="schema-1",
        model_identifier="model-1",
        image_detail="high",
    )


def test_migration_and_job_selections_survive_a_database_restart(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    rows = (
        row("SKU-1", description="Black shirt size S"),
        row("SKU-2", description="Black shirt size M", row_number=3),
        row("NO-BASE", None, "Green shirt", 4),
    )
    assets = (image("SKU-1", 1), image("SKU-2", 1, "b"), image("NO-BASE", 1, "c"))
    database = JobDatabase(path)
    service = JobService(database)
    job_id = service.create_job(
        rows,
        assets,
        attribute_set="topwear",
        registry_version="registry-1",
    )
    service.update_group(
        job_id,
        "base:BASE",
        analysis_mode=AnalysisMode.BASE_CODE_SIZE_ONLY,
        representative_sku="SKU-2",
    )
    artifact = database.add_artifact(job_id, "CMS_WORKBOOK", "artifacts/output.xlsx")
    database.close()

    restarted = JobDatabase(path)
    groups = restarted.load_groups(job_id)

    assert restarted.schema_version == SCHEMA_VERSION
    assert restarted.get_job(job_id).status == JobStatus.READY
    assert restarted.load_rows(job_id) == rows
    assert restarted.load_image_assets(job_id) == assets
    assert groups[0].analysis_mode == AnalysisMode.BASE_CODE_SIZE_ONLY
    assert groups[0].representative_sku == "SKU-2"
    assert groups[0].user_selected_representative is True
    assert groups[1].key == "sku:NO-BASE"
    assert groups[1].base_code is None
    assert len(restarted.list_work_items(job_id)) == 2
    assert restarted.list_artifacts(job_id) == (artifact,)


def test_newer_database_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.close()

    with pytest.raises(DatabaseVersionError, match="newer than supported"):
        JobDatabase(path)


def test_valid_and_invalid_job_state_transitions_are_enforced() -> None:
    database = JobDatabase(":memory:")
    groups = build_variant_groups((row("SKU-1"),))
    job_id = database.create_job(groups, cache_context())

    with pytest.raises(InvalidStateTransition, match="UPLOADED to COMPLETED"):
        database.transition_job(job_id, JobStatus.COMPLETED)
    assert database.get_job(job_id).status == JobStatus.UPLOADED

    for status in (
        JobStatus.VALIDATING,
        JobStatus.READY,
        JobStatus.RUNNING,
        JobStatus.COMPLETED,
    ):
        assert database.transition_job(job_id, status).status == status

    with pytest.raises(InvalidStateTransition, match="COMPLETED to RUNNING"):
        database.transition_job(job_id, JobStatus.RUNNING)


def test_invalid_group_edit_rolls_back_without_losing_the_existing_plan() -> None:
    database = JobDatabase(":memory:")
    service = JobService(database)
    job_id = service.create_job(
        (row("SKU-1"), row("SKU-2", row_number=3)),
        attribute_set="topwear",
        registry_version="registry-1",
    )
    original_items = database.list_work_items(job_id)

    with pytest.raises(InvalidJobEdit, match="must belong"):
        database.update_group(job_id, "base:BASE", representative_sku="OUTSIDER")

    assert database.load_groups(job_id)[0].representative_sku == "SKU-1"
    assert database.list_work_items(job_id) == original_items
