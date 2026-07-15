import re
import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook

from fashion_cms.registry import (
    DataType,
    EvidencePolicy,
    RegistryValidationError,
    load_registry,
    normalize_value,
)


ROOT = Path(__file__).parents[1]
REGISTRY_PATH = ROOT / "config" / "attribute_registry.xlsx"


def expected_attribute_sets() -> dict[str, tuple[str, tuple[str, ...]]]:
    plan = (ROOT / "PLAN.md").read_text()
    blocks = re.findall(
        r"## A\d+\. (.*?) \(`([^`]+)`\)\n\n```text\n(.*?)\n```",
        plan,
        re.DOTALL,
    )
    return {set_id: (name, tuple(headers.splitlines())) for name, set_id, headers in blocks}


def workbook_fixture(tmp_path: Path, mutate) -> Path:
    path = tmp_path / "registry.xlsx"
    shutil.copy2(REGISTRY_PATH, path)
    workbook = load_workbook(path)
    mutate(workbook)
    workbook.save(path)
    return path


def row_with_value(worksheet, column: int, value: str) -> int:
    return next(
        row
        for row in range(2, worksheet.max_row + 1)
        if worksheet.cell(row, column).value == value
    )


def test_committed_header_names_membership_and_order_match_plan() -> None:
    registry = load_registry(REGISTRY_PATH)
    expected = expected_attribute_sets()

    assert list(registry.mappings_by_set) == list(expected)
    for set_id, (name, headers) in expected.items():
        rows = [row for row in registry.attribute_sets if row.attribute_set_id == set_id]
        assert {row.attribute_set_name for row in rows} == {name}
        assert registry.mappings_by_set[set_id] == headers


def test_committed_registry_has_one_correct_definition_per_header() -> None:
    registry = load_registry(REGISTRY_PATH)
    mapped_headers = {header for headers in registry.mappings_by_set.values() for header in headers}

    assert len(registry.definitions) == len(mapped_headers) == 78
    assert set(registry.definitions_by_header) == mapped_headers
    for header in {
        "sku",
        "base_code",
        "attributes__lulu_ean",
        "attributes__shipping_weight",
    }:
        definition = registry.definitions_by_header[header]
        assert definition.data_type == DataType.SYSTEM_COPY
        assert definition.evidence_policy == EvidencePolicy.SYSTEM_COPY
    for header in {
        "attributes__keywords",
        "name",
        "attributes__product_title",
        *(f"attributes__bullet_point_{number}" for number in range(1, 7)),
    }:
        definition = registry.definitions_by_header[header]
        assert definition.data_type == DataType.GENERATED_TEXT
        assert definition.evidence_policy == EvidencePolicy.GENERATED_CONTENT

    assert registry.permitted_values_by_header["attributes__color"] == (
        "Blue",
        "Red",
        "White",
        "Black",
        "Green",
        "Grey",
        "Brown",
    )
    assert registry.definitions_by_header["attributes__color"].data_type == DataType.ENUM
    assert set(registry.profiles_by_id) == {("topwear", "topwear_mvp")}
    assert registry.aliases[0].active is False
    assert registry.aliases_by_header == {}
    assert len(registry.fingerprint) == 64


def test_normalization_handles_unicode_case_whitespace_and_punctuation() -> None:
    assert normalize_value("  Ａ-Line__FIT  ") == normalize_value("a line fit")


def test_duplicate_header_is_rejected(tmp_path: Path) -> None:
    def mutate(workbook) -> None:
        sheet = workbook["Attribute_Sets"]
        sheet.append(["topwear", "Topwear", 46, "attributes__color", False])

    path = workbook_fixture(tmp_path, mutate)
    with pytest.raises(RegistryValidationError, match="duplicate headers"):
        load_registry(path)


def test_duplicate_canonical_value_is_rejected(tmp_path: Path) -> None:
    def mutate(workbook) -> None:
        definitions = workbook["Attribute_Definitions"]
        definition_row = row_with_value(definitions, 1, "attributes__fit_type")
        definitions.cell(definition_row, 2).value = "ENUM"

        values = workbook["Permitted_Values"]
        values.cell(1, 4).value = "value_2"
        value_row = row_with_value(values, 1, "attributes__fit_type")
        values.cell(value_row, 2).value = "ENUM"
        values.cell(value_row, 3).value = "A-Line"
        values.cell(value_row, 4).value = "a line"

    path = workbook_fixture(tmp_path, mutate)
    with pytest.raises(RegistryValidationError, match="duplicate canonical values"):
        load_registry(path)


def test_missing_definition_is_rejected(tmp_path: Path) -> None:
    def mutate(workbook) -> None:
        sheet = workbook["Attribute_Definitions"]
        sheet.delete_rows(row_with_value(sheet, 1, "attributes__color"))

    path = workbook_fixture(tmp_path, mutate)
    with pytest.raises(RegistryValidationError, match="missing definitions"):
        load_registry(path)


def test_active_alias_without_canonical_value_is_rejected(tmp_path: Path) -> None:
    def mutate(workbook) -> None:
        workbook["Value_Aliases"].cell(2, 4).value = True

    path = workbook_fixture(tmp_path, mutate)
    with pytest.raises(RegistryValidationError, match="points to missing canonical value"):
        load_registry(path)


def test_invalid_data_type_is_rejected(tmp_path: Path) -> None:
    def mutate(workbook) -> None:
        sheet = workbook["Attribute_Definitions"]
        row = row_with_value(sheet, 1, "attributes__color")
        sheet.cell(row, 2).value = "NOT_A_DATA_TYPE"

    path = workbook_fixture(tmp_path, mutate)
    with pytest.raises(RegistryValidationError, match="Input should be"):
        load_registry(path)


def test_invalid_profile_is_rejected(tmp_path: Path) -> None:
    def mutate(workbook) -> None:
        workbook["Product_Profiles"].append(
            ["topwear", "shirt", "shirts", "attributes__not_a_header", True]
        )

    path = workbook_fixture(tmp_path, mutate)
    with pytest.raises(RegistryValidationError, match="is not in topwear"):
        load_registry(path)
