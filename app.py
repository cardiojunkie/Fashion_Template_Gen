from hashlib import sha256
import os
from pathlib import Path

import streamlit as st

from fashion_cms.database import InvalidJobEdit, InvalidStateTransition, JobDatabase
from fashion_cms.excel_service import (
    SYSTEM_COPY_FIELDS,
    build_blank_cms_workbook,
    parse_input_workbook,
)
from fashion_cms.image_downloader import (
    DownloadSettings,
    build_download_report,
    build_image_zip,
    download_images,
    parse_url_workbook,
)
from fashion_cms.image_service import open_oriented_image, parse_uploaded_images
from fashion_cms.jobs import JobService
from fashion_cms.models import AnalysisMode, JobStatus, Severity, ValidationIssue
from fashion_cms.registry import load_registry


ROOT = Path(__file__).resolve().parent
IMAGE_BATCH_STATE = "image_download_batch"
IMAGE_WORKBOOK_DIGEST_STATE = "image_download_workbook_digest"
CMS_JOB_STATE = "cms_job_id"
CMS_SOURCE_DIGEST_STATE = "cms_source_digest"
DEFAULT_DATABASE_PATH = ROOT / "data" / "fashion_cms.sqlite3"
registry = load_registry(ROOT / "config" / "attribute_registry.xlsx")
set_names = {
    row.attribute_set_id: row.attribute_set_name for row in registry.attribute_sets
}


@st.cache_resource
def get_database(path: str) -> JobDatabase:
    return JobDatabase(path)


def job_database() -> JobDatabase:
    path = os.environ.get("FASHION_CMS_DB_PATH", str(DEFAULT_DATABASE_PATH))
    return get_database(path)


def _source_digest(
    workbook: bytes,
    images: tuple[tuple[str, bytes], ...],
    attribute_set: str,
) -> str:
    digest = sha256(attribute_set.encode())
    digest.update(len(workbook).to_bytes(8, "big"))
    digest.update(workbook)
    for name, content in images:
        encoded_name = name.encode("utf-8", "surrogatepass")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def show_issues(issues: tuple[ValidationIssue, ...]) -> None:
    if not issues:
        st.success("No validation findings.")
        return
    for severity in (Severity.CRITICAL, Severity.WARNING):
        group = [issue for issue in issues if issue.severity == severity]
        if not group:
            continue
        st.subheader(f"{severity.value.title()} ({len(group)})")
        st.dataframe(
            [
                {
                    "Code": issue.code,
                    "Location": issue.location or "",
                    "Message": issue.message,
                }
                for issue in group
            ],
            hide_index=True,
            width="stretch",
        )


def cms_workbook_page() -> None:
    st.title("Fashion CMS Upload Generator")
    st.write("Create a validated blank CMS workbook from local product data and SKU images.")

    attribute_set = st.selectbox(
        "CMS attribute set",
        tuple(registry.mappings_by_set),
        format_func=lambda set_id: set_names[set_id],
    )
    workbook_upload = st.file_uploader("Input workbook", type=["xlsx"])
    image_uploads = st.file_uploader(
        "Product images or ZIP files",
        type=["jpg", "jpeg", "png", "webp", "zip"],
        accept_multiple_files=True,
    )

    if workbook_upload is None:
        st.info("Upload an .xlsx workbook to begin local validation.")
        return

    workbook_content = workbook_upload.getvalue()
    image_upload_data = tuple(
        (upload.name, upload.getvalue()) for upload in image_uploads
    )
    workbook_result = parse_input_workbook(workbook_content, workbook_upload.name)
    image_result = parse_uploaded_images(
        image_upload_data,
        tuple(row.sku for row in workbook_result.rows),
    )
    show_issues(workbook_result.issues + image_result.issues)

    headers = registry.mappings_by_set[attribute_set]
    if workbook_result.rows:
        st.subheader("CMS skeleton preview")
        preview = [
            {
                header: getattr(row, SYSTEM_COPY_FIELDS[header])
                if header in SYSTEM_COPY_FIELDS
                else None
                for header in headers
            }
            for row in workbook_result.rows[:20]
        ]
        st.dataframe(preview, hide_index=True, width="stretch")
        if len(workbook_result.rows) > 20:
            st.caption(f"Showing 20 of {len(workbook_result.rows):,} rows.")

    if image_result.images:
        st.subheader("Matched image preview")
        # ponytail: preview is capped; add pagination only if large uploads need visual review.
        for image in image_result.images[:12]:
            with open_oriented_image(image.content) as preview_image:
                st.image(
                    preview_image,
                    caption=(
                        f"{image.sku} · image {image.ordinal} · "
                        f"{image.width}×{image.height}"
                    ),
                    width=180,
                )
        if len(image_result.images) > 12:
            st.caption(f"Showing 12 of {len(image_result.images):,} matched images.")

    if workbook_result.ready and image_result.ready:
        st.success(
            f"Ready to process · {len(workbook_result.rows):,} SKU rows · "
            f"{len(image_result.images):,} matched images"
        )
        output = build_blank_cms_workbook(workbook_result.rows, headers)
        st.download_button(
            "Download blank CMS workbook",
            data=output,
            file_name=f"cms_{attribute_set}_blank.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        digest = _source_digest(workbook_content, image_upload_data, attribute_set)
        if st.session_state.get(CMS_SOURCE_DIGEST_STATE) != digest:
            st.session_state[CMS_SOURCE_DIGEST_STATE] = digest
            st.session_state.pop(CMS_JOB_STATE, None)

        job_id = st.session_state.get(CMS_JOB_STATE)
        if not job_id:
            st.subheader("Variant analysis job")
            st.caption(
                "Creating a job stores normalized rows, image metadata, and hashes. "
                "No API request is made in Phase 4."
            )
            if st.button("Create persistent job", type="primary"):
                try:
                    job_id = JobService(job_database()).create_job(
                        workbook_result.rows,
                        image_result.images,
                        attribute_set=attribute_set,
                        registry_version=registry.fingerprint,
                    )
                except Exception:
                    st.error("The persistent job could not be created safely.")
                else:
                    st.session_state[CMS_JOB_STATE] = job_id
                    st.rerun()
        else:
            try:
                show_job_plan(job_id)
            except Exception:
                st.session_state.pop(CMS_JOB_STATE, None)
                st.error("The selected job could not be opened. Create or open it again.")
    else:
        st.error("Resolve critical findings before processing or download.")


def show_job_plan(job_id: str) -> None:
    database = job_database()
    service = JobService(database)
    job = database.get_job(job_id)
    groups = database.load_groups(job_id)
    items = database.list_work_items(job_id)
    editable = job.status == JobStatus.READY

    st.subheader(f"Variant analysis job · {job_id}")
    st.caption(f"Status: {job.status.value} · selections and work plan are stored in SQLite.")
    bulk_mode = st.selectbox(
        "Bulk analysis mode",
        tuple(AnalysisMode),
        format_func=lambda mode: mode.value,
        key=f"bulk_mode_{job_id}",
        disabled=not editable,
    )
    if st.button("Apply mode to all groups", disabled=not editable, key=f"bulk_{job_id}"):
        service.bulk_update_mode(job_id, bulk_mode)
        st.rerun()

    st.dataframe(
        [
            {
                "Base code": group.base_code or "(blank · SKU fallback)",
                "SKU count": len(group.skus),
                "Image count": len(group.images),
                "Detected colors": ", ".join(group.detected_colors),
                "Detected sizes": ", ".join(group.detected_sizes),
                "Warnings": " ".join(group.size_only_warnings),
                "Mode": group.analysis_mode.value,
                "Representative SKU": group.representative_sku,
            }
            for group in groups
        ],
        hide_index=True,
        width="stretch",
    )

    for group in groups:
        label = group.base_code or f"blank base code · {group.skus[0]}"
        with st.expander(label):
            if group.size_only_suggested:
                st.info(
                    "Size-only may be suitable because descriptions differ only by "
                    "recognized size terms. It remains off until explicitly selected."
                )
            mode = st.selectbox(
                "Analysis mode",
                tuple(AnalysisMode),
                index=tuple(AnalysisMode).index(group.analysis_mode),
                format_func=lambda value: value.value,
                key=f"mode_{job_id}_{group.key}",
                disabled=not editable,
            )
            representative = st.selectbox(
                "Representative SKU",
                group.skus,
                index=group.skus.index(group.representative_sku),
                key=f"representative_{job_id}_{group.key}",
                disabled=not editable,
            )
            if mode == AnalysisMode.BASE_CODE_SIZE_ONLY:
                st.warning(
                    "Use size-only only when these SKUs differ by size and show the same "
                    "visible product."
                )
                for warning in group.size_only_warnings:
                    st.warning(warning)
            if st.button(
                "Save group selection",
                key=f"save_{job_id}_{group.key}",
                disabled=not editable,
            ):
                try:
                    service.update_group(
                        job_id,
                        group.key,
                        analysis_mode=mode,
                        representative_sku=(
                            representative
                            if representative != group.representative_sku
                            else None
                        ),
                    )
                except (InvalidJobEdit, InvalidStateTransition, ValueError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()

    size_only_count = sum(
        group.analysis_mode == AnalysisMode.BASE_CODE_SIZE_ONLY for group in groups
    )
    metrics = st.columns(5)
    metrics[0].metric("Base-code groups", len(groups))
    metrics[1].metric("SKUs", sum(len(group.skus) for group in groups))
    metrics[2].metric("Size-only groups", size_only_count)
    metrics[3].metric("Per-SKU groups", len(groups) - size_only_count)
    metrics[4].metric("Planned vision requests", len(items))
    st.caption("This is the exact Phase 4 work plan; no LLM or API request is performed.")
    st.dataframe(
        [
            {
                "Request": item.position + 1,
                "Base-code group": item.group_key,
                "Mode": item.analysis_mode.value,
                "Represented SKUs": ", ".join(item.represented_skus),
                "Representative SKU": (
                    item.representative_sku
                    if item.analysis_mode == AnalysisMode.BASE_CODE_SIZE_ONLY
                    else ""
                ),
                "Status": item.status.value,
                "Cache hit": item.cache_hit,
            }
            for item in items
        ],
        hide_index=True,
        width="stretch",
    )
    if job.status == JobStatus.READY and st.button(
        "Run fake extraction", type="primary", key=f"run_{job_id}"
    ):
        service.run_job(job_id)
        st.rerun()
    elif job.status == JobStatus.RUNNING and st.button(
        "Resume interrupted job", type="primary", key=f"resume_{job_id}"
    ):
        service.resume_job(job_id)
        st.rerun()
    elif job.status in {JobStatus.PARTIAL_FAILURE, JobStatus.FAILED} and st.button(
        "Retry failed items", type="primary", key=f"retry_{job_id}"
    ):
        service.retry_failed_items(job_id)
        st.rerun()


def image_downloader_page() -> None:
    st.title("Image Downloader")
    st.write(
        "Download SKU-linked image URLs and standardize successful images to exact "
        "1500 × 1500 JPEGs."
    )
    st.selectbox("Processing mode", ("PAD_WHITE",), disabled=True)
    st.caption(
        "PAD_WHITE preserves aspect ratio, does not crop or upscale, and centres the image "
        "on white. REMOVE_AND_WHITE is optional and not installed; use PAD_WHITE as the "
        "safe fallback."
    )

    workbook_upload = st.file_uploader(
        "Image URL workbook", type=["xlsx"], key="image_url_workbook"
    )
    if workbook_upload is None:
        st.info("Upload an .xlsx workbook with SKU in column A and image URLs from column B.")
        return

    content = workbook_upload.getvalue()
    workbook_digest = sha256(content).hexdigest()
    if st.session_state.get(IMAGE_WORKBOOK_DIGEST_STATE) != workbook_digest:
        st.session_state[IMAGE_WORKBOOK_DIGEST_STATE] = workbook_digest
        st.session_state.pop(IMAGE_BATCH_STATE, None)

    workbook_result = parse_url_workbook(content, workbook_upload.name)
    show_issues(workbook_result.issues)

    if workbook_result.requests:
        st.subheader("URL column preview")
        st.dataframe(
            [
                {
                    "SKU": request.sku,
                    "URL ordinal": request.ordinal,
                    "Source URL": request.source_url,
                    "Output filename": request.output_filename,
                }
                for request in workbook_result.requests[:100]
            ],
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "URL ordinals come from physical workbook columns; blank or failed URLs do not "
            "renumber later images."
        )
        if len(workbook_result.requests) > 100:
            st.caption(
                f"Showing 100 of {len(workbook_result.requests):,} image URLs."
            )

    if not workbook_result.ready:
        st.error("Resolve critical findings before downloading images.")
        return

    batch = st.session_state.get(IMAGE_BATCH_STATE)
    if batch is None or batch.failed:
        retrying = batch is not None
        label = "Retry failed URLs" if retrying else "Download and standardize images"
        if st.button(label, type="primary"):
            try:
                settings = DownloadSettings.from_env()
                with st.spinner("Downloading and standardizing images…"):
                    batch = download_images(
                        workbook_result.requests,
                        settings=settings,
                        previous=batch if retrying else None,
                    )
            except ValueError as exc:
                st.error(f"Image downloader configuration is invalid: {exc}")
                return
            except Exception:
                st.error("The image download could not complete safely. Try again.")
                return
            st.session_state[IMAGE_BATCH_STATE] = batch
            st.rerun()

    if batch is None:
        st.info(f"Ready to download {len(workbook_result.requests):,} image URLs.")
        return

    if batch.failed:
        st.warning(
            f"{len(batch.images):,} images succeeded and {len(batch.failed):,} URLs failed. "
            "Retry failed URLs keeps successful images."
        )
    else:
        st.success(f"All {len(batch.images):,} images downloaded successfully.")

    if batch.images:
        st.subheader("Processed image preview")
        low_resolution = sum(image.low_resolution for image in batch.images)
        if low_resolution:
            st.warning(
                f"{low_resolution:,} source images have low resolution. They were centred "
                "without upscaling."
            )
        # ponytail: preview is capped; add pagination only if operators need more samples.
        for image in batch.images[:12]:
            st.image(
                image.content,
                caption=(
                    f"{image.output_filename} · source "
                    f"{image.source_width}×{image.source_height} · output "
                    f"{image.output_width}×{image.output_height}"
                ),
                width=180,
            )
        if len(batch.images) > 12:
            st.caption(f"Showing 12 of {len(batch.images):,} processed images.")

    try:
        if batch.images:
            st.download_button(
                "Download successful images ZIP",
                data=build_image_zip(batch.images),
                file_name="standardized_images.zip",
                mime="application/zip",
            )
        st.download_button(
            "Download image report",
            data=build_download_report(batch.report),
            file_name="image_download_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception:
        st.error("Download artifacts could not be prepared safely.")

    if batch.failed:
        st.subheader("Failed URLs")
        st.dataframe(
            [
                {
                    "SKU": row.sku,
                    "URL ordinal": row.ordinal,
                    "Source URL": row.source_url,
                    "HTTP status": row.http_status,
                    "Error message": row.error_message or "",
                }
                for row in batch.failed
            ],
            hide_index=True,
            width="stretch",
        )


def attribute_registry_page() -> None:
    st.title("Attribute Registry")
    st.write("Read-only view of the validated CMS attribute registry.")
    st.caption(f"Registry version: {registry.fingerprint}")
    st.dataframe(
        [
            {
                "Attribute set ID": set_id,
                "Attribute set": set_names[set_id],
                "Header count": len(headers),
            }
            for set_id, headers in registry.mappings_by_set.items()
        ],
        hide_index=True,
        width="stretch",
    )
    selected_set = st.selectbox(
        "Inspect attribute set",
        tuple(registry.mappings_by_set),
        format_func=lambda set_id: set_names[set_id],
        key="registry_set",
    )
    st.dataframe(
        [
            {
                "Position": position,
                "Header": header,
                "Data type": registry.definitions_by_header[header].data_type.value,
                "Scope": registry.definitions_by_header[header].scope.value,
                "Evidence policy": registry.definitions_by_header[
                    header
                ].evidence_policy.value,
            }
            for position, header in enumerate(
                registry.mappings_by_set[selected_set], start=1
            )
        ],
        hide_index=True,
        width="stretch",
    )


def job_history_page() -> None:
    st.title("Job History")
    st.write("Open persisted jobs, inspect failures, and resume only unfinished work.")
    database = job_database()
    service = JobService(database)
    jobs = database.list_jobs()
    if not jobs:
        st.info("No persistent jobs have been created yet.")
        return

    st.dataframe(
        [
            {
                "Job ID": job.id,
                "Job type": job.job_type,
                "Attribute set": job.attribute_set,
                "Created time": job.created_at,
                "Updated time": job.updated_at,
                "Overall status": job.status.value,
                "Completed items": job.completed_item_count,
                "Failed items": job.failed_item_count,
                "Review required": job.review_required_count,
                "Planned requests": job.planned_request_count,
            }
            for job in jobs
        ],
        hide_index=True,
        width="stretch",
    )
    selected_id = st.selectbox(
        "Open job details",
        tuple(job.id for job in jobs),
        format_func=lambda job_id: next(
            f"{job_id} · {job.status.value}" for job in jobs if job.id == job_id
        ),
    )
    job = database.get_job(selected_id)
    groups = database.load_groups(selected_id)
    items = database.list_work_items(selected_id)
    summary = next(summary for summary in jobs if summary.id == selected_id)

    columns = st.columns(5)
    columns[0].metric("Status", job.status.value)
    columns[1].metric("Planned", len(items))
    columns[2].metric("Completed", summary.completed_item_count)
    columns[3].metric("Failed", summary.failed_item_count)
    columns[4].metric("Review required", summary.review_required_count)
    cache_hits = sum(item.cache_hit for item in items)
    st.caption(
        f"{cache_hits:,} work item(s) reused deterministic cached results. "
        "Successful items are never repeated by failure-only retry."
    )

    st.subheader("Selected analysis modes")
    st.dataframe(
        [
            {
                "Base code": group.base_code or "(blank · SKU fallback)",
                "SKUs": ", ".join(group.skus),
                "Mode": group.analysis_mode.value,
                "Representative SKU": group.representative_sku,
                "Warnings": " ".join(group.size_only_warnings),
            }
            for group in groups
        ],
        hide_index=True,
        width="stretch",
    )

    failures = [item for item in items if item.status.value == "FAILED"]
    if failures:
        st.subheader("Base-code / SKU failures")
        st.dataframe(
            [
                {
                    "Base-code group": item.group_key,
                    "SKUs": ", ".join(item.represented_skus),
                    "Representative SKU": item.representative_sku,
                    "Error": item.error or "",
                    "Retry count": item.retry_count,
                }
                for item in failures
            ],
            hide_index=True,
            width="stretch",
        )
        if st.button("Retry failed items", type="primary", key=f"history_retry_{selected_id}"):
            service.retry_failed_items(selected_id)
            st.rerun()
    if job.status in {
        JobStatus.UPLOADED,
        JobStatus.VALIDATING,
        JobStatus.READY,
        JobStatus.RUNNING,
    } and st.button(
        "Resume interrupted job", type="primary", key=f"history_resume_{selected_id}"
    ):
        service.resume_job(selected_id)
        st.rerun()

    st.subheader("Artifacts")
    artifacts = database.list_artifacts(selected_id)
    if not artifacts:
        st.info("This Phase 4 orchestration job has no completed output artifact.")
    artifact_root = (ROOT / "data" / "artifacts").resolve()
    for artifact in artifacts:
        st.write(f"{artifact.kind}: {artifact.path}")
        path = Path(artifact.path)
        resolved = (path if path.is_absolute() else ROOT / path).resolve()
        if resolved.is_relative_to(artifact_root) and resolved.is_file():
            st.download_button(
                f"Download {artifact.kind}",
                data=resolved.read_bytes(),
                file_name=resolved.name,
                key=f"artifact_{artifact.id}",
            )


st.set_page_config(page_title="Fashion CMS Upload Generator", layout="wide")
page = st.navigation(
    [
        st.Page(cms_workbook_page, title="CMS Generator", default=True),
        st.Page(image_downloader_page, title="Image Downloader"),
        st.Page(attribute_registry_page, title="Attribute Registry"),
        st.Page(job_history_page, title="Job History"),
    ]
)
page.run()
