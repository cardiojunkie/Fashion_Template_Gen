from __future__ import annotations

import pytest

from fashion_cms.models import AnalysisMode, InputRow
from fashion_cms.variant_service import (
    CacheContext,
    ImageAsset,
    build_cache_key,
    build_request_plan,
    build_variant_groups,
)


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


def context(**changes: str | None) -> CacheContext:
    values: dict[str, str | None] = {
        "attribute_set": "topwear",
        "product_profile": "shirts",
        "registry_version": "registry-1",
        "prompt_version": "prompt-1",
        "schema_version": "schema-1",
        "model_identifier": "model-1",
        "image_detail": "high",
    }
    values.update(changes)
    return CacheContext(**values)  # type: ignore[arg-type]


def test_per_sku_is_default_and_size_only_is_only_suggested() -> None:
    groups = build_variant_groups(
        (
            row("SKU-S", description="Red shirt size S"),
            row("SKU-M", description="Red shirt size M", row_number=3),
        )
    )

    assert len(groups) == 1
    assert groups[0].analysis_mode == AnalysisMode.PER_SKU
    assert groups[0].size_only_suggested is True
    assert groups[0].warnings == ()


def test_mixed_modes_and_request_count_are_planned_per_group() -> None:
    groups = build_variant_groups(
        (
            row("A-S", "A", "Black shirt size S"),
            row("A-M", "A", "Black shirt size M", 3),
            row("B-S", "B", "White shirt size S", 4),
            row("B-M", "B", "White shirt size M", 5),
        ),
        modes={"base:A": AnalysisMode.BASE_CODE_SIZE_ONLY},
    )
    plan = build_request_plan(groups, context())

    assert [group.analysis_mode for group in groups] == [
        AnalysisMode.BASE_CODE_SIZE_ONLY,
        AnalysisMode.PER_SKU,
    ]
    assert plan.group_count == 2
    assert plan.sku_count == 4
    assert plan.size_only_group_count == 1
    assert plan.per_sku_group_count == 1
    assert plan.planned_request_count == 3
    assert [item.represented_skus for item in plan.items] == [
        ("A-S", "A-M"),
        ("B-S",),
        ("B-M",),
    ]
    assert plan.items[0].representative_sku == "A-S"


def test_blank_base_code_uses_a_private_sku_fallback_without_changing_input() -> None:
    groups = build_variant_groups(
        (
            row("SAME", None),
            row("OTHER", None, row_number=3),
            row("SKU-3", "SAME", row_number=4),
        )
    )

    assert [group.key for group in groups] == ["sku:SAME", "sku:OTHER", "base:SAME"]
    assert [group.base_code for group in groups] == [None, None, "SAME"]
    assert groups[0].rows[0].base_code is None


@pytest.mark.parametrize(
    ("descriptions", "warning_fragment", "detected_field", "expected"),
    [
        (
            ("Red shirt size S", "Blue shirt size M"),
            "Multiple colors detected",
            "detected_colors",
            ("blue", "red"),
        ),
        (
            ("Solid shirt size S", "Striped shirt size M"),
            "Multiple patterns detected",
            "detected_patterns",
            ("solid", "striped"),
        ),
    ],
)
def test_size_only_warns_about_visible_variant_differences(
    descriptions: tuple[str, str],
    warning_fragment: str,
    detected_field: str,
    expected: tuple[str, str],
) -> None:
    group = build_variant_groups(
        (
            row("SKU-S", description=descriptions[0]),
            row("SKU-M", description=descriptions[1], row_number=3),
        ),
        modes={"base:BASE": AnalysisMode.BASE_CODE_SIZE_ONLY},
    )[0]

    assert getattr(group, detected_field) == expected
    assert any(warning_fragment in warning for warning in group.warnings)


def test_size_only_warns_about_product_pack_model_and_other_differences() -> None:
    group = build_variant_groups(
        (
            row("SKU-1", description="Shirt pack of 1 model: A size S"),
            row("SKU-2", description="Jacket pack of 2 model: B size M", row_number=3),
        ),
        modes={"base:BASE": AnalysisMode.BASE_CODE_SIZE_ONLY},
    )[0]

    assert len(group.detected_product_types) == 2
    assert group.detected_pack_counts == ("1", "2")
    assert group.detected_model_codes == ("a", "b")
    assert {warning.split(" detected", 1)[0] for warning in group.warnings} >= {
        "Multiple product types",
        "Multiple pack counts",
        "Multiple model codes",
    }
    assert any("beyond recognized size terms" in warning for warning in group.warnings)


def test_user_selected_representative_overrides_image_count() -> None:
    group = build_variant_groups(
        (row("SKU-1"), row("SKU-2", row_number=3)),
        (image("SKU-1", 1), image("SKU-1", 2)),
        representatives={"base:BASE": "SKU-2"},
    )[0]

    assert group.representative_sku == "SKU-2"
    assert group.user_selected_representative is True


def test_representative_automatically_uses_the_most_valid_images() -> None:
    group = build_variant_groups(
        (row("SKU-1"), row("SKU-2", row_number=3)),
        (image("SKU-1", 1), image("SKU-2", 1), image("SKU-2", 2)),
    )[0]

    assert group.representative_sku == "SKU-2"
    assert group.user_selected_representative is False


def test_representative_ties_use_workbook_order() -> None:
    group = build_variant_groups(
        (row("LATER-LEXICALLY"), row("A-FIRST-LEXICALLY", row_number=3)),
        (image("LATER-LEXICALLY", 1), image("A-FIRST-LEXICALLY", 1)),
    )[0]

    assert group.representative_sku == "LATER-LEXICALLY"


@pytest.mark.parametrize(
    ("component", "changes"),
    [
        ("analysis mode", {"analysis_mode": AnalysisMode.BASE_CODE_SIZE_ONLY}),
        ("ordered identifiers", {"ordered_identifiers": ("base:BASE", "SKU-2", "SKU-1")}),
        (
            "normalized model data",
            {"model_code_input_data": (("SKU-1", "Blue shirt"),)},
        ),
        ("image hash", {"image_assets": (image("SKU-1", 1, "b"),)}),
        ("attribute set", {"context": context(attribute_set="bottomwear")}),
        ("product profile", {"context": context(product_profile="tees")}),
        ("registry version", {"context": context(registry_version="registry-2")}),
        ("prompt version", {"context": context(prompt_version="prompt-2")}),
        ("schema version", {"context": context(schema_version="schema-2")}),
        ("model identifier", {"context": context(model_identifier="model-2")}),
        ("image detail", {"context": context(image_detail="low")}),
    ],
)
def test_every_cache_contract_component_invalidates_the_key(
    component: str, changes: dict[str, object]
) -> None:
    inputs: dict[str, object] = {
        "analysis_mode": AnalysisMode.PER_SKU,
        "ordered_identifiers": ("base:BASE", "SKU-1"),
        "model_code_input_data": (("SKU-1", "Red shirt"),),
        "image_assets": (image("SKU-1", 1),),
        "context": context(),
    }
    baseline = build_cache_key(**inputs)  # type: ignore[arg-type]
    inputs.update(changes)

    assert build_cache_key(**inputs) != baseline, component  # type: ignore[arg-type]


def test_cache_key_normalizes_equivalent_model_text() -> None:
    inputs = {
        "analysis_mode": AnalysisMode.PER_SKU,
        "ordered_identifiers": ("base:BASE", "SKU-1"),
        "image_assets": (image("SKU-1", 1),),
        "context": context(),
    }

    assert build_cache_key(
        **inputs, model_code_input_data=(("SKU-1", "  Red\u00a0shirt  "),)
    ) == build_cache_key(
        **inputs, model_code_input_data=(("SKU-1", "Red shirt"),)
    )
