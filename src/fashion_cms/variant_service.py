from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from pydantic import BaseModel, ConfigDict, Field

from fashion_cms.models import AnalysisMode, InputRow, UploadedImage


class ImageAsset(BaseModel):
    schema_version: str = "1"
    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str
    ordinal: int = Field(gt=0)
    filename: str
    source_name: str | None = None
    image_format: str | None = None
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @classmethod
    def from_upload(cls, image: UploadedImage) -> ImageAsset:
        return cls(
            sku=image.sku,
            ordinal=image.ordinal,
            filename=image.filename,
            source_name=image.source_name,
            image_format=image.image_format,
            sha256=hashlib.sha256(image.content).hexdigest(),
            width=image.width,
            height=image.height,
        )


class VariantGroup(BaseModel):
    schema_version: str = "1"
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    base_code: str | None = None
    rows: tuple[InputRow, ...]
    images: tuple[ImageAsset, ...] = ()
    analysis_mode: AnalysisMode = AnalysisMode.PER_SKU
    representative_sku: str
    user_selected_representative: bool = False
    detected_colors: tuple[str, ...] = ()
    detected_sizes: tuple[str, ...] = ()
    detected_patterns: tuple[str, ...] = ()
    detected_product_types: tuple[str, ...] = ()
    detected_pack_counts: tuple[str, ...] = ()
    detected_model_codes: tuple[str, ...] = ()
    size_only_warnings: tuple[str, ...] = ()
    size_only_suggested: bool = False

    @property
    def skus(self) -> tuple[str, ...]:
        return tuple(row.sku for row in self.rows)

    @property
    def warnings(self) -> tuple[str, ...]:
        return (
            self.size_only_warnings
            if self.analysis_mode == AnalysisMode.BASE_CODE_SIZE_ONLY
            else ()
        )


class CacheContext(BaseModel):
    schema_version: str
    model_config = ConfigDict(frozen=True, extra="forbid")

    attribute_set: str
    product_profile: str | None = None
    registry_version: str
    prompt_version: str
    model_identifier: str
    image_detail: str


class PlannedWorkItem(BaseModel):
    schema_version: str = "1"
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    group_key: str
    analysis_mode: AnalysisMode
    represented_skus: tuple[str, ...]
    representative_sku: str
    ordered_identifiers: tuple[str, ...]
    normalized_model_data: tuple[tuple[str, str], ...]
    image_assets: tuple[ImageAsset, ...]
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    cache_payload_json: str


class RequestPlan(BaseModel):
    schema_version: str = "1"
    model_config = ConfigDict(frozen=True, extra="forbid")

    groups: tuple[VariantGroup, ...]
    items: tuple[PlannedWorkItem, ...]

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def sku_count(self) -> int:
        return sum(len(group.rows) for group in self.groups)

    @property
    def size_only_group_count(self) -> int:
        return sum(
            group.analysis_mode == AnalysisMode.BASE_CODE_SIZE_ONLY for group in self.groups
        )

    @property
    def per_sku_group_count(self) -> int:
        return self.group_count - self.size_only_group_count

    @property
    def planned_request_count(self) -> int:
        return len(self.items)


_COLORS = {
    "beige",
    "black",
    "blue",
    "brown",
    "burgundy",
    "cream",
    "gold",
    "gray",
    "green",
    "grey",
    "khaki",
    "maroon",
    "multicolor",
    "multi color",
    "navy",
    "navy blue",
    "off white",
    "olive",
    "orange",
    "pink",
    "purple",
    "red",
    "silver",
    "tan",
    "teal",
    "white",
    "yellow",
}
_PATTERNS = {
    "animal print",
    "camouflage",
    "check",
    "checked",
    "checkered",
    "color block",
    "colour block",
    "floral",
    "graphic",
    "paisley",
    "plaid",
    "polka dot",
    "print",
    "printed",
    "solid",
    "stripe",
    "striped",
}
_PRODUCT_TYPES = {
    "bag",
    "blazer",
    "blouse",
    "cap",
    "cardigan",
    "coat",
    "dress",
    "hoodie",
    "jacket",
    "jeans",
    "jumpsuit",
    "leggings",
    "pants",
    "polo",
    "shirt",
    "shorts",
    "skirt",
    "sweater",
    "sweatshirt",
    "t shirt",
    "tee",
    "top",
    "trousers",
    "watch",
}
_NAMED_SIZE = (
    r"(?:xxxxl|xxxl|xxl|xl|xxs|xs|one[ -]?size|extra[ -]?small|small|medium|large|"
    r"extra[ -]?large|[sml])"
)
_SIZE_RE = re.compile(
    rf"(?<!\w)(?:(?:size\s*[:=#-]?\s*)?({_NAMED_SIZE})|"
    rf"size\s*[:=#-]?\s*([0-9]{{1,3}}(?:\.[05])?))(?!\w)",
    re.I,
)
_PACK_RES = (
    re.compile(r"\b(?:pack|set)\s*(?:of\s*)?[:=#-]?\s*([0-9]{1,3})\b", re.I),
    re.compile(r"\b([0-9]{1,3})\s*[- ]?(?:pack|piece|pcs)\b", re.I),
)
_MODEL_RE = re.compile(
    r"\b(?:model(?:\s+code)?|style\s+code)\s*[:=#-]\s*"
    r"([A-Za-z0-9][A-Za-z0-9._/-]{0,39})",
    re.I,
)
_LABEL_RE = re.compile(
    r"(?:^|[;,|\n])\s*"
    r"(color|colour|size|pattern(?:[ _]type)?|product[ _]type|type|"
    r"pack[ _](?:count|size|quantity)|model(?:[ _]code)?|style[ _]code|"
    r"design|sleeve|neckline|closure|finish)\s*[:=]\s*([^;,|\n]+)",
    re.I,
)
_LABEL_ALIASES = {
    "colour": "color",
    "pattern type": "pattern",
    "product type": "product_type",
    "type": "product_type",
    "pack count": "pack_count",
    "pack size": "pack_count",
    "pack quantity": "pack_count",
    "model": "model_code",
    "model code": "model_code",
    "style code": "model_code",
}
_LABELED_VALUE_ALIASES = {
    ("color", "gray"): "grey",
    ("color", "navy blue"): "navy",
    ("color", "multi color"): "multicolor",
    ("pattern", "check"): "checked",
    ("pattern", "checkered"): "checked",
    ("pattern", "colour block"): "color block",
    ("pattern", "print"): "printed",
    ("pattern", "stripe"): "striped",
    ("product_type", "t shirt"): "tee",
}


def _normalized_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _comparison_text(value: str | None) -> str:
    text = _normalized_text(value).casefold()
    return " ".join("".join(character if character.isalnum() else " " for character in text).split())


def _find_terms(
    texts: Sequence[str],
    terms: set[str],
    aliases: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    found = set()
    for text in texts:
        matches = sorted(
            (
                (match.start(), match.end(), term)
                for term in terms
                for match in re.finditer(
                    rf"(?<!\w){re.escape(term)}(?!\w)", text, re.I
                )
            ),
            key=lambda item: (item[0], -(item[1] - item[0])),
        )
        accepted: list[tuple[int, int]] = []
        for start, end, term in matches:
            if any(start >= kept_start and end <= kept_end for kept_start, kept_end in accepted):
                continue
            accepted.append((start, end))
            found.add((aliases or {}).get(term, term))
    return tuple(sorted(found))


def _detected_sizes(texts: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                (match.group(1) or match.group(2)).upper()
                for text in texts
                for match in _SIZE_RE.finditer(text)
            }
        )
    )


def _description_without_size(value: str | None) -> str:
    return _comparison_text(_SIZE_RE.sub(" ", _normalized_text(value)))


def _labeled_values(texts: Sequence[str]) -> dict[str, set[str]]:
    values: dict[str, set[str]] = {}
    for text in texts:
        pairs: list[tuple[str, object]] = []
        try:
            document = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            document = None
        if isinstance(document, dict):
            pairs.extend((str(key), value) for key, value in document.items())
        pairs.extend(match.groups() for match in _LABEL_RE.finditer(text))
        for raw_key, raw_value in pairs:
            key = _comparison_text(raw_key).replace("_", " ")
            key = _LABEL_ALIASES.get(key, key.replace(" ", "_"))
            if key not in {
                "color",
                "size",
                "pattern",
                "product_type",
                "pack_count",
                "model_code",
                "design",
                "sleeve",
                "neckline",
                "closure",
                "finish",
            } or raw_value is None or isinstance(raw_value, (dict, list, tuple, bool)):
                continue
            value = _comparison_text(str(raw_value))
            if value:
                values.setdefault(key, set()).add(
                    _LABELED_VALUE_ALIASES.get((key, value), value)
                )
    return values


def _signals(rows: Sequence[InputRow]) -> dict[str, tuple[str, ...] | bool]:
    texts = tuple(row.model_code_input_data or "" for row in rows)
    labeled = _labeled_values(texts)
    colors = tuple(
        sorted(
            set(
                _find_terms(
                    texts,
                    _COLORS,
                    {
                        "gray": "grey",
                        "navy blue": "navy",
                        "multi color": "multicolor",
                    },
                )
            )
            | labeled.get("color", set())
        )
    )
    patterns = tuple(
        sorted(
            set(
                _find_terms(
                    texts,
                    _PATTERNS,
                    {
                        "check": "checked",
                        "checkered": "checked",
                        "colour block": "color block",
                        "print": "printed",
                        "stripe": "striped",
                    },
                )
            )
            | labeled.get("pattern", set())
        )
    )
    product_types = tuple(
        sorted(
            set(_find_terms(texts, _PRODUCT_TYPES, {"t shirt": "tee"}))
            | labeled.get("product_type", set())
        )
    )
    sizes = tuple(
        sorted(set(_detected_sizes(texts)) | {value.upper() for value in labeled.get("size", set())})
    )
    pack_counts = tuple(
        sorted(
            {
                match.group(1)
                for text in texts
                for pattern in _PACK_RES
                for match in pattern.finditer(text)
            }
            | labeled.get("pack_count", set()),
            key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
        )
    )
    model_codes = tuple(
        sorted(
            {match.group(1).casefold() for text in texts for match in _MODEL_RE.finditer(text)}
            | labeled.get("model_code", set())
        )
    )
    visible_differences = {
        key: tuple(sorted(labeled.get(key, set())))
        for key in ("design", "sleeve", "neckline", "closure", "finish")
    }
    normalized = tuple(_comparison_text(text) for text in texts)
    without_size = tuple(_description_without_size(text) for text in texts)
    non_size_difference = len(set(without_size)) > 1
    suggestion = (
        len(rows) > 1
        and all(normalized)
        and len(set(normalized)) > 1
        and len(set(without_size)) == 1
        and bool(without_size[0])
        and len(sizes) > 1
    )
    warnings = []
    for label, values in (
        ("colors", colors),
        ("patterns", patterns),
        ("product types", product_types),
        ("pack counts", pack_counts),
        ("model codes", model_codes),
    ):
        if len(values) > 1:
            warnings.append(f"Multiple {label} detected: {', '.join(values)}.")
    for label, values in visible_differences.items():
        if len(values) > 1:
            warnings.append(f"Multiple {label} values detected: {', '.join(values)}.")
    if non_size_difference:
        warnings.append(
            "Descriptions differ beyond recognized size terms; review other visible differences."
        )
    return {
        "detected_colors": colors,
        "detected_sizes": sizes,
        "detected_patterns": patterns,
        "detected_product_types": product_types,
        "detected_pack_counts": pack_counts,
        "detected_model_codes": model_codes,
        "size_only_warnings": tuple(warnings),
        "size_only_suggested": suggestion,
    }


def internal_group_key(row: InputRow) -> str:
    kind = "base" if row.base_code else "sku"
    value = row.base_code or row.sku
    return f"{kind}:{value}"


def select_representative_sku(
    rows: Sequence[InputRow],
    images: Sequence[ImageAsset | UploadedImage] = (),
    user_selected: str | None = None,
) -> str:
    skus = tuple(row.sku for row in rows)
    if not skus:
        raise ValueError("A variant group must contain at least one SKU.")
    if user_selected is not None:
        if user_selected not in skus:
            raise ValueError("Representative SKU must belong to its variant group.")
        return user_selected
    counts = Counter(image.sku for image in images if image.sku in skus)
    return max(skus, key=lambda sku: counts[sku])


def build_variant_groups(
    rows: Sequence[InputRow],
    images: Sequence[UploadedImage | ImageAsset] = (),
    *,
    modes: Mapping[str, AnalysisMode | str] | None = None,
    representatives: Mapping[str, str] | None = None,
) -> tuple[VariantGroup, ...]:
    assets = tuple(
        image if isinstance(image, ImageAsset) else ImageAsset.from_upload(image)
        for image in images
    )
    grouped: dict[str, list[InputRow]] = {}
    for row in rows:
        grouped.setdefault(internal_group_key(row), []).append(row)

    result = []
    for key, group_rows in grouped.items():
        sku_position = {row.sku: position for position, row in enumerate(group_rows)}
        group_assets = tuple(
            sorted(
                (asset for asset in assets if asset.sku in sku_position),
                key=lambda asset: (
                    sku_position[asset.sku],
                    asset.ordinal,
                    asset.filename,
                ),
            )
        )
        fallback_selection_key = group_rows[0].base_code or group_rows[0].sku
        raw_mode = (modes or {}).get(
            key,
            (modes or {}).get(fallback_selection_key, AnalysisMode.PER_SKU),
        )
        mode = AnalysisMode(raw_mode)
        selected = (representatives or {}).get(
            key, (representatives or {}).get(fallback_selection_key)
        )
        representative = select_representative_sku(group_rows, group_assets, selected)
        result.append(
            VariantGroup(
                key=key,
                base_code=group_rows[0].base_code,
                rows=tuple(group_rows),
                images=group_assets,
                analysis_mode=mode,
                representative_sku=representative,
                user_selected_representative=selected is not None,
                **_signals(group_rows),
            )
        )
    return tuple(result)


def build_cache_payload(
    *,
    analysis_mode: AnalysisMode | str,
    ordered_identifiers: Sequence[str],
    model_code_input_data: Sequence[tuple[str, str | None]],
    image_assets: Sequence[ImageAsset],
    context: CacheContext,
) -> dict[str, object]:
    return {
        "analysis_mode": AnalysisMode(analysis_mode).value,
        "ordered_identifiers": list(ordered_identifiers),
        "model_code_input_data": [
            [sku, _normalized_text(value)] for sku, value in model_code_input_data
        ],
        "selected_images": [
            [asset.sku, asset.ordinal, asset.sha256] for asset in image_assets
        ],
        "attribute_set": context.attribute_set,
        "product_profile": context.product_profile,
        "registry_version": context.registry_version,
        "prompt_version": context.prompt_version,
        "schema_version": context.schema_version,
        "model_identifier": context.model_identifier,
        "image_detail": context.image_detail,
    }


def build_cache_key(
    *,
    analysis_mode: AnalysisMode | str,
    ordered_identifiers: Sequence[str],
    model_code_input_data: Sequence[tuple[str, str | None]],
    image_assets: Sequence[ImageAsset],
    context: CacheContext,
) -> str:
    payload = build_cache_payload(
        analysis_mode=analysis_mode,
        ordered_identifiers=ordered_identifiers,
        model_code_input_data=model_code_input_data,
        image_assets=image_assets,
        context=context,
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def build_request_plan(groups: Sequence[VariantGroup], context: CacheContext) -> RequestPlan:
    items = []
    for group in groups:
        requests = (
            ((row.sku,), row.sku, (row,))
            for row in group.rows
        ) if group.analysis_mode == AnalysisMode.PER_SKU else (
            ((tuple(row.sku for row in group.rows)), group.representative_sku, group.rows),
        )
        for represented_skus, representative, relevant_rows in requests:
            selected_images = tuple(
                image for image in group.images if image.sku == representative
            )
            identifiers = (group.key, *represented_skus)
            model_data = tuple(
                (row.sku, _normalized_text(row.model_code_input_data))
                for row in relevant_rows
            )
            cache_payload = build_cache_payload(
                analysis_mode=group.analysis_mode,
                ordered_identifiers=identifiers,
                model_code_input_data=tuple(
                    (row.sku, row.model_code_input_data) for row in relevant_rows
                ),
                image_assets=selected_images,
                context=context,
            )
            cache_payload_json = json.dumps(
                cache_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            cache_key = hashlib.sha256(cache_payload_json.encode()).hexdigest()
            item_key = hashlib.sha256(
                json.dumps(
                    [group.key, group.analysis_mode.value, represented_skus],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            items.append(
                PlannedWorkItem(
                    key=item_key,
                    group_key=group.key,
                    analysis_mode=group.analysis_mode,
                    represented_skus=represented_skus,
                    representative_sku=representative,
                    ordered_identifiers=identifiers,
                    normalized_model_data=model_data,
                    image_assets=selected_images,
                    cache_key=cache_key,
                    cache_payload_json=cache_payload_json,
                )
            )
    return RequestPlan(groups=tuple(groups), items=tuple(items))


# Readable aliases for callers and tests.
group_variants = build_variant_groups
plan_requests = build_request_plan
