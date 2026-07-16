from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path

import streamlit as st

from fashion_cms.database import InvalidJobEdit, InvalidStateTransition, JobDatabase
from fashion_cms.evaluation import load_thresholds
from fashion_cms.config import (
    ResourceLimits,
    load_pricing,
    maximum_job_cost,
    usage_cost,
)
from fashion_cms.catalog_service import (
    CONTENT_PROMPT_VERSION,
    CONTENT_SCHEMA_VERSION,
    build_cms_workbook,
    build_qc_report,
    fake_catalog_client,
    generate_catalog_batch,
    model_year_schema_warnings,
)
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
from fashion_cms.jobs import JobService, summarize_job_usage
from fashion_cms.llm_service import (
    LLMError,
    NVIDIA_ADAPTER_VERSION,
    NVIDIA_CACHE_KEY,
    NVIDIA_CHAT_COMPLETIONS_URL,
    NVIDIA_IMAGE_DETAIL,
    NVIDIA_MODEL,
    NvidiaInklingClient,
    NvidiaSettings,
    test_nvidia_connection,
)
from fashion_cms.provider_service import (
    ProviderProtocol,
    ProviderStore,
    RoutePurpose,
)
from fashion_cms.models import (
    AnalysisMode,
    ImageResult,
    JobStatus,
    Severity,
    UploadedImage,
    ValidationIssue,
    WorkbookResult,
    WorkItemStatus,
)
from fashion_cms.registry import configuration_issues, load_registry, profile_ids
from fashion_cms.release_gates import load_report
from fashion_cms.review import (
    ProposalStatus,
    ReviewAction,
    accepted_facts,
    bulk_accept_safe,
    load_review_items,
    persist_review_decision,
    unresolved_review_items,
)
from fashion_cms.topwear_extraction import (
    ATTRIBUTE_PROMPT_VERSION,
    ATTRIBUTE_SCHEMA_VERSION,
    PROMPT_VERSION as TOPWEAR_PROMPT_VERSION,
    SCHEMA_VERSION as TOPWEAR_SCHEMA_VERSION,
    ExtractionRecord,
    applicable_attribute_headers,
    cached_attribute_item_keys,
    run_attribute_job,
)
from fashion_cms.variant_service import (
    CacheContext,
    RequestPlan,
    VariantGroup,
    build_request_plan,
    build_variant_groups,
)


ROOT = Path(__file__).resolve().parent
IMAGE_BATCH_STATE = "image_download_batch"
IMAGE_WORKBOOK_DIGEST_STATE = "image_download_workbook_digest"
CMS_JOB_STATE = "cms_job_id"
CMS_SOURCE_DIGEST_STATE = "cms_source_digest"
CMS_INPUT_DIGEST_STATE = "cms_input_digest"
CMS_RUN_STATE = "cms_run_job_id"
CMS_REVIEW_STATE = "cms_review_job_id"
CMS_PROGRESS_STATE = "cms_progress"
NVIDIA_CONNECTION_STATE = "nvidia_connection"
DEFAULT_DATABASE_PATH = ROOT / "data" / "fashion_cms.sqlite3"
PRICING_PATH = ROOT / "config" / "model_pricing.json"
THRESHOLDS_PATH = ROOT / "config" / "evaluation_thresholds.json"
RELEASE_REPORT_PATH = ROOT / "docs" / "releases" / "0.1.0-rc1" / "release-gates.json"
LEGACY_EXTRACTION_CONTRACTS = frozenset(
    {
        ("topwear-extraction-v1", "topwear-structured-output-v1"),
        ("attribute-extraction-v1", "attribute-structured-output-v1"),
    }
)
registry = load_registry(ROOT / "config" / "attribute_registry.xlsx")
set_names = {
    row.attribute_set_id: row.attribute_set_name for row in registry.attribute_sets
}


def _contract_versions(attribute_set: str) -> tuple[str, str]:
    return (
        (TOPWEAR_PROMPT_VERSION, TOPWEAR_SCHEMA_VERSION)
        if attribute_set == "topwear"
        else (ATTRIBUTE_PROMPT_VERSION, ATTRIBUTE_SCHEMA_VERSION)
    )


def _fake_model(attribute_set: str) -> str:
    return "phase5-fake" if attribute_set == "topwear" else "phase7-fake"


def _is_extraction_job(job) -> bool:
    return (
        job.context.prompt_version,
        job.context.schema_version,
    ) == _contract_versions(job.attribute_set)


def _is_legacy_extraction_job(job) -> bool:
    return (
        job.context.prompt_version,
        job.context.schema_version,
    ) in LEGACY_EXTRACTION_CONTRACTS


@st.cache_resource
def get_database(path: str) -> JobDatabase:
    return JobDatabase(path)


def job_database() -> JobDatabase:
    path = os.environ.get("FASHION_CMS_DB_PATH", str(DEFAULT_DATABASE_PATH))
    return get_database(path)


def provider_store() -> ProviderStore:
    return ProviderStore(job_database())


def _source_digest(
    workbook: bytes,
    images: tuple[tuple[str, bytes], ...],
    attribute_set: str,
    configuration: str = "",
) -> str:
    digest = sha256(f"{attribute_set}\0{configuration}".encode())
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


@dataclass(frozen=True)
class ExtractionChecklist:
    passed: tuple[str, ...]
    action_required: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.action_required


def _nvidia_connection_passed(settings: NvidiaSettings) -> bool:
    state = st.session_state.get(NVIDIA_CONNECTION_STATE)
    return bool(
        settings.connection_fingerprint
        and isinstance(state, dict)
        and state.get("fingerprint") == settings.connection_fingerprint
    )


def _show_nvidia_connection(settings: NvidiaSettings) -> bool:
    st.subheader("NVIDIA Inkling connection")
    st.caption(
        f"Fixed model: {NVIDIA_MODEL}. The test sends one small image and may incur a charge."
    )
    if not settings.enabled:
        st.info(settings.disabled_reason)
        st.session_state.pop(NVIDIA_CONNECTION_STATE, None)
    if st.button(
        "Test NVIDIA Connection",
        disabled=not settings.enabled,
        key="test_nvidia_connection",
    ):
        client = NvidiaInklingClient(settings)
        try:
            with st.spinner("Testing NVIDIA vision and structured output…"):
                response = test_nvidia_connection(client)
        except Exception as exc:
            st.session_state.pop(NVIDIA_CONNECTION_STATE, None)
            st.error(f"NVIDIA connection test failed: {exc}")
        else:
            st.session_state[NVIDIA_CONNECTION_STATE] = {
                "fingerprint": settings.connection_fingerprint,
                "request_id": response.request_id,
                "usage": response.usage,
            }
        finally:
            client.close()
    passed = _nvidia_connection_passed(settings)
    if passed:
        state = st.session_state[NVIDIA_CONNECTION_STATE]
        st.success("NVIDIA connection, vision, and structured output passed for this session.")
        st.caption(
            f"Request ID: {state.get('request_id') or 'unavailable'} · "
            f"usage: {state.get('usage') or 'unavailable'}"
        )
    return passed


def _vision_context(
    attribute_set: str,
    product_profile: str | None,
) -> CacheContext:
    prompt_version, schema_version = _contract_versions(attribute_set)
    return CacheContext(
        attribute_set=attribute_set,
        product_profile=product_profile,
        registry_version=registry.fingerprint,
        prompt_version=prompt_version,
        schema_version=schema_version,
        model_identifier=NVIDIA_MODEL,
        image_detail=NVIDIA_IMAGE_DETAIL,
        provider_cache_key=NVIDIA_CACHE_KEY,
    )


def _configure_analysis_modes(
    rows,
    images: tuple[UploadedImage, ...],
    product_profile: str | None,
    *,
    key_prefix: str,
    stored_groups: tuple[VariantGroup, ...] = (),
    editable: bool = True,
) -> tuple[tuple[VariantGroup, ...], tuple[str, ...]]:
    defaults = stored_groups or build_variant_groups(
        rows, images, product_profile=product_profile
    )
    modes: dict[str, AnalysisMode] = {}
    representatives: dict[str, str] = {}
    errors = []
    for group in defaults:
        label = group.base_code or f"blank base code · {group.skus[0]}"
        with st.expander(label, expanded=len(defaults) == 1):
            mode = st.selectbox(
                f"Analysis mode for {label}",
                tuple(AnalysisMode),
                index=tuple(AnalysisMode).index(group.analysis_mode),
                format_func=lambda value: value.value,
                key=f"cms_mode_{key_prefix}_{sha256(group.key.encode()).hexdigest()[:12]}",
                disabled=not editable,
            )
            representative = st.selectbox(
                f"Representative SKU for {label}",
                group.skus,
                index=group.skus.index(group.representative_sku),
                key=f"cms_representative_{key_prefix}_{sha256(group.key.encode()).hexdigest()[:12]}",
                disabled=not editable or mode != AnalysisMode.BASE_CODE_SIZE_ONLY,
            )
            if mode == AnalysisMode.BASE_CODE_SIZE_ONLY:
                st.warning(
                    "Use size-only only when these SKUs differ by size and show the same "
                    "visible product."
                )
                for warning in group.size_only_warnings:
                    st.warning(warning)
                confirmed = not editable or st.checkbox(
                    f"Confirm BASE_CODE_SIZE_ONLY for {label}",
                    key=f"cms_confirm_mode_{key_prefix}_{sha256(group.key.encode()).hexdigest()[:12]}",
                )
                if not confirmed:
                    errors.append(f"Confirm analysis mode for base code {label}")
            modes[group.key] = mode
            representatives[group.key] = representative
    return (
        build_variant_groups(
            rows,
            images,
            modes=modes,
            representatives=representatives,
            product_profile=product_profile,
        ),
        tuple(errors),
    )


def _extraction_checklist(
    workbook_result: WorkbookResult | None,
    image_result: ImageResult | None,
    attribute_set: str | None,
    product_profile: str | None,
    profile_confirmed: bool,
    plan: RequestPlan | None,
    settings: NvidiaSettings,
    connection_passed: bool,
    limits: ResourceLimits,
    *,
    planned_attempts: int,
    attempted_calls: int = 0,
    estimated_cost=None,
    mode_errors: tuple[str, ...] = (),
    registry_errors: tuple[str, ...] = (),
) -> ExtractionChecklist:
    passed = []
    required = []
    if workbook_result is None:
        required.append("Upload input workbook")
    else:
        critical = tuple(
            issue for issue in workbook_result.issues if issue.severity == Severity.CRITICAL
        )
        if workbook_result.ready:
            passed.extend(
                (
                    "Workbook validated",
                    "All required columns present",
                    f"{len(workbook_result.rows):,} SKUs found",
                )
            )
        else:
            required.extend(f"Workbook: {issue.message}" for issue in critical)
            if not workbook_result.rows and not critical:
                required.append("Add at least one valid SKU")

    if image_result is None:
        required.append("Upload images or an image ZIP")
    else:
        critical = tuple(
            issue for issue in image_result.issues if issue.severity == Severity.CRITICAL
        )
        required.extend(f"Images: {issue.message}" for issue in critical)
        if image_result.images:
            passed.append(f"{len(image_result.images):,} images matched")
        elif not critical:
            required.append("Upload at least one valid SKU image")

    if attribute_set:
        passed.append(f"Attribute set: {set_names[attribute_set]}")
    else:
        required.append("Select an attribute set")
    if product_profile and profile_confirmed:
        passed.append(f"Product profile: {product_profile}")
    elif not product_profile:
        required.append("Select a product profile")
    else:
        required.append("Confirm the selected product profile")
    required.extend(f"Attribute configuration: {error}" for error in registry_errors)

    if plan is not None and plan.groups:
        passed.append(f"Analysis modes valid for {len(plan.groups):,} base-code groups")
        passed.append(f"{len(plan.items):,} vision requests planned")
        labels = {group.key: group.base_code or group.skus[0] for group in plan.groups}
        for item in plan.items:
            if item.image_assets:
                continue
            if item.analysis_mode == AnalysisMode.PER_SKU:
                required.append(f"SKU {item.representative_sku} has no image")
            else:
                required.append(
                    f"Representative SKU {item.representative_sku} for base code "
                    f"{labels[item.group_key]} has no image"
                )
    elif workbook_result is not None and workbook_result.ready:
        required.append("Configure a valid analysis mode for every base-code group")
    required.extend(mode_errors)

    if not settings.enabled:
        required.append("Configure NVIDIA_API_KEY in the server environment")
    else:
        passed.append(f"Vision model configured: NVIDIA · {NVIDIA_MODEL}")
        passed.append("NVIDIA authentication key is available")
        if connection_passed:
            passed.append("NVIDIA connection, vision, and structured output passed")
        else:
            required.append("Pass Test NVIDIA Connection for this session")

    if plan is not None:
        if attempted_calls + planned_attempts <= limits.calls_per_job:
            passed.append(
                f"Request limit available: {planned_attempts:,} planned attempts, "
                f"{limits.calls_per_job - attempted_calls:,} remaining"
            )
        else:
            required.append(
                f"Reduce planned attempts below the remaining request limit of "
                f"{max(0, limits.calls_per_job - attempted_calls):,}"
            )
        if estimated_cost is not None and planned_attempts:
            if limits.maximum_estimated_cost is None:
                required.append("Configure an approved maximum estimated extraction cost")
            elif estimated_cost > limits.maximum_estimated_cost:
                required.append(
                    f"Estimated extraction cost exceeds the configured limit of "
                    f"{limits.maximum_estimated_cost}"
                )
            else:
                passed.append("Estimated extraction cost is within the configured limit")
    return ExtractionChecklist(tuple(passed), tuple(dict.fromkeys(required)))


def _show_extraction_checklist(checklist: ExtractionChecklist) -> None:
    st.markdown("**Ready:**")
    if checklist.passed:
        st.success("\n\n".join(f"✓ {item}" for item in checklist.passed))
    else:
        st.info("No readiness checks have passed yet.")
    if checklist.action_required:
        st.markdown("**Action required:**")
        st.error("\n\n".join(f"✗ {item}" for item in checklist.action_required))


def _create_extraction_job(
    workbook_result: WorkbookResult,
    image_result: ImageResult,
    attribute_set: str,
    product_profile: str,
    groups: tuple[VariantGroup, ...],
) -> str:
    prompt_version, schema_version = _contract_versions(attribute_set)
    job_id = JobService(job_database()).create_job(
        workbook_result.rows,
        image_result.images,
        attribute_set=attribute_set,
        product_profile=product_profile,
        registry_version=registry.fingerprint,
        prompt_version=prompt_version,
        schema_version=schema_version,
        model_identifier=NVIDIA_MODEL,
        image_detail=NVIDIA_IMAGE_DETAIL,
        provider_cache_key=NVIDIA_CACHE_KEY,
        modes={group.key: group.analysis_mode for group in groups},
        representatives={group.key: group.representative_sku for group in groups},
    )
    provider_store().record_job_snapshot(
        job_id,
        RoutePurpose.VISION_EXTRACTION,
        provider=None,
        display_name="NVIDIA NIM · Inkling",
        protocol=ProviderProtocol.OPENAI_CHAT_COMPLETIONS.value,
        base_url_fingerprint=sha256(NVIDIA_CHAT_COMPLETIONS_URL.encode()).hexdigest()[:16],
        model_id=NVIDIA_MODEL,
        provider_configuration_version=0,
        adapter_version=NVIDIA_ADAPTER_VERSION,
        prompt_version=prompt_version,
        schema_version=schema_version,
    )
    return job_id


def _run_extraction_job(
    job_id: str,
    images: tuple[UploadedImage, ...],
    settings: NvidiaSettings,
) -> None:
    database = job_database()
    job = database.get_job(job_id)
    if (
        not settings.enabled
        or not _nvidia_connection_passed(settings)
        or NVIDIA_CACHE_KEY != job.context.provider_cache_key
        or NVIDIA_MODEL != job.context.model_identifier
        or NVIDIA_IMAGE_DETAIL != job.context.image_detail
    ):
        raise ValueError("The tested NVIDIA runtime no longer matches this job.")
    items = database.list_work_items(job_id)
    retry_failed = any(item.status == WorkItemStatus.FAILED for item in items)
    if job.cancel_requested:
        database.clear_cancellation(job_id)
    progress_bar = st.progress(0.0)
    progress_text = st.empty()

    def update(done: int, total: int, item) -> None:
        st.session_state[CMS_PROGRESS_STATE] = {
            "job_id": job_id,
            "done": done,
            "total": total,
            "skus": item.represented_skus,
        }
        progress_bar.progress(done / total if total else 1.0)
        progress_text.caption(
            f"Processed {done} of {total}: {', '.join(item.represented_skus)}"
        )

    client = NvidiaInklingClient(settings)
    prompt_version, schema_version = _contract_versions(job.attribute_set)
    try:
        with st.spinner("Extracting product observations…"):
            run_attribute_job(
                database,
                job_id,
                client,
                images,
                registry,
                retry_failed=retry_failed,
                progress=update,
                expected_prompt_version=prompt_version,
                expected_schema_version=schema_version,
            )
    finally:
        client.close()


def cms_workbook_page() -> None:
    st.title("Fashion CMS Upload Generator")
    st.write("Validate SKU data and images, extract canonical product facts, and continue to review.")

    attribute_set = st.selectbox(
        "CMS attribute set",
        tuple(registry.mappings_by_set),
        format_func=lambda set_id: set_names[set_id],
    )
    profiles = profile_ids(registry, attribute_set)
    product_profile = st.selectbox(
        "Product profile",
        profiles,
        index=None if attribute_set == "mens_accessories" else 0,
        placeholder="Select a profile",
        help="Profiles control which fields are sent for extraction; they are not CMS columns.",
    )
    profile_confirmed = attribute_set == "topwear"
    if product_profile:
        extraction_headers = applicable_attribute_headers(
            registry, attribute_set, product_profile
        )
        st.caption(
            f"Extraction-field preview · {len(extraction_headers)} applicable fields · "
            f"{', '.join(extraction_headers)}"
        )
        if attribute_set != "topwear":
            profile_confirmed = st.checkbox(
                "Confirm selected product profile",
                key=f"confirm_profile_{attribute_set}_{product_profile}",
            )
    elif attribute_set == "mens_accessories":
        st.warning("Select and confirm a Men's Accessories profile before extraction.")
    for issue in configuration_issues(registry, attribute_set):
        st.warning(f"Configuration incomplete: {issue}")
    st.info(
        f"{set_names[attribute_set]} · extract, review canonical facts, generate factual copy, "
        "and export separate CMS and QC workbooks."
    )
    nvidia_settings = NvidiaSettings.from_env()
    connection_passed = _show_nvidia_connection(nvidia_settings)

    workbook_upload = st.file_uploader("Input workbook", type=["xlsx"])
    image_uploads = st.file_uploader(
        "Product images or ZIP files",
        type=["jpg", "jpeg", "png", "webp", "zip"],
        accept_multiple_files=True,
    )
    workbook_content = workbook_upload.getvalue() if workbook_upload is not None else b""
    image_upload_data = tuple(
        (upload.name, upload.getvalue()) for upload in image_uploads
    )
    input_digest = _source_digest(
        workbook_content,
        image_upload_data,
        attribute_set,
        "|".join((product_profile or "", NVIDIA_CACHE_KEY, registry.fingerprint)),
    )
    if st.session_state.get(CMS_INPUT_DIGEST_STATE) != input_digest:
        st.session_state[CMS_INPUT_DIGEST_STATE] = input_digest
        for key in (
            CMS_JOB_STATE,
            CMS_SOURCE_DIGEST_STATE,
            CMS_RUN_STATE,
            CMS_REVIEW_STATE,
            CMS_PROGRESS_STATE,
        ):
            st.session_state.pop(key, None)

    try:
        limits = ResourceLimits.from_env()
        limits_error = None
    except (TypeError, ValueError) as exc:
        limits = ResourceLimits()
        limits_error = str(exc)
    workbook_result = (
        parse_input_workbook(workbook_content, workbook_upload.name)
        if workbook_upload is not None
        else None
    )
    image_result = (
        parse_uploaded_images(
            image_upload_data,
            tuple(row.sku for row in workbook_result.rows),
            limits=limits,
        )
        if workbook_result is not None
        else None
    )

    st.subheader("Validate workbook and images")
    if workbook_result is None:
        st.info("Upload an .xlsx workbook to begin local validation.")
    else:
        show_issues(
            workbook_result.issues
            + (image_result.issues if image_result is not None else ())
        )
    if limits_error:
        st.error(f"Resource-limit configuration is invalid: {limits_error}")

    headers = registry.mappings_by_set[attribute_set]
    if workbook_result is not None and workbook_result.rows:
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

    st.subheader("SKU/image association preview")
    if workbook_result is not None and workbook_result.rows:
        matched_images = image_result.images if image_result is not None else ()
        st.dataframe(
            [
                {
                    "SKU": row.sku,
                    "Base code": row.base_code or "",
                    "Matched images": sum(image.sku == row.sku for image in matched_images),
                    "Files": ", ".join(
                        image.filename for image in matched_images if image.sku == row.sku
                    ),
                }
                for row in workbook_result.rows
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("SKU/image associations will appear after workbook validation.")
    if image_result is not None and image_result.images:
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

    job = None
    job_id = st.session_state.get(CMS_JOB_STATE)
    if job_id:
        try:
            job = job_database().get_job(job_id)
        except Exception:
            st.session_state.pop(CMS_JOB_STATE, None)
            job_id = None

    st.subheader("Configure PER_SKU or BASE_CODE_SIZE_ONLY mode")
    groups: tuple[VariantGroup, ...] = ()
    mode_errors: tuple[str, ...] = ()
    plan = None
    if workbook_result is not None and workbook_result.ready:
        stored_groups = job_database().load_groups(job_id) if job_id else ()
        groups, mode_errors = _configure_analysis_modes(
            workbook_result.rows,
            image_result.images if image_result is not None else (),
            product_profile,
            key_prefix=input_digest[:16],
            stored_groups=stored_groups,
            editable=job is None,
        )
        plan = build_request_plan(
            groups, _vision_context(attribute_set, product_profile)
        )
    else:
        st.info("Analysis modes will be available after workbook validation.")

    mode_configuration = "|".join(
        f"{group.key}:{group.analysis_mode.value}:{group.representative_sku}"
        for group in groups
    )
    source_digest = sha256(f"{input_digest}\0{mode_configuration}".encode()).hexdigest()
    if st.session_state.get(CMS_SOURCE_DIGEST_STATE) != source_digest:
        st.session_state[CMS_SOURCE_DIGEST_STATE] = source_digest
        if job is not None:
            st.session_state.pop(CMS_JOB_STATE, None)
            st.session_state.pop(CMS_RUN_STATE, None)
            st.session_state.pop(CMS_REVIEW_STATE, None)
            job = None
            job_id = None

    stored_items = job_database().list_work_items(job_id) if job_id else ()
    cached_keys: frozenset[str] = frozenset()
    if job_id and job is not None and job.context.registry_version == registry.fingerprint:
        try:
            cached_keys = cached_attribute_item_keys(job_database(), job_id, registry)
        except ValueError:
            cached_keys = frozenset()
    if job_id:
        outstanding_keys = {
            item.key
            for item in stored_items
            if item.status
            in {WorkItemStatus.PENDING, WorkItemStatus.RUNNING, WorkItemStatus.FAILED}
        }
        remaining_plan = tuple(
            item
            for item in (plan.items if plan is not None else ())
            if item.key in outstanding_keys and item.key not in cached_keys
        )
    else:
        remaining_plan = plan.items if plan is not None else ()
    planned_attempts = len(remaining_plan) * (limits.model_retries + 1)
    planned_image_attempts = sum(len(item.image_assets) for item in remaining_plan) * (
        limits.model_retries + 1
    )
    model_pricing = load_pricing(PRICING_PATH).for_model(NVIDIA_MODEL)
    estimated_cost = maximum_job_cost(
        model_pricing,
        request_count=planned_attempts,
        image_count=planned_image_attempts,
    )

    st.subheader("Planned vision-call count")
    final_count = len(stored_items) if stored_items else len(plan.items) if plan else 0
    metric_columns = st.columns(3)
    metric_columns[0].metric("Final planned vision calls", final_count)
    metric_columns[1].metric("Cached / calls required", f"{len(cached_keys)} / {len(remaining_plan)}")
    metric_columns[2].metric("Maximum remaining attempts", planned_attempts)
    st.caption(
        f"Extraction runtime: NVIDIA NIM · {NVIDIA_MODEL} · "
        f"image detail {NVIDIA_IMAGE_DETAIL}."
    )
    if estimated_cost is not None and model_pricing is not None:
        st.metric(
            "Estimated maximum extraction cost",
            f"{model_pricing.currency} {estimated_cost:.4f}",
        )
    else:
        st.caption(
            "Estimated extraction cost unavailable: no approved NVIDIA pricing is configured."
        )

    registry_errors = tuple(configuration_issues(registry, attribute_set))
    if limits_error:
        registry_errors += (f"Resource limits: {limits_error}",)
    checklist = _extraction_checklist(
        workbook_result,
        image_result,
        attribute_set,
        product_profile,
        profile_confirmed,
        plan,
        nvidia_settings,
        connection_passed,
        limits,
        planned_attempts=planned_attempts,
        attempted_calls=job.attempted_model_calls if job is not None else 0,
        estimated_cost=estimated_cost,
        mode_errors=mode_errors,
        registry_errors=registry_errors,
    )
    may_incur_charges = bool(remaining_plan and nvidia_settings.enabled)
    charges_confirmed = not may_incur_charges or st.checkbox(
        "I confirm these provider calls may incur charges.",
        key=f"cms_charge_confirmation_{source_digest[:16]}",
    )
    if may_incur_charges and not charges_confirmed:
        checklist = ExtractionChecklist(
            checklist.passed,
            (*checklist.action_required, "Confirm possible provider charges"),
        )

    _show_extraction_checklist(checklist)

    failures = sum(item.status == WorkItemStatus.FAILED for item in stored_items)
    unfinished = sum(
        item.status in {WorkItemStatus.PENDING, WorkItemStatus.RUNNING}
        for item in stored_items
    )
    run_requested = bool(job_id and st.session_state.get(CMS_RUN_STATE) == job_id)
    if run_requested:
        action_label = "Extraction Running…"
    elif job is not None and job.cancel_requested:
        action_label = "Resume Extraction"
    elif failures:
        action_label = "Retry Failed Extractions"
    elif job is not None and job.status == JobStatus.RUNNING:
        action_label = "Resume Extraction"
    else:
        action_label = "Run Data Extraction"
    extraction_finished = bool(stored_items) and not failures and not unfinished
    action_clicked = False
    if not extraction_finished:
        action_clicked = st.button(
            action_label,
            type="primary",
            key=f"cms_extraction_action_{source_digest[:16]}",
            disabled=not checklist.ready or run_requested,
        )
    if run_requested or (job is not None and job.status == JobStatus.RUNNING):
        if st.button("Cancel Extraction", key=f"cms_cancel_{job_id}"):
            JobService(job_database()).request_cancellation(job_id)
            st.session_state.pop(CMS_RUN_STATE, None)
            st.rerun()

    if action_clicked:
        fresh_settings = NvidiaSettings.from_env()
        fresh_connection_passed = _nvidia_connection_passed(fresh_settings)
        fresh_workbook = (
            parse_input_workbook(workbook_content, workbook_upload.name)
            if workbook_upload is not None
            else None
        )
        fresh_images = (
            parse_uploaded_images(
                image_upload_data,
                tuple(row.sku for row in fresh_workbook.rows),
                limits=limits,
            )
            if fresh_workbook is not None
            else None
        )
        fresh_groups = (
            build_variant_groups(
                fresh_workbook.rows,
                fresh_images.images if fresh_images is not None else (),
                modes={group.key: group.analysis_mode for group in groups},
                representatives={group.key: group.representative_sku for group in groups},
                product_profile=product_profile,
            )
            if fresh_workbook is not None and fresh_workbook.ready
            else ()
        )
        fresh_plan = (
            build_request_plan(
                fresh_groups,
                _vision_context(attribute_set, product_profile),
            )
            if fresh_groups
            else None
        )
        fresh_checklist = _extraction_checklist(
            fresh_workbook,
            fresh_images,
            attribute_set,
            product_profile,
            profile_confirmed,
            fresh_plan,
            fresh_settings,
            fresh_connection_passed,
            limits,
            planned_attempts=planned_attempts,
            attempted_calls=job.attempted_model_calls if job is not None else 0,
            estimated_cost=estimated_cost,
            mode_errors=mode_errors,
            registry_errors=registry_errors,
        )
        if not fresh_checklist.ready or not charges_confirmed:
            st.error("Data extraction was not started because server-side validation failed.")
        else:
            if job_id is None:
                assert (
                    fresh_workbook is not None
                    and fresh_images is not None
                    and product_profile is not None
                )
                job_id = _create_extraction_job(
                    fresh_workbook,
                    fresh_images,
                    attribute_set,
                    product_profile,
                    fresh_groups,
                )
                st.session_state[CMS_JOB_STATE] = job_id
            st.session_state[CMS_RUN_STATE] = job_id
            st.session_state.pop(CMS_REVIEW_STATE, None)
            st.rerun()

    if run_requested:
        try:
            _run_extraction_job(
                job_id,
                image_result.images if image_result is not None else (),
                NvidiaSettings.from_env(),
            )
        except Exception:
            st.error("Extraction could not complete safely. Inspect the persisted item results.")
        finally:
            st.session_state.pop(CMS_RUN_STATE, None)
        st.rerun()

    st.subheader("Processing progress")
    progress = st.session_state.get(CMS_PROGRESS_STATE)
    if isinstance(progress, dict) and progress.get("job_id") == job_id:
        done = int(progress.get("done", 0))
        total = int(progress.get("total", 0))
        st.progress(done / total if total else 1.0)
        st.caption(f"Completed {done} of {total} work items.")
    elif job_id:
        st.caption("No work item has completed in this run yet.")
    else:
        st.caption("Progress will appear after extraction starts.")

    st.subheader("Extraction result summary")
    if job_id:
        _show_attribute_results(job_database(), job_id)
        stored_items = job_database().list_work_items(job_id)
    else:
        st.info("No extraction results yet.")
    successful = sum(
        item.status in {WorkItemStatus.COMPLETED, WorkItemStatus.REVIEW_REQUIRED}
        for item in stored_items
    )
    if st.button(
        "Continue to Review",
        type="primary" if successful else "secondary",
        key=f"cms_continue_review_{source_digest[:16]}",
        disabled=successful == 0,
    ):
        st.session_state[CMS_REVIEW_STATE] = job_id
        st.rerun()
    if successful and st.session_state.get(CMS_REVIEW_STATE) == job_id:
        st.subheader("Review")
        _show_attribute_review(job_database(), job_id)

    if workbook_result is not None and workbook_result.ready:
        output = build_blank_cms_workbook(workbook_result.rows, headers)
        st.download_button(
            "Download blank CMS workbook",
            data=output,
            file_name=f"cms_{attribute_set}_blank.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def show_job_plan(job_id: str, uploaded_images: tuple[UploadedImage, ...] = ()) -> None:
    database = job_database()
    service = JobService(database)
    job = database.get_job(job_id)
    groups = database.load_groups(job_id)
    items = database.list_work_items(job_id)
    editable = job.status == JobStatus.READY
    extraction_job = _is_extraction_job(job)
    current_registry = job.context.registry_version == registry.fingerprint
    cached_keys = (
        cached_attribute_item_keys(database, job_id, registry)
        if extraction_job and current_registry
        else frozenset()
    )
    if extraction_job and not current_registry:
        st.warning(
            "The active registry changed after extraction. Stored decisions are revalidated; "
            "unchanged extraction cache entries are not reused."
        )

    st.subheader(f"Variant analysis job · {job_id}")
    st.caption(
        f"Status: {job.status.value} · profile: {job.product_profile or '(missing)'} · "
        "selections and work plan are stored in SQLite."
    )
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
                "Detected profiles": ", ".join(group.detected_product_profiles),
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
    limits = ResourceLimits.from_env()
    pricing = load_pricing(PRICING_PATH)
    model_pricing = pricing.for_model(job.context.model_identifier)
    expected_copy_calls = sum(len(group.skus) for group in groups)
    vision_attempts = len(items) * (limits.model_retries + 1)
    copy_attempts = expected_copy_calls * 2 * (limits.model_retries + 1)
    maximum_cost = maximum_job_cost(
        model_pricing,
        request_count=vision_attempts + copy_attempts,
        image_count=sum(len(group.images) for group in groups)
        * (limits.model_retries + 1),
    )
    metrics = st.columns(8)
    metrics[0].metric("Base-code groups", len(groups))
    metrics[1].metric("SKUs", sum(len(group.skus) for group in groups))
    metrics[2].metric("Size-only groups", size_only_count)
    metrics[3].metric("Per-SKU groups", len(groups) - size_only_count)
    metrics[4].metric("Planned vision requests", len(items))
    metrics[5].metric("Cached / required", f"{len(cached_keys)} / {len(items) - len(cached_keys)}")
    metrics[6].metric("Expected text calls (max)", expected_copy_calls)
    metrics[7].metric(
        "Estimated maximum cost",
        f"{model_pricing.currency} {maximum_cost:.4f}"
        if maximum_cost is not None and model_pricing is not None
        else "Unavailable",
    )
    st.caption(
        f"Model: {job.context.model_identifier} · configured concurrency: "
        f"{limits.model_concurrency} · hard call limit: {limits.calls_per_job}. "
        "The exact stored plan is checked again before extraction; cache hits make no API call."
    )
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
                "Status": (
                    "CACHED"
                    if item.status == WorkItemStatus.PENDING and item.key in cached_keys
                    else (
                        "REQUEST_REQUIRED"
                        if item.status == WorkItemStatus.PENDING
                        else item.status.value
                    )
                ),
                "Cache hit": item.cache_hit,
            }
            for item in items
        ],
        hide_index=True,
        width="stretch",
    )
    if extraction_job:
        _show_attribute_results(database, job_id)
        _show_attribute_review(database, job_id)
    else:
        st.info("Extraction is blocked because this stored job uses an obsolete contract.")


def _show_attribute_results(database: JobDatabase, job_id: str) -> None:
    items = database.list_work_items(job_id)
    records = []
    for item in items:
        result = database.get_work_item_result(item)
        if result is None:
            continue
        try:
            records.append((item, ExtractionRecord.model_validate(result)))
        except Exception:
            continue
    if not records and not any(item.status == WorkItemStatus.FAILED for item in items):
        return

    success = sum(
        item.status in {WorkItemStatus.COMPLETED, WorkItemStatus.REVIEW_REQUIRED}
        for item in items
    )
    failures = sum(item.status == WorkItemStatus.FAILED for item in items)
    review_required = sum(item.status == WorkItemStatus.REVIEW_REQUIRED for item in items)
    warnings = sum(len(record.vision_result.warnings) for _, record in records)
    metrics = st.columns(5)
    metrics[0].metric("Successful", success)
    metrics[1].metric("Cached", sum(item.cache_hit for item in items))
    metrics[2].metric("Failed", failures)
    metrics[3].metric("Review required", review_required)
    metrics[4].metric("Warnings", warnings)
    job = database.get_job(job_id)
    pricing = load_pricing(PRICING_PATH)
    usage = summarize_job_usage(
        database, job_id, pricing.for_model(job.context.model_identifier)
    )
    st.caption(
        f"Calls attempted: {usage.attempted_calls} · successful: {usage.successful_calls} · "
        f"retries: {usage.retries} · failed: {usage.failed_calls} · cache hits: "
        f"{usage.cache_hits} · usage: {usage.usage or 'unavailable'} · cost: "
        f"{usage.cost if usage.cost is not None else 'unavailable'}"
    )
    st.dataframe(
        [
            {
                "Base-code group": unit.group_key,
                "SKUs": ", ".join(unit.skus),
                "Attempted calls": unit.attempted_calls,
                "Retries": unit.retries,
                "Cache hit": unit.cache_hit,
                "Usage": unit.usage,
                "Cost": unit.cost if unit.cost is not None else "Unavailable",
            }
            for unit in usage.units
        ],
        hide_index=True,
        width="stretch",
    )

    observations = []
    messages = []
    for item, record in records:
        vision = record.vision_result
        for observation in vision.shared_attributes:
            observations.append(
                {
                    "Work item": item.position + 1,
                    "SKU / scope": f"Shared from {vision.representative_sku}",
                    "Header": observation.header,
                    "Raw value": observation.raw_value or "",
                    "Canonical value": observation.canonical_value or "",
                    "Status": observation.status.value,
                    "Evidence type": observation.evidence_type.value,
                    "Evidence references": ", ".join(observation.evidence_refs),
                    "Confidence": observation.confidence.value if observation.confidence else "",
                    "Normalization": observation.normalization_rule or "",
                    "Note": observation.note or "",
                }
            )
        for sku, sku_observations in vision.sku_attributes.items():
            for observation in sku_observations:
                observations.append(
                    {
                        "Work item": item.position + 1,
                        "SKU / scope": sku,
                        "Header": observation.header,
                        "Raw value": observation.raw_value or "",
                        "Canonical value": observation.canonical_value or "",
                        "Status": observation.status.value,
                        "Evidence type": observation.evidence_type.value,
                        "Evidence references": ", ".join(observation.evidence_refs),
                        "Confidence": (
                            observation.confidence.value if observation.confidence else ""
                        ),
                        "Normalization": observation.normalization_rule or "",
                        "Note": observation.note or "",
                    }
                )
        messages.extend(
            {"Work item": item.position + 1, "Type": "Warning", "Message": message}
            for message in vision.warnings
        )
        messages.extend(
            {"Work item": item.position + 1, "Type": "Conflict", "Message": message}
            for message in vision.conflicts
        )
    if observations:
        st.subheader("Read-only extracted observations and evidence")
        st.dataframe(observations, hide_index=True, width="stretch")
    if records:
        st.subheader("Conflicts and warnings")
    if messages:
        st.dataframe(messages, hide_index=True, width="stretch")
    elif records:
        st.info("No conflicts or warnings were returned for these work items.")


def _show_attribute_review(database: JobDatabase, job_id: str) -> None:
    job = database.get_job(job_id)
    attribute_set_name = set_names[job.attribute_set]
    items = database.list_work_items(job_id)
    if not items:
        return
    successful_statuses = {WorkItemStatus.COMPLETED, WorkItemStatus.REVIEW_REQUIRED}
    successful_skus = {
        sku
        for item in items
        if item.status in successful_statuses
        for sku in item.represented_skus
    }
    partial = any(item.status not in successful_statuses for item in items)
    if not successful_skus:
        st.info("No successful extraction rows are available for review or partial export.")
        return
    review_items = tuple(
        item
        for item in load_review_items(database, job_id, registry)
        if item.sku in successful_skus
    )
    if not review_items:
        return
    all_rows = database.load_rows(job_id)
    rows = tuple(row for row in all_rows if row.sku in successful_skus)

    st.subheader(f"{attribute_set_name} attribute review")
    if partial:
        st.warning(
            f"Partial job: review and export include only {len(rows):,} successful SKU(s). "
            "The QC workbook lists every failed or incomplete SKU."
        )
    for warning in model_year_schema_warnings(rows, attribute_set_name):
        st.warning(warning)
    filters = st.multiselect(
        "Review filters",
        (
            "Conflict",
            "Unmapped value",
            "Missing permitted value",
            "Image-inferred color",
            "Low confidence",
            "Unknown",
            "Invalid enum",
            "User-edited",
            "Review not completed",
        ),
        key=f"review_filters_{job_id}",
    )
    base_codes = tuple(dict.fromkeys(item.base_code or "(blank)" for item in review_items))
    skus = tuple(dict.fromkeys(item.sku for item in review_items))
    headers = tuple(dict.fromkeys(item.header for item in review_items))
    filter_columns = st.columns(3)
    selected_base = filter_columns[0].selectbox(
        "Base code", ("All", *base_codes), key=f"review_base_{job_id}"
    )
    selected_sku = filter_columns[1].selectbox(
        "SKU", ("All", *skus), key=f"review_sku_{job_id}"
    )
    selected_header = filter_columns[2].selectbox(
        "Attribute header", ("All", *headers), key=f"review_header_{job_id}"
    )

    def visible(item) -> bool:
        checks = {
            "Conflict": bool(item.conflict),
            "Unmapped value": item.proposal_status == ProposalStatus.UNMAPPED,
            "Missing permitted value": (
                registry.definitions_by_header[item.header].data_type.value == "ENUM"
                and item.proposal_status == ProposalStatus.UNMAPPED
            ),
            "Image-inferred color": item.image_inferred_color,
            "Low confidence": item.confidence is not None and item.confidence.value == "low",
            "Unknown": item.proposal_status == ProposalStatus.UNKNOWN,
            "Invalid enum": not item.decision_valid,
            "User-edited": item.review_action == ReviewAction.EDIT,
            "Review not completed": item.requires_review
            and (item.review_action is None or not item.decision_valid),
        }
        return (
            all(checks[selection] for selection in filters)
            and (selected_base == "All" or selected_base == (item.base_code or "(blank)"))
            and (selected_sku == "All" or selected_sku == item.sku)
            and (selected_header == "All" or selected_header == item.header)
        )

    filtered = tuple(item for item in review_items if visible(item))
    st.dataframe(
        [
            {
                "SKU": item.sku,
                "Base code": item.base_code or "",
                "Profile": item.product_profile or "",
                "Attribute": item.header,
                "Supplied/input": item.supplied_value or "",
                "Raw extracted": item.raw_value or "",
                "Proposed canonical": item.proposed_value or "",
                "Evidence type": item.evidence_type,
                "Evidence references": ", ".join(item.evidence_references),
                "Confidence": item.confidence.value if item.confidence else "",
                "Conflict": item.conflict or "",
                "Normalization": item.matching_method.value,
                "Suggestion": (
                    f"{item.fuzzy_suggestion} ({item.fuzzy_score:.3f})"
                    if item.fuzzy_suggestion and item.fuzzy_score is not None
                    else ""
                ),
                "Warning/note": item.warning or "",
                "Final value": item.final_value or "",
                "Review action": item.review_action.value if item.review_action else "",
            }
            for item in filtered
        ],
        hide_index=True,
        width="stretch",
    )
    st.caption(f"Showing {len(filtered):,} of {len(review_items):,} review items.")

    safe_count = sum(item.safe_for_bulk_accept for item in filtered)
    if st.button(
        f"Accept {safe_count} safe filtered proposals",
        disabled=safe_count == 0,
        key=f"bulk_review_{job_id}",
    ):
        bulk_accept_safe(database, filtered, registry)
        st.rerun()

    actionable = tuple(item for item in filtered if item.requires_review or item.review_action)
    if actionable:
        selected_key = st.selectbox(
            "Edit review item",
            tuple((item.sku, item.header) for item in actionable),
            format_func=lambda key: f"{key[0]} · {key[1]}",
            key=f"review_item_{job_id}",
        )
        selected = next(
            item for item in actionable if (item.sku, item.header) == selected_key
        )
        default_action = selected.review_action or (
            ReviewAction.ACCEPT if selected.proposed_value else ReviewAction.BLANK
        )
        action = st.selectbox(
            "Review action",
            tuple(ReviewAction),
            index=tuple(ReviewAction).index(default_action),
            format_func=lambda value: value.value,
            key=f"review_action_{job_id}_{selected.sku}_{selected.header}",
        )
        final_value = selected.final_value or selected.proposed_value or ""
        if action == ReviewAction.EDIT:
            definition = registry.definitions_by_header[selected.header]
            if definition.data_type.value == "ENUM":
                permitted = registry.permitted_values_by_header[selected.header]
                final_value = st.selectbox(
                    "Final permitted value",
                    ("", *permitted),
                    index=("", *permitted).index(final_value) if final_value in permitted else 0,
                    key=f"review_value_{job_id}_{selected.sku}_{selected.header}",
                )
            else:
                final_value = st.text_input(
                    "Final value",
                    value=final_value,
                    key=f"review_value_{job_id}_{selected.sku}_{selected.header}",
                )
        note = st.text_input(
            "Reviewer note",
            value=selected.reviewer_note or "",
            key=f"review_note_{job_id}_{selected.sku}_{selected.header}",
        )
        if st.button(
            "Save review decision",
            type="primary",
            key=f"save_review_{job_id}_{selected.sku}_{selected.header}",
        ):
            try:
                persist_review_decision(
                    database,
                    selected,
                    action,
                    registry,
                    final_value=final_value,
                    reviewer_note=note,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.rerun()

    unresolved = unresolved_review_items(review_items)
    if unresolved:
        st.warning(
            f"Catalog copy and final export are blocked by {len(unresolved):,} unresolved "
            "critical review items."
        )
        return

    facts = accepted_facts(review_items)
    decision_digest = sha256(
        "|".join(
            f"{item.sku}:{item.header}:{item.review_action}:{item.final_value}"
            for item in review_items
        ).encode()
    ).hexdigest()
    fake = job.context.model_identifier in {"phase5-fake", "phase7-fake"}
    limits = ResourceLimits.from_env()
    pricing = load_pricing(PRICING_PATH)
    nvidia_settings = NvidiaSettings.from_env()
    live_ready = fake or (
        nvidia_settings.enabled and _nvidia_connection_passed(nvidia_settings)
    )
    live_confirmed = True
    if not fake:
        if not live_ready:
            st.info(
                "Configure NVIDIA_API_KEY and pass Test NVIDIA Connection in this session "
                "before generating catalog copy."
            )
        live_confirmed = st.checkbox(
            "I confirm this NVIDIA catalog-copy request may incur provider charges.",
            key=f"live_catalog_confirm_{job_id}",
        )
    catalog_model = "phase6-fake" if fake else NVIDIA_MODEL
    catalog_route_key = catalog_model if fake else NVIDIA_CACHE_KEY
    catalog_state = f"catalog_{job_id}_{decision_digest}_{sha256(catalog_route_key.encode()).hexdigest()[:16]}"
    catalogs = st.session_state.get(catalog_state)
    catalog_attempts = len(rows) * 2 * (limits.model_retries + 1)
    catalog_estimate = maximum_job_cost(
        pricing.for_model(catalog_model),
        request_count=catalog_attempts,
        image_count=0,
    )
    catalog_calls_blocked = not fake and job.attempted_model_calls >= limits.calls_per_job
    catalog_cost_blocked = not fake and catalog_estimate is not None and (
        limits.maximum_estimated_cost is None
        or catalog_estimate > limits.maximum_estimated_cost
    )
    if not fake:
        st.caption(
            "Maximum planned catalog-copy attempts: "
            f"{catalog_attempts:,} · estimated cost: "
            f"{catalog_estimate if catalog_estimate is not None else 'unavailable'}."
        )
    if catalog_calls_blocked or catalog_cost_blocked:
        st.error("Catalog copy is blocked by the configured call or cost circuit breaker.")
    if catalogs is None and st.button(
        "Generate factual catalog copy",
        type="primary",
        key=f"generate_catalog_{job_id}_{decision_digest}",
        disabled=(
            not live_ready
            or not live_confirmed
            or catalog_calls_blocked
            or catalog_cost_blocked
        ),
    ):
        client = fake_catalog_client() if fake else NvidiaInklingClient(nvidia_settings)
        try:
            provider_store().record_job_snapshot(
                job_id,
                RoutePurpose.CATALOG_COPY,
                provider=None,
                display_name="Offline fake client" if fake else "NVIDIA NIM · Inkling",
                protocol=(
                    "FAKE" if fake else ProviderProtocol.OPENAI_CHAT_COMPLETIONS.value
                ),
                base_url_fingerprint=(
                    "offline"
                    if fake
                    else sha256(NVIDIA_CHAT_COMPLETIONS_URL.encode()).hexdigest()[:16]
                ),
                model_id=catalog_model,
                provider_configuration_version=0,
                adapter_version="fake-v1" if fake else NVIDIA_ADAPTER_VERSION,
                prompt_version=CONTENT_PROMPT_VERSION,
                schema_version=CONTENT_SCHEMA_VERSION,
            )

            def consume_catalog_call() -> None:
                if not database.claim_model_call(job_id, limits.calls_per_job):
                    raise LLMError("The configured job call circuit breaker was reached.")

            catalogs = generate_catalog_batch(
                rows,
                facts,
                registry,
                client,
                model=catalog_model,
                keyword_separator=os.environ.get("FASHION_CMS_KEYWORD_SEPARATOR", ", "),
                groups=database.load_groups(job_id),
                attribute_set=job.attribute_set,
                product_profile=job.product_profile,
                max_retries=limits.model_retries,
                before_attempt=None if fake else consume_catalog_call,
            )
        except Exception as exc:
            st.error(f"Catalog copy could not be accepted: {exc}")
        else:
            st.session_state[catalog_state] = catalogs
        finally:
            if not fake:
                client.close()
        if catalogs is not None:
            st.rerun()
    if catalogs is None:
        st.info("Review is complete. Generate catalog copy to enable final downloads.")
        return

    catalog_contents = {
        (
            catalog.content.request_id,
            catalog.content.model,
            catalog.content.keywords,
            catalog.content.bullets,
        ): catalog.content
        for catalog in catalogs.values()
    }.values()
    catalog_usage: dict[str, int] = {}
    catalog_cost = 0
    cost_available = True
    for content in catalog_contents:
        for name, value in content.usage.items():
            catalog_usage[name] = catalog_usage.get(name, 0) + value
        content_cost = usage_cost(pricing.for_model(content.model), content.usage)
        if content_cost is None:
            cost_available = False
        else:
            catalog_cost += content_cost
    st.caption(
        f"Catalog calls attempted: {sum(content.request_count for content in catalog_contents)} · "
        f"retries: {sum(content.retry_count for content in catalog_contents)} · usage: "
        f"{catalog_usage or 'unavailable'} · cost: "
        f"{catalog_cost if cost_available else 'unavailable'}"
    )

    st.subheader(f"Accepted {attribute_set_name} titles and catalog copy")
    st.dataframe(
        [
            {
                "SKU": sku,
                "Title / name": catalog.title,
                "Keywords": catalog.content.keywords,
                **{
                    f"Bullet {index}": bullet
                    for index, bullet in enumerate(catalog.content.bullets, start=1)
                },
                "Warnings": " ".join(catalog.content.warnings),
            }
            for sku, catalog in catalogs.items()
        ],
        hide_index=True,
        width="stretch",
    )
    try:
        cms = build_cms_workbook(
            rows,
            review_items,
            catalogs,
            registry,
            attribute_set=job.attribute_set,
            product_profile=job.product_profile,
        )
        qc = build_qc_report(
            review_items,
            catalogs,
            rows=rows,
            attribute_set=job.attribute_set,
            product_profile=job.product_profile,
            configuration_warnings=configuration_issues(registry, job.attribute_set),
            incomplete_rows=tuple(
                (sku, item.status.value, item.error or "Incomplete")
                for item in items
                if item.status not in successful_statuses
                for sku in item.represented_skus
            ),
        )
    except Exception as exc:
        st.error(str(exc))
        return

    artifact_root = ROOT / "data" / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    existing = {(artifact.kind, artifact.path) for artifact in database.list_artifacts(job_id)}
    for kind, filename, content in (
        (
            "CMS_WORKBOOK_PARTIAL" if partial else "CMS_WORKBOOK",
            f"{job_id}_{job.attribute_set}_{'partial_' if partial else ''}cms.xlsx",
            cms,
        ),
        (
            "QC_REPORT_PARTIAL" if partial else "QC_REPORT",
            f"{job_id}_{job.attribute_set}_{'partial_' if partial else ''}qc.xlsx",
            qc,
        ),
    ):
        path = artifact_root / filename
        path.write_bytes(content)
        relative = str(path.relative_to(ROOT))
        if (kind, relative) not in existing:
            database.add_artifact(job_id, kind, relative)
    if not partial and job.status == JobStatus.REVIEW_REQUIRED:
        database.transition_job(job_id, JobStatus.COMPLETED)
    downloads = st.columns(2)
    downloads[0].download_button(
        f"Download CMS-ready {attribute_set_name} workbook",
        data=cms,
        file_name=f"{job.attribute_set}_{'partial_' if partial else ''}cms_upload.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    downloads[1].download_button(
        f"Download separate {attribute_set_name} QC report",
        data=qc,
        file_name=f"{job.attribute_set}_{'partial_' if partial else ''}qc_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


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
                "Profiles": ", ".join(profile_ids(registry, set_id)),
                "Configuration health": (
                    "Ready"
                    if not configuration_issues(registry, set_id)
                    else "Incomplete: " + " ".join(configuration_issues(registry, set_id))
                ),
            }
            for set_id, headers in registry.mappings_by_set.items()
        ],
        hide_index=True,
        width="stretch",
    )
    st.subheader("Configuration health")
    try:
        limits = ResourceLimits.from_env()
    except (TypeError, ValueError) as exc:
        st.error(f"Resource-limit configuration is invalid: {exc}")
    else:
        st.dataframe(limits.health_rows(), hide_index=True, width="stretch")
    pricing = load_pricing(PRICING_PATH)
    thresholds = load_thresholds(THRESHOLDS_PATH)
    st.caption(
        f"Pricing configuration {pricing.version}: {pricing.approval_status}. "
        f"{len(pricing.models)} configured model(s). Evaluation thresholds "
        f"{thresholds.version}: {thresholds.approval_status.value}."
    )
    selected_set = st.selectbox(
        "Inspect attribute set",
        tuple(registry.mappings_by_set),
        format_func=lambda set_id: set_names[set_id],
        key="registry_set",
    )
    for issue in configuration_issues(registry, selected_set):
        st.warning(f"Configuration incomplete: {issue}")
    selected_profile = st.selectbox(
        "Inspect product profile",
        profile_ids(registry, selected_set),
        key=f"registry_profile_{selected_set}",
    )
    applicable = set(
        applicable_attribute_headers(registry, selected_set, selected_profile)
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
                "Sent for extraction": header in applicable,
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
                "Product profile": database.get_job(job.id).product_profile or "",
                "Created time": job.created_at,
                "Updated time": job.updated_at,
                "Overall status": job.status.value,
                "Completed items": job.completed_item_count,
                "Failed items": job.failed_item_count,
                "Review required": job.review_required_count,
                "Planned requests": job.planned_request_count,
                "Attempted model calls": job.attempted_model_calls,
                "Cancellation requested": job.cancel_requested,
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
    extraction_job = _is_extraction_job(job)
    legacy_extraction_job = _is_legacy_extraction_job(job)
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
    snapshots = provider_store().job_snapshots(selected_id)
    if snapshots:
        st.subheader("Non-secret provider snapshots")
        st.dataframe(snapshots, hide_index=True, width="stretch")
    if job.cancel_requested:
        st.warning(
            "Cancellation requested: no new work will be scheduled. An already-sent "
            "provider request may finish; completed results are preserved and pending units "
            "remain resumable."
        )
    elif job.status in {JobStatus.READY, JobStatus.RUNNING} and st.button(
        "Request cancellation", key=f"cancel_{selected_id}"
    ):
        service.request_cancellation(selected_id)
        st.rerun()

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
        if extraction_job or legacy_extraction_job:
            st.info(
                "Re-upload the same validated workbook and images in CMS Generator to retry "
                "safely; image bytes are not stored in SQLite."
            )
        elif st.button(
            "Retry failed items", type="primary", key=f"history_retry_{selected_id}"
        ):
            service.retry_failed_items(selected_id)
            st.rerun()
    if not extraction_job and not legacy_extraction_job and job.status in {
        JobStatus.UPLOADED,
        JobStatus.VALIDATING,
        JobStatus.READY,
        JobStatus.RUNNING,
    } and st.button(
        "Resume interrupted job", type="primary", key=f"history_resume_{selected_id}"
    ):
        service.resume_job(selected_id)
        st.rerun()

    if extraction_job:
        _show_attribute_results(database, selected_id)
        _show_attribute_review(database, selected_id)
    elif legacy_extraction_job:
        st.info(
            "This pre-input_data extraction is read-only. Completed results and artifacts "
            "remain available; unfinished work requires a new upload."
        )
        _show_attribute_results(database, selected_id)

    st.subheader("Artifacts")
    artifacts = database.list_artifacts(selected_id)
    if not artifacts:
        st.info("No output artifact exists yet; complete review and catalog generation.")
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


def release_readiness_page() -> None:
    st.title("Release Readiness")
    st.write("Centralized release gates for the current verified release candidate.")
    try:
        report = load_report(RELEASE_REPORT_PATH)
    except Exception:
        st.error("The release-gate artifact is missing or invalid.")
        return
    if report.production_ready:
        st.success("All mandatory release gates pass; user acceptance may proceed.")
    else:
        st.warning(f"Production release is blocked. Current verdict: {report.verdict}.")
    st.caption(
        f"{report.application} {report.version} · generated {report.generated_at.isoformat()}"
    )
    st.dataframe(
        [
            {
                "Gate": result.gate_id,
                "Description": result.description,
                "Status": result.status.value,
                "Evidence": result.evidence,
                "Artifact": result.artifact_path or "",
                "Blocker / failure": result.reason or "",
            }
            for result in report.results
        ],
        hide_index=True,
        width="stretch",
    )


st.set_page_config(page_title="Fashion CMS Upload Generator", layout="wide")
page = st.navigation(
    [
        st.Page(cms_workbook_page, title="CMS Generator", default=True),
        st.Page(image_downloader_page, title="Image Downloader"),
        st.Page(attribute_registry_page, title="Attribute Registry"),
        st.Page(job_history_page, title="Job History"),
        st.Page(release_readiness_page, title="Release Readiness"),
    ]
)
page.run()
