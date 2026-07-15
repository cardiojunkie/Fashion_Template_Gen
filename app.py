from hashlib import sha256
from pathlib import Path

import streamlit as st

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
from fashion_cms.models import Severity, ValidationIssue
from fashion_cms.registry import load_registry


ROOT = Path(__file__).resolve().parent
IMAGE_BATCH_STATE = "image_download_batch"
IMAGE_WORKBOOK_DIGEST_STATE = "image_download_workbook_digest"
registry = load_registry(ROOT / "config" / "attribute_registry.xlsx")
set_names = {
    row.attribute_set_id: row.attribute_set_name for row in registry.attribute_sets
}


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

    workbook_result = parse_input_workbook(
        workbook_upload.getvalue(), workbook_upload.name
    )
    image_result = parse_uploaded_images(
        tuple((upload.name, upload.getvalue()) for upload in image_uploads),
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
    else:
        st.error("Resolve critical findings before processing or download.")


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


st.set_page_config(page_title="Fashion CMS Upload Generator", layout="wide")
page = st.navigation(
    [
        st.Page(cms_workbook_page, title="CMS Workbook", default=True),
        st.Page(image_downloader_page, title="Image Downloader"),
    ]
)
page.run()
