from hashlib import sha256
import os
from pathlib import Path

import streamlit as st
from pydantic import SecretStr

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
from fashion_cms.llm_service import LLMError, LLMSettings, OpenAIResponsesClient
from fashion_cms.provider_service import (
    AuthenticationMode,
    EndpointPolicy,
    FailureCategory,
    ModelRoute,
    ProviderDraft,
    ProviderProtocol,
    ProviderRequestError,
    ProviderStore,
    RoutePurpose,
    SecretStorageMode,
    create_adapter,
    discover_models,
    encrypted_mode_available,
    endpoint_url,
    provider_public_row,
    provider_secret_available,
    resolve_provider_secret,
    test_structured_output,
    test_text_connection,
    test_vision,
)
from fashion_cms.models import (
    AnalysisMode,
    JobStatus,
    Severity,
    UploadedImage,
    ValidationIssue,
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
    fake_attribute_client,
    run_attribute_job,
)


ROOT = Path(__file__).resolve().parent
IMAGE_BATCH_STATE = "image_download_batch"
IMAGE_WORKBOOK_DIGEST_STATE = "image_download_workbook_digest"
CMS_JOB_STATE = "cms_job_id"
CMS_SOURCE_DIGEST_STATE = "cms_source_digest"
DEFAULT_DATABASE_PATH = ROOT / "data" / "fashion_cms.sqlite3"
PRICING_PATH = ROOT / "config" / "model_pricing.json"
THRESHOLDS_PATH = ROOT / "config" / "evaluation_thresholds.json"
RELEASE_REPORT_PATH = ROOT / "docs" / "releases" / "0.1.0-rc1" / "release-gates.json"
PROVIDER_SECRETS_STATE = "llm_provider_session_secrets"
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


@st.cache_resource
def get_database(path: str) -> JobDatabase:
    return JobDatabase(path)


def job_database() -> JobDatabase:
    path = os.environ.get("FASHION_CMS_DB_PATH", str(DEFAULT_DATABASE_PATH))
    return get_database(path)


def provider_store() -> ProviderStore:
    return ProviderStore(job_database())


def provider_session_secrets() -> dict[str, object]:
    return st.session_state.setdefault(PROVIDER_SECRETS_STATE, {})


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


def cms_workbook_page() -> None:
    st.title("Fashion CMS Upload Generator")
    st.write("Create a validated blank CMS workbook from local product data and SKU images.")

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

    try:
        llm_settings = LLMSettings.from_env()
        settings_error = None
    except ValueError as exc:
        llm_settings = LLMSettings()
        settings_error = str(exc)
    st.info(
        f"{set_names[attribute_set]} · extract, review canonical facts, generate factual copy, "
        "and export separate CMS and QC workbooks."
    )
    active_vision_route = provider_store().active_route(RoutePurpose.VISION_EXTRACTION)
    execution_options = ["Fake (offline)", "OpenAI Responses API environment (legacy)"]
    if active_vision_route is not None:
        execution_options.append("Active configured provider route (live)")
    execution_mode = st.radio(
        "Extraction client",
        tuple(execution_options),
        horizontal=True,
    )
    if settings_error:
        st.error(settings_error)
    elif execution_mode == "OpenAI Responses API environment (legacy)" and not llm_settings.enabled:
        st.info(llm_settings.disabled_reason)
    elif execution_mode == "Active configured provider route (live)":
        assert active_vision_route is not None
        active_provider = provider_store().get_provider(active_vision_route.provider_id)
        if not provider_secret_available(
            active_provider, session_secrets=provider_session_secrets()
        ):
            st.error("The active vision provider API key is unavailable in this session.")
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
        limits=ResourceLimits.from_env(),
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
        configuration = "|".join(
            (
                execution_mode,
                product_profile or "",
                (
                    _fake_model(attribute_set)
                    if execution_mode == "Fake (offline)"
                    else (
                        active_vision_route.model_id
                        if execution_mode == "Active configured provider route (live)"
                        and active_vision_route is not None
                        else llm_settings.model or "unconfigured"
                    )
                ),
                (
                    active_vision_route.image_detail or "high"
                    if execution_mode == "Active configured provider route (live)"
                    and active_vision_route is not None
                    else llm_settings.image_detail
                ),
                *_contract_versions(attribute_set),
            )
        )
        digest = _source_digest(
            workbook_content, image_upload_data, attribute_set, configuration
        )
        if st.session_state.get(CMS_SOURCE_DIGEST_STATE) != digest:
            st.session_state[CMS_SOURCE_DIGEST_STATE] = digest
            st.session_state.pop(CMS_JOB_STATE, None)

        job_id = st.session_state.get(CMS_JOB_STATE)
        if not job_id:
            st.subheader("Variant analysis job")
            st.caption(
                "Creating a job stores normalized rows, image metadata, and hashes. "
                "Creation never makes an API request."
            )
            configured_provider_unavailable = False
            active_provider = None
            if execution_mode == "Active configured provider route (live)":
                assert active_vision_route is not None
                active_provider = provider_store().get_provider(active_vision_route.provider_id)
                configured_provider_unavailable = not provider_secret_available(
                    active_provider, session_secrets=provider_session_secrets()
                )
            live_unavailable = (
                execution_mode == "OpenAI Responses API environment (legacy)"
                and not llm_settings.enabled
            ) or configured_provider_unavailable
            if st.button(
                "Create persistent job",
                type="primary",
                disabled=live_unavailable or not product_profile or not profile_confirmed,
            ):
                try:
                    prompt_version, schema_version = _contract_versions(attribute_set)
                    selected_model = (
                        _fake_model(attribute_set)
                        if execution_mode == "Fake (offline)"
                        else (
                            active_vision_route.model_id
                            if active_vision_route is not None
                            and execution_mode == "Active configured provider route (live)"
                            else llm_settings.model
                        )
                    )
                    selected_detail = (
                        active_vision_route.image_detail or "high"
                        if active_vision_route is not None
                        and execution_mode == "Active configured provider route (live)"
                        else llm_settings.image_detail
                    )
                    provider_cache_key = (
                        f"{active_provider.cache_key}:{active_vision_route.configuration_version}"
                        if active_provider is not None
                        and active_vision_route is not None
                        and execution_mode == "Active configured provider route (live)"
                        else ""
                    )
                    job_id = JobService(job_database()).create_job(
                        workbook_result.rows,
                        image_result.images,
                        attribute_set=attribute_set,
                        product_profile=product_profile,
                        registry_version=registry.fingerprint,
                        prompt_version=prompt_version,
                        schema_version=schema_version,
                        model_identifier=selected_model,
                        image_detail=selected_detail,
                        provider_cache_key=provider_cache_key,
                    )
                    snapshot_store = provider_store()
                    snapshot_store.record_job_snapshot(
                        job_id,
                        RoutePurpose.VISION_EXTRACTION,
                        provider=active_provider,
                        display_name=(
                            active_provider.display_name
                            if active_provider is not None
                            else (
                                "Offline fake client"
                                if execution_mode == "Fake (offline)"
                                else "OpenAI environment configuration"
                            )
                        ),
                        protocol=(
                            active_provider.protocol.value
                            if active_provider is not None
                            else (
                                "FAKE"
                                if execution_mode == "Fake (offline)"
                                else ProviderProtocol.OPENAI_RESPONSES.value
                            )
                        ),
                        base_url_fingerprint=(
                            active_provider.base_url_fingerprint
                            if active_provider is not None
                            else "offline" if execution_mode == "Fake (offline)" else "legacy-openai"
                        ),
                        model_id=selected_model or "unconfigured",
                        provider_configuration_version=(
                            active_provider.configuration_version if active_provider else 0
                        ),
                        adapter_version=(
                            active_provider.adapter_version if active_provider else "legacy-v1"
                        ),
                        prompt_version=prompt_version,
                        schema_version=schema_version,
                    )
                except Exception:
                    st.error("The persistent job could not be created safely.")
                else:
                    st.session_state[CMS_JOB_STATE] = job_id
                    st.rerun()
        else:
            try:
                show_job_plan(job_id, image_result.images)
            except Exception:
                st.session_state.pop(CMS_JOB_STATE, None)
                st.error("The selected job could not be opened. Create or open it again.")
    else:
        st.error("Resolve critical findings before processing or download.")


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
        _attribute_controls(database, job_id, uploaded_images)
        _show_attribute_results(database, job_id)
        _show_attribute_review(database, job_id)
    else:
        st.info("Extraction is blocked because this stored job uses an obsolete contract.")


def _attribute_controls(
    database: JobDatabase,
    job_id: str,
    uploaded_images: tuple[UploadedImage, ...],
) -> None:
    job = database.get_job(job_id)
    if job.status not in {
        JobStatus.READY,
        JobStatus.RUNNING,
        JobStatus.PARTIAL_FAILURE,
        JobStatus.FAILED,
    }:
        return
    attribute_set_name = set_names[job.attribute_set]
    fake = job.context.model_identifier in {"phase5-fake", "phase7-fake"}
    configured_provider_job = bool(job.context.provider_cache_key)
    stored_items = database.list_work_items(job_id)
    retry_failed = any(item.status == WorkItemStatus.FAILED for item in stored_items)
    action = f"Retry failed {attribute_set_name} items" if retry_failed else (
        f"Resume {attribute_set_name} extraction"
        if job.status == JobStatus.RUNNING
        else f"Run {attribute_set_name} extraction"
    )
    settings_error = None
    try:
        settings = LLMSettings.from_env()
    except ValueError as exc:
        settings = LLMSettings()
        settings_error = str(exc)
    active_route = None
    active_provider = None
    provider_secret = None
    if configured_provider_job:
        active_route = provider_store().active_route(RoutePurpose.VISION_EXTRACTION)
        if active_route is not None:
            active_provider = provider_store().get_provider(active_route.provider_id)
            provider_secret = resolve_provider_secret(
                active_provider, session_secrets=provider_session_secrets()
            )
    configured = (job.context.registry_version == registry.fingerprint) and (
        fake
        or (
            configured_provider_job
            and active_route is not None
            and active_provider is not None
            and provider_secret is not None
            and f"{active_provider.cache_key}:{active_route.configuration_version}"
            == job.context.provider_cache_key
            and active_route.model_id == job.context.model_identifier
            and active_route.image_detail == job.context.image_detail
        )
        or (
            not configured_provider_job
            and settings.enabled
            and settings.model == job.context.model_identifier
            and settings.image_detail == job.context.image_detail
        )
    )
    confirmed = True
    limits = ResourceLimits.from_env()
    pricing = load_pricing(PRICING_PATH)
    model_pricing = pricing.for_model(job.context.model_identifier)
    remaining_units = sum(
        item.status in {WorkItemStatus.PENDING, WorkItemStatus.FAILED}
        for item in stored_items
    )
    remaining_attempts = remaining_units * (limits.model_retries + 1)
    estimated_cost = maximum_job_cost(
        model_pricing,
        request_count=remaining_attempts,
        image_count=len(uploaded_images) * (limits.model_retries + 1),
    )
    calls_blocked = not fake and job.attempted_model_calls >= limits.calls_per_job
    cost_blocked = not fake and estimated_cost is not None and (
        limits.maximum_estimated_cost is None
        or estimated_cost > limits.maximum_estimated_cost
    )
    if calls_blocked:
        st.error(
            "Live processing is blocked because this job has reached its hard model-call limit."
        )
    if cost_blocked:
        st.error(
            "Live processing is blocked by the estimated-cost circuit breaker. "
            "Configure an approved maximum at or above the displayed estimate."
        )
    if fake:
        st.caption("Offline fake client selected. No network request or API key is used.")
    elif configured_provider_job:
        if not configured:
            st.info(
                "Live extraction is disabled because the active vision route changed, "
                "became unavailable, or has no API key in this session."
            )
        elif active_provider is not None and active_route is not None:
            st.caption(
                f"Provider: {active_provider.display_name} · protocol: "
                f"{active_provider.protocol.value} · model: {active_route.model_id}."
            )
        confirmed = st.checkbox(
            "I confirm this configured-provider request and the displayed planned request count.",
            key=f"live_confirm_{job_id}",
        )
        if estimated_cost is None:
            st.caption("Cost unavailable: no approved matching pricing configuration.")
    else:
        if settings_error:
            st.error(settings_error)
        elif not configured:
            st.info(
                "Live extraction is disabled. Configure the same OPENAI_MODEL and "
                "OPENAI_IMAGE_DETAIL used when this job was created."
            )
        confirmed = st.checkbox(
            "I confirm this live OpenAI request and the displayed planned request count.",
            key=f"live_confirm_{job_id}",
        )
        if estimated_cost is None:
            st.caption("Cost unavailable: no approved matching pricing configuration.")
        elif model_pricing is not None:
            st.caption(f"Estimated maximum: {model_pricing.currency} {estimated_cost:.4f}.")
    if not st.button(
        action,
        type="primary",
        key=f"attribute_run_{job_id}_{job.status.value}",
        disabled=not configured or not confirmed or cost_blocked or calls_blocked,
    ):
        return

    progress_bar = st.progress(0.0)
    progress_text = st.empty()

    def update(done: int, total: int, item) -> None:
        progress_bar.progress(done / total if total else 1.0)
        progress_text.caption(
            f"Processed {done} of {total}: {', '.join(item.represented_skus)}"
        )

    if fake:
        client = fake_attribute_client()
    elif configured_provider_job:
        assert active_provider is not None and active_route is not None
        client = create_adapter(active_provider, active_route, provider_secret)
    else:
        client = OpenAIResponsesClient(settings)
    prompt_version, schema_version = _contract_versions(job.attribute_set)
    try:
        if job.cancel_requested and not retry_failed:
            database.clear_cancellation(job_id)
        with st.spinner(f"Extracting {attribute_set_name} observations…"):
            run_attribute_job(
                database,
                job_id,
                client,
                uploaded_images,
                registry,
                retry_failed=retry_failed,
                progress=update,
                expected_prompt_version=prompt_version,
                expected_schema_version=schema_version,
            )
    except Exception:
        st.error(
            f"{attribute_set_name} extraction could not complete safely. "
            "Inspect failed work items."
        )
    finally:
        if not fake:
            client.close()
    st.rerun()


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
    warnings = sum(len(record.vision_result.warnings) for _, record in records)
    metrics = st.columns(4)
    metrics[0].metric("Successful", success)
    metrics[1].metric("Cached", sum(item.cache_hit for item in items))
    metrics[2].metric("Failed", failures)
    metrics[3].metric("Warnings", warnings)
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
    live_ready = True
    live_confirmed = True
    live_settings = None
    catalog_route = None
    catalog_provider = None
    catalog_secret = None
    configured_provider_job = bool(job.context.provider_cache_key)
    if not fake:
        catalog_route = provider_store().active_route(RoutePurpose.CATALOG_COPY)
        if catalog_route is not None:
            catalog_provider = provider_store().get_provider(catalog_route.provider_id)
            try:
                catalog_secret = resolve_provider_secret(
                    catalog_provider, session_secrets=provider_session_secrets()
                )
            except ValueError:
                catalog_secret = None
            live_ready = (
                catalog_provider is not None
                and (
                    catalog_provider.authentication_mode == AuthenticationMode.NO_AUTH
                    or catalog_secret is not None
                )
            )
            if not live_ready:
                st.info(
                    "Activate a tested CATALOG_COPY route with an available API key to "
                    "generate live catalog copy."
                )
        elif configured_provider_job:
            live_ready = False
            st.info(
                "Activate a tested CATALOG_COPY route with an available API key to "
                "generate live catalog copy."
            )
        else:
            try:
                live_settings = LLMSettings.from_env()
                live_ready = live_settings.enabled
            except ValueError as exc:
                live_ready = False
                st.error(str(exc))
            if not live_ready:
                st.info(
                    "Configure OPENAI_API_KEY and OPENAI_MODEL for optional live copy generation."
                )
        live_confirmed = st.checkbox(
            "I confirm this text-only catalog-copy request, which may incur provider charges.",
            key=f"live_catalog_confirm_{job_id}",
        )
    catalog_model = (
        "phase6-fake"
        if fake
        else (
            catalog_route.model_id
            if catalog_route is not None
            else (live_settings.model if live_settings and live_settings.model else "")
        )
    )
    catalog_route_key = (
        f"{catalog_provider.cache_key}:{catalog_route.configuration_version}"
        if catalog_provider is not None and catalog_route is not None
        else catalog_model
    )
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
        client = fake_catalog_client()
        try:
            if not fake:
                if catalog_route is not None:
                    assert catalog_provider is not None and catalog_route is not None
                    client = create_adapter(catalog_provider, catalog_route, catalog_secret)
                else:
                    assert live_settings is not None
                    client = OpenAIResponsesClient(live_settings)

            provider_store().record_job_snapshot(
                job_id,
                RoutePurpose.CATALOG_COPY,
                provider=catalog_provider,
                display_name=(
                    catalog_provider.display_name
                    if catalog_provider is not None
                    else "Offline fake client" if fake else "OpenAI environment configuration"
                ),
                protocol=(
                    catalog_provider.protocol.value
                    if catalog_provider is not None
                    else "FAKE" if fake else ProviderProtocol.OPENAI_RESPONSES.value
                ),
                base_url_fingerprint=(
                    catalog_provider.base_url_fingerprint
                    if catalog_provider is not None
                    else "offline" if fake else "legacy-openai"
                ),
                model_id=catalog_model,
                provider_configuration_version=(
                    catalog_provider.configuration_version if catalog_provider else 0
                ),
                adapter_version=(
                    catalog_provider.adapter_version if catalog_provider else "legacy-v1"
                ),
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


def llm_providers_page() -> None:
    st.title("LLM Providers")
    st.write(
        "Custom provider configuration currently supports OpenAI-compatible endpoints. "
        "Providers using a different API protocol require a dedicated adapter."
    )
    st.warning(
        "This application has no user authentication. Provider management is safe only in a "
        "private development environment; keep the Codespaces port private."
    )
    st.caption(
        "Connection and capability tests send small API requests and may incur provider charges. "
        "No customer, catalog, file, web-search, or tool data is used."
    )
    policy = EndpointPolicy.from_env()
    if policy.allow_private or policy.allow_insecure_http:
        st.error(
            "Development-only local endpoint access is enabled by server configuration. "
            "Only explicitly allowlisted hosts are accepted; production ignores these flags."
        )
    st.info(
        "In Codespaces, localhost means the Codespace container—not the user’s Windows computer. "
        "A model running on the user’s laptop is not automatically reachable from Codespaces."
    )

    store = provider_store()
    secrets = provider_session_secrets()
    providers = store.list_providers(include_retired=True)
    routes = store.list_routes()
    st.subheader("Provider list")
    if providers:
        st.dataframe(
            [
                provider_public_row(
                    provider,
                    tuple(route for route in routes if route.provider_id == provider.id),
                    secret_available=provider_secret_available(
                        provider, session_secrets=secrets
                    ),
                )
                for provider in providers
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("No provider configurations have been saved.")

    selected_id = st.selectbox(
        "Add or edit provider",
        ("", *(provider.id for provider in providers)),
        format_func=lambda identifier: (
            "Add new provider"
            if not identifier
            else next(
                f"Edit · {provider.display_name}" for provider in providers if provider.id == identifier
            )
        ),
    )
    selected = store.get_provider(selected_id) if selected_id else None
    discovered = tuple(st.session_state.get(f"provider_models_{selected_id}", ()))

    if selected is not None:
        columns = st.columns(3)
        if columns[0].button(
            "Disable" if selected.enabled else "Enable",
            key=f"toggle_provider_{selected.id}",
        ):
            store.set_enabled(selected.id, not selected.enabled)
            st.rerun()
        clear_confirmed = columns[1].checkbox(
            "Confirm clear API key",
            key=f"clear_provider_confirm_{selected.id}",
        )
        if columns[1].button(
            "Clear API key",
            disabled=not clear_confirmed,
            key=f"clear_provider_{selected.id}",
        ):
            secrets.pop(selected.id, None)
            store.clear_secret(selected.id)
            st.rerun()
        retire_confirmed = columns[2].checkbox(
            "Confirm delete/retire",
            key=f"retire_provider_confirm_{selected.id}",
        )
        if columns[2].button(
            "Delete / retire",
            disabled=not retire_confirmed,
            key=f"retire_provider_{selected.id}",
        ):
            store.delete_or_retire(selected.id)
            secrets.pop(selected.id, None)
            st.rerun()

    encrypted_available = encrypted_mode_available()
    storage_options = [SecretStorageMode.SESSION_ONLY, SecretStorageMode.ENV_REFERENCE]
    if encrypted_available or (
        selected is not None
        and selected.secret_storage_mode == SecretStorageMode.ENCRYPTED_DATABASE
    ):
        storage_options.append(SecretStorageMode.ENCRYPTED_DATABASE)
    provider_routes = store.list_routes(selected.id) if selected is not None else ()
    vision_route = next(
        (route for route in provider_routes if route.purpose == RoutePurpose.VISION_EXTRACTION),
        None,
    )
    catalog_route = next(
        (route for route in provider_routes if route.purpose == RoutePurpose.CATALOG_COPY),
        None,
    )

    with st.form(f"llm_provider_form_{selected_id or 'new'}"):
        name = st.text_input("Provider Name", value=selected.display_name if selected else "")
        protocol = st.selectbox(
            "Protocol",
            tuple(ProviderProtocol),
            index=(
                tuple(ProviderProtocol).index(selected.protocol) if selected is not None else 0
            ),
            format_func=lambda value: value.value,
        )
        base_url = st.text_input(
            "Base URL",
            value=selected.base_url if selected else "https://api.openai.com/v1",
            help="Enter the API base only. The adapter adds known endpoint paths; /v1 is never added automatically.",
        )
        authentication = st.selectbox(
            "Authentication Mode",
            tuple(AuthenticationMode),
            index=(
                tuple(AuthenticationMode).index(selected.authentication_mode)
                if selected is not None
                else 0
            ),
            format_func=lambda value: value.value,
        )
        api_header = st.text_input(
            "API key header name",
            value=selected.api_key_header_name if selected and selected.api_key_header_name else "x-api-key",
            disabled=authentication != AuthenticationMode.API_KEY_HEADER,
        )
        storage = st.selectbox(
            "Secret Storage Mode",
            tuple(storage_options),
            index=(
                tuple(storage_options).index(selected.secret_storage_mode)
                if selected is not None and selected.secret_storage_mode in storage_options
                else 0
            ),
            format_func=lambda value: value.value,
            disabled=authentication == AuthenticationMode.NO_AUTH,
        )
        if storage == SecretStorageMode.SESSION_ONLY:
            st.caption(
                "SESSION_ONLY is the default. The key stays in this server session only and is "
                "lost on session/server restart; unattended resume then requires re-entry."
            )
        elif storage == SecretStorageMode.ENCRYPTED_DATABASE and not encrypted_available:
            st.error(
                "ENCRYPTED_DATABASE is unavailable without a valid server master key and, in "
                "production, application authentication."
            )
        environment_name = st.text_input(
            "Environment Secret Name",
            value=(
                selected.secret_reference
                if selected
                and selected.secret_storage_mode == SecretStorageMode.ENV_REFERENCE
                and selected.secret_reference
                else ""
            ),
            disabled=storage != SecretStorageMode.ENV_REFERENCE,
        )
        api_key = st.text_input(
            "API Key",
            type="password",
            value="",
            help="Blank while editing keeps the existing encrypted or session key. The key is never sent back to this field.",
            disabled=(
                authentication == AuthenticationMode.NO_AUTH
                or storage == SecretStorageMode.ENV_REFERENCE
            ),
        )
        request_timeout = st.number_input(
            "Request Timeout",
            min_value=1.0,
            max_value=300.0,
            value=float(selected.request_timeout if selected else 30.0),
        )
        if selected is not None:
            st.caption(
                f"Effective endpoints · models: {endpoint_url(selected.base_url, 'models')} · "
                f"generation: {endpoint_url(selected.base_url, 'responses' if selected.protocol == ProviderProtocol.OPENAI_RESPONSES else 'chat/completions')}"
            )
        model_choice = st.selectbox(
            "Discovered model (optional)",
            discovered,
            index=None,
            placeholder=(
                "Select a discovered model" if discovered else "Fetch Models or enter IDs manually"
            ),
        )
        vision_model = st.text_input(
            "Vision Model",
            value=vision_route.model_id if vision_route else "",
            help="Manual model-ID fallback remains available when listing is unsupported.",
        )
        catalog_model = st.text_input(
            "Catalog/Text Model",
            value=catalog_route.model_id if catalog_route else "",
            help="The same model may be used for both purposes.",
        )
        if model_choice:
            use_for = st.radio(
                "Use discovered model for",
                tuple(RoutePurpose),
                horizontal=True,
                format_func=lambda value: value.value,
            )
            if use_for == RoutePurpose.VISION_EXTRACTION:
                vision_model = model_choice
            else:
                catalog_model = model_choice
        maximum_output_tokens = st.number_input(
            "Maximum Output Tokens",
            min_value=1,
            max_value=100_000,
            value=int(
                max(
                    vision_route.maximum_output_tokens if vision_route else 1_024,
                    catalog_route.maximum_output_tokens if catalog_route else 1_024,
                )
            ),
        )
        image_detail = st.selectbox(
            "Vision image detail",
            ("auto", "low", "high"),
            index=("auto", "low", "high").index(
                vision_route.image_detail if vision_route and vision_route.image_detail else "high"
            ),
        )
        test_target = st.selectbox(
            "Text / structured test target",
            tuple(RoutePurpose),
            format_func=lambda value: value.value,
        )
        confirm_replace = st.checkbox(
            "Confirm replacement of any currently active route for the selected purpose(s)"
        )
        activation_purposes = st.multiselect(
            "Purposes to activate",
            tuple(RoutePurpose),
            format_func=lambda value: value.value,
        )
        st.warning("Tests send small requests and may incur provider charges.")
        actions = st.columns(3)
        save = actions[0].form_submit_button("Save")
        fetch = actions[1].form_submit_button("Fetch Models / Refresh")
        text_test = actions[2].form_submit_button("Test Connection")
        structured_test = actions[0].form_submit_button("Test Structured Output")
        vision_test = actions[1].form_submit_button("Test Vision")
        activate = actions[2].form_submit_button("Save and Activate", type="primary")

    action_requested = any((save, fetch, text_test, structured_test, vision_test, activate))
    if action_requested:
        try:
            saved = store.save_provider(
                ProviderDraft(
                    display_name=name,
                    protocol=protocol,
                    base_url=base_url,
                    authentication_mode=authentication,
                    api_key_header_name=api_header,
                    secret_storage_mode=storage,
                    secret_reference=environment_name or None,
                    request_timeout=request_timeout,
                ),
                provider_id=selected.id if selected else None,
                api_key=api_key or None,
                policy=policy,
            )
            if storage == SecretStorageMode.SESSION_ONLY and api_key:
                secrets[saved.id] = SecretStr(api_key)
            elif storage != SecretStorageMode.SESSION_ONLY or (
                authentication == AuthenticationMode.NO_AUTH
            ):
                secrets.pop(saved.id, None)
            saved_routes = {}
            if vision_model.strip():
                saved_routes[RoutePurpose.VISION_EXTRACTION] = store.save_route(
                    saved.id,
                    RoutePurpose.VISION_EXTRACTION,
                    vision_model,
                    timeout=request_timeout,
                    maximum_output_tokens=maximum_output_tokens,
                    image_detail=image_detail,
                )
            if catalog_model.strip():
                saved_routes[RoutePurpose.CATALOG_COPY] = store.save_route(
                    saved.id,
                    RoutePurpose.CATALOG_COPY,
                    catalog_model,
                    timeout=request_timeout,
                    maximum_output_tokens=maximum_output_tokens,
                )
            saved = store.get_provider(saved.id)
            secret = resolve_provider_secret(saved, session_secrets=secrets)
            if saved.authentication_mode != AuthenticationMode.NO_AUTH and secret is None and (
                fetch or text_test or structured_test or vision_test or activate
            ):
                raise ValueError("An API key is required for this action.")
            target_route = saved_routes.get(test_target)
            if fetch:
                discovery_route = target_route or ModelRoute(
                    id="model-discovery",
                    purpose=RoutePurpose.CATALOG_COPY,
                    provider_id=saved.id,
                    model_id="model-discovery",
                    active=False,
                    enabled=True,
                    timeout=request_timeout,
                    maximum_output_tokens=64,
                    image_detail=None,
                    configuration_version=1,
                    created_at=saved.created_at,
                    updated_at=saved.updated_at,
                )
                adapter = create_adapter(saved, discovery_route, secret, policy=policy)
                try:
                    st.session_state[f"provider_models_{saved.id}"] = discover_models(
                        store, adapter, refresh=True
                    )
                finally:
                    adapter.close()
            if text_test or structured_test:
                if target_route is None:
                    raise ValueError("Save a model ID for the selected test target first.")
                adapter = create_adapter(saved, target_route, secret, policy=policy)
                try:
                    result = (
                        test_text_connection(adapter)
                        if text_test
                        else test_structured_output(adapter)
                    )
                finally:
                    adapter.close()
                pricing_model = load_pricing(PRICING_PATH).for_model(target_route.model_id)
                cost = usage_cost(pricing_model, result.usage)
                if cost is not None and pricing_model is not None:
                    result = result.model_copy(
                        update={"cost": f"{pricing_model.currency} {cost}"}
                    )
                store.record_test(saved.id, target_route.model_id, result)
                st.session_state[f"provider_test_{saved.id}"] = result.public_summary()
            if vision_test:
                target_route = saved_routes.get(RoutePurpose.VISION_EXTRACTION)
                if target_route is None:
                    raise ValueError("Save a vision model ID before testing vision.")
                adapter = create_adapter(saved, target_route, secret, policy=policy)
                try:
                    result = test_vision(adapter)
                finally:
                    adapter.close()
                pricing_model = load_pricing(PRICING_PATH).for_model(target_route.model_id)
                cost = usage_cost(pricing_model, result.usage, image_count=1)
                if cost is not None and pricing_model is not None:
                    result = result.model_copy(
                        update={"cost": f"{pricing_model.currency} {cost}"}
                    )
                store.record_test(saved.id, target_route.model_id, result)
                st.session_state[f"provider_test_{saved.id}"] = result.public_summary()
            if activate:
                if not activation_purposes:
                    raise ValueError("Select at least one purpose to activate.")
                for purpose in activation_purposes:
                    route = saved_routes.get(purpose)
                    if route is None:
                        raise ValueError(f"Save a model ID for {purpose.value} before activation.")
                    store.activate_route(
                        route.id,
                        secret_available=provider_secret_available(
                            saved, session_secrets=secrets
                        ),
                        confirm_replace=confirm_replace,
                    )
        except ProviderRequestError as exc:
            if fetch and exc.category == FailureCategory.UNSUPPORTED_ENDPOINT:
                st.warning("Model listing unsupported. Enter the model ID manually and test it before activation.")
            else:
                st.error(str(exc))
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success("Provider configuration saved safely.")
            st.rerun()

    if selected is not None:
        if result := st.session_state.get(f"provider_test_{selected.id}"):
            st.subheader("Latest sanitized capability test")
            st.json(result)
        st.subheader("Saved routes")
        pricing = load_pricing(PRICING_PATH)
        st.dataframe(
            [
                {
                    "Purpose": route.purpose.value,
                    "Provider": selected.display_name,
                    "Effective base URL": selected.base_url,
                    "Protocol": selected.protocol.value,
                    "Model ID": route.model_id,
                    "Verified capabilities": store.capability(
                        selected.id, route.model_id
                    ).model_dump(mode="json"),
                    "Last test time": store.capability(
                        selected.id, route.model_id
                    ).last_tested_at
                    or "",
                    "Secret mode": selected.secret_storage_mode.value,
                    "Pricing status": (
                        "Configured and approved"
                        if pricing.for_model(route.model_id) is not None
                        else "Cost unavailable"
                    ),
                    "Active": route.active,
                }
                for route in provider_routes
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
        if extraction_job:
            st.info(
                "Re-upload the same validated workbook and images in CMS Generator to retry "
                "safely; image bytes are not stored in SQLite."
            )
        elif st.button(
            "Retry failed items", type="primary", key=f"history_retry_{selected_id}"
        ):
            service.retry_failed_items(selected_id)
            st.rerun()
    if not extraction_job and job.status in {
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
        st.Page(llm_providers_page, title="LLM Providers"),
        st.Page(job_history_page, title="Job History"),
        st.Page(release_readiness_page, title="Release Readiness"),
    ]
)
page.run()
