from __future__ import annotations

import json
from pathlib import Path

import pytest

from fashion_cms.database import JobDatabase
from fashion_cms.jobs import JobService
from fashion_cms.llm_service import FakeLLMClient, LLMResponse
from fashion_cms.models import InputRow, UploadedImage
from fashion_cms.registry import DataType, load_registry
from fashion_cms.review import (
    ReviewAction,
    SourcePriority,
    bulk_accept_safe,
    derive_topwear_occasion,
    load_review_items,
    persist_review_decision,
    unresolved_review_items,
    validate_final_value,
)
from fashion_cms.topwear_extraction import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    TOPWEAR_PROFILE_ID,
    fake_topwear_client,
    run_topwear_job,
)


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def registry():
    return load_registry(ROOT / "config" / "attribute_registry.xlsx")


def input_row(data: str, *, sku: str = "0001", row_number: int = 2) -> InputRow:
    return InputRow(
        row_number=row_number,
        sku=sku,
        base_code="BASE",
        attributes__lulu_ean=f"00{row_number}",
        attributes__shipping_weight="1.0",
        model_code_input_data=data,
    )


def create_extracted_job(
    database: JobDatabase,
    registry,
    rows: tuple[InputRow, ...],
    *,
    images: tuple[UploadedImage, ...] = (),
    client=None,
) -> str:
    job_id = JobService(database).create_job(
        rows,
        images,
        attribute_set="topwear",
        registry_version=registry.fingerprint,
        product_profile=TOPWEAR_PROFILE_ID,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        model_identifier="phase5-fake",
        image_detail="high",
    )
    run_topwear_job(
        database,
        job_id,
        client or fake_topwear_client(),
        images,
        registry,
    )
    return job_id


def visual_client(color: str = "Red", design: str = "Striped") -> FakeLLMClient:
    def responder(request) -> LLMResponse:
        contract = request.contract
        refs = [contract.image_ids[0]]
        observations = [
            {
                "header": "attributes__color",
                "raw_value": color,
                "canonical_value": None,
                "status": "observed",
                "evidence_type": "image",
                "evidence_refs": refs,
                "confidence": "high",
                "normalization_rule": None,
                "note": None,
            },
            {
                "header": "attributes__design",
                "raw_value": design,
                "canonical_value": None,
                "status": "observed",
                "evidence_type": "image",
                "evidence_refs": refs,
                "confidence": "high",
                "normalization_rule": None,
                "note": None,
            },
        ]
        output = {
            "attribute_set_id": "topwear",
            "product_profile": contract.product_profile,
            "analysis_mode": contract.analysis_mode.value,
            "group_key": contract.group_key,
            "representative_sku": contract.representative_sku,
            "image_ids": list(contract.image_ids),
            "shared_attributes": [],
            "sku_attributes": [
                {"sku": sku, "observations": observations if index == 0 else []}
                for index, sku in enumerate(contract.represented_skus)
            ],
            "warnings": [],
            "conflicts": [],
        }
        return LLMResponse(
            request_id="visual-fake",
            model="phase5-fake",
            status="completed",
            output_text=json.dumps(output),
        )

    return FakeLLMClient(responder=responder)


def test_source_priority_and_color_conflict_are_preserved(registry) -> None:
    database = JobDatabase(":memory:")
    row = input_row("color: Blue; design: Logo")
    image = UploadedImage(
        source_name="0001-1.jpg",
        filename="0001-1.jpg",
        sku="0001",
        ordinal=1,
        image_format="jpeg",
        width=10,
        height=10,
        content=b"image",
    )
    job_id = create_extracted_job(
        database,
        registry,
        (row,),
        images=(image,),
        client=visual_client(),
    )
    items = {(item.sku, item.header): item for item in load_review_items(database, job_id, registry)}

    color = items[("0001", "attributes__color")]
    design = items[("0001", "attributes__design")]
    assert color.proposed_value == "Blue"
    assert color.source_priority == SourcePriority.MODEL_DATA
    assert color.image_inferred_color is False
    assert "conflicts with image evidence" in (color.conflict or "")
    assert design.proposed_value == "logo"
    assert design.source_priority == SourcePriority.MODEL_DATA
    assert design.conflict is not None


def test_structured_input_keeps_lower_priority_facts_for_other_headers(registry) -> None:
    database = JobDatabase(":memory:")
    data = json.dumps({"brand": "Acme", "notes": "; color: Blue;"})
    job_id = create_extracted_job(database, registry, (input_row(data),))
    items = {item.header: item for item in load_review_items(database, job_id, registry)}

    assert items["attributes__brand"].source_priority == SourcePriority.STRUCTURED_INPUT
    assert items["attributes__color"].proposed_value == "Blue"
    assert items["attributes__color"].source_priority == SourcePriority.MODEL_DATA


def test_approved_alias_and_canonical_sources_do_not_create_false_conflict(registry) -> None:
    database = JobDatabase(":memory:")
    job_id = create_extracted_job(
        database,
        registry,
        (input_row(json.dumps({"fit_type": "A-Line Fit"})),),
    )
    fit = next(
        item
        for item in load_review_items(database, job_id, registry)
        if item.header == "attributes__fit_type"
    )

    assert fit.proposed_value == "A-Line"
    assert fit.conflict is None
    assert "do not yet distinguish" in (fit.warning or "")


def test_all_review_actions_persist_across_restart(tmp_path: Path, registry) -> None:
    path = tmp_path / "review.sqlite3"
    database = JobDatabase(path)
    data = json.dumps(
        {
            "brand": "Acme",
            "product_type": "T-Shirt",
            "model": "M1",
            "material": "Cotton",
        }
    )
    job_id = create_extracted_job(database, registry, (input_row(data),))
    items = {item.header: item for item in load_review_items(database, job_id, registry)}
    persist_review_decision(database, items["attributes__brand"], ReviewAction.ACCEPT, registry)
    persist_review_decision(
        database,
        items["attributes__product_type"],
        ReviewAction.EDIT,
        registry,
        final_value="Crew-neck T-Shirt",
    )
    persist_review_decision(database, items["attributes__model"], ReviewAction.BLANK, registry)
    persist_review_decision(
        database, items["attributes__material"], ReviewAction.REJECT, registry
    )
    database.close()

    restarted = JobDatabase(path)
    restored = {item.header: item for item in load_review_items(restarted, job_id, registry)}
    assert restored["attributes__brand"].review_action == ReviewAction.ACCEPT
    assert restored["attributes__product_type"].final_value == "Crew-neck T-Shirt"
    assert restored["attributes__model"].review_action == ReviewAction.BLANK
    assert restored["attributes__material"].review_action == ReviewAction.REJECT
    assert unresolved_review_items(tuple(restored.values())) == ()


def test_enum_edits_are_restricted_and_registry_changes_revalidate(registry) -> None:
    database = JobDatabase(":memory:")
    job_id = create_extracted_job(
        database, registry, (input_row(json.dumps({"color": "Blue"})),)
    )
    color = next(
        item
        for item in load_review_items(database, job_id, registry)
        if item.header == "attributes__color"
    )
    with pytest.raises(ValueError, match="permitted"):
        persist_review_decision(
            database,
            color,
            ReviewAction.EDIT,
            registry,
            final_value="Navy Blue",
        )
    persist_review_decision(database, color, ReviewAction.ACCEPT, registry)

    permitted = dict(registry.permitted_values_by_header)
    permitted["attributes__color"] = ("Red",)
    changed = registry.model_copy(
        update={"permitted_values_by_header": permitted, "fingerprint": "changed"}
    )
    revalidated = next(
        item
        for item in load_review_items(database, job_id, changed)
        if item.header == "attributes__color"
    )
    assert revalidated.final_value == "Blue"
    assert revalidated.decision_valid is False


def test_numeric_format_units_and_placeholder_values_are_validated(registry) -> None:
    definitions = dict(registry.definitions_by_header)
    definitions["attributes__weight"] = definitions["attributes__weight"].model_copy(
        update={"data_type": DataType.DECIMAL, "unit_or_format": "unit:kg"}
    )
    typed = registry.model_copy(update={"definitions_by_header": definitions})

    assert validate_final_value(typed, "attributes__weight", "1.25 kg") == "1.25 kg"
    with pytest.raises(ValueError, match="configured unit"):
        validate_final_value(typed, "attributes__weight", "1.25 lb")
    with pytest.raises(ValueError, match="Placeholder"):
        validate_final_value(registry, "attributes__brand", "N/A")


def test_bulk_accept_excludes_conflicts_low_evidence_and_image_color(registry) -> None:
    database = JobDatabase(":memory:")
    job_id = create_extracted_job(
        database,
        registry,
        (
            input_row(
                json.dumps({"brand": "Acme", "color": "Blue"})
            ),
        ),
    )
    items = load_review_items(database, job_id, registry)
    brand = next(item for item in items if item.header == "attributes__brand")
    color = next(item for item in items if item.header == "attributes__color")
    unsafe_color = color.model_copy(
        update={"image_inferred_color": True, "warning": None}
    )
    unsafe_conflict = brand.model_copy(update={"conflict": "conflict"})

    assert brand.safe_for_bulk_accept is True
    assert unsafe_color.safe_for_bulk_accept is False
    assert unsafe_conflict.safe_for_bulk_accept is False
    assert bulk_accept_safe(database, (brand, unsafe_color, unsafe_conflict), registry) == 1


def test_occasion_rules_require_support_permission_and_explicit_approval(registry) -> None:
    permitted = dict(registry.permitted_values_by_header)
    permitted["attributes__occasion"] = ("Casual", "Everyday")
    permitted["attributes__occasion_type"] = ("Casual Wear",)
    configured = registry.model_copy(update={"permitted_values_by_header": permitted})
    facts = {
        "attributes__product_type": "T-Shirt",
        "attributes__pattern": "Graphic",
        "attributes__neckline": "Crew Neck",
    }

    assert derive_topwear_occasion(facts, configured) == ({}, ())
    derived, warnings = derive_topwear_occasion(
        facts, configured, ("graphic_crew_tshirt_casual",)
    )
    assert derived == {
        "attributes__occasion": "Casual",
        "attributes__occasion_type": "Casual Wear",
    }
    assert warnings == ()
    assert derive_topwear_occasion(
        {"attributes__product_type": "Blazer"},
        configured,
        ("graphic_crew_tshirt_casual",),
    )[0] == {}
