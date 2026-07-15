from pathlib import Path

import streamlit as st

from fashion_cms.excel_service import (
    SYSTEM_COPY_FIELDS,
    build_blank_cms_workbook,
    parse_input_workbook,
)
from fashion_cms.image_service import open_oriented_image, parse_uploaded_images
from fashion_cms.models import Severity, ValidationIssue
from fashion_cms.registry import load_registry


ROOT = Path(__file__).resolve().parent
registry = load_registry(ROOT / "config" / "attribute_registry.xlsx")
set_names = {row.attribute_set_id: row.attribute_set_name for row in registry.attribute_sets}

st.set_page_config(page_title="Fashion CMS Upload Generator", layout="wide")
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


if workbook_upload is not None:
    workbook_result = parse_input_workbook(workbook_upload.getvalue(), workbook_upload.name)
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
                    caption=f"{image.sku} · image {image.ordinal} · {image.width}×{image.height}",
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
else:
    st.info("Upload an .xlsx workbook to begin local validation.")
