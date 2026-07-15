from io import BytesIO
from zipfile import ZipFile

import pytest
from PIL import Image

from fashion_cms import image_service
from fashion_cms.image_service import open_oriented_image, parse_uploaded_images


def image_bytes(image_format: str = "PNG", size: tuple[int, int] = (12, 8), orientation=0) -> bytes:
    image = Image.new("RGB", size, "blue")
    output = BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[274] = orientation
    image.save(output, format=image_format, exif=exif)
    image.close()
    return output.getvalue()


def zip_bytes(files: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def issue_codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


@pytest.mark.parametrize(
    ("extension", "image_format"),
    [("jpg", "JPEG"), ("jpeg", "JPEG"), ("png", "PNG"), ("webp", "WEBP")],
)
def test_supported_uploaded_image_formats(extension: str, image_format: str) -> None:
    result = parse_uploaded_images(
        ((f"SKU-1.{extension}", image_bytes(image_format)),),
        ("SKU",),
    )
    assert result.ready
    assert result.images[0].image_format == image_format


def test_zip_image_uses_complete_hyphenated_sku_final_ordinal_and_exif() -> None:
    jpeg = image_bytes("JPEG", (12, 8), orientation=6)
    result = parse_uploaded_images(
        (("images.zip", zip_bytes({"nested/ABC-12-2.jpg": jpeg})),),
        ("ABC", "ABC-12"),
    )
    assert result.ready
    assert len(result.images) == 1
    assert (result.images[0].sku, result.images[0].ordinal) == ("ABC-12", 2)
    assert (result.images[0].width, result.images[0].height) == (8, 12)
    with open_oriented_image(result.images[0].content) as preview:
        assert preview.size == (8, 12)
    assert "MISSING_IMAGE" in issue_codes(result)  # ABC has no image; warnings do not block.


def test_duplicate_ordinal_across_direct_upload_and_zip_blocks_without_winner() -> None:
    png = image_bytes()
    result = parse_uploaded_images(
        (
            ("ABC-12-01.png", png),
            ("images.zip", zip_bytes({"nested/ABC-12-1.png": png})),
        ),
        ("ABC-12",),
    )
    assert not result.ready
    assert result.images == ()
    assert "DUPLICATE_IMAGE_ORDINAL" in issue_codes(result)


def test_orphan_unsupported_and_missing_images_are_actionable_warnings() -> None:
    result = parse_uploaded_images(
        (
            ("ORPHAN-1.png", image_bytes()),
            ("KNOWN-1.gif", b"gif"),
            ("bad-name.png", image_bytes()),
        ),
        ("KNOWN",),
    )
    assert result.ready
    assert result.images == ()
    assert {
        "ORPHAN_IMAGE",
        "UNSUPPORTED_IMAGE_TYPE",
        "INVALID_IMAGE_NAME",
        "MISSING_IMAGE",
    } <= issue_codes(result)


@pytest.mark.parametrize(
    ("name", "content", "expected_code"),
    [
        ("SKU-1.png", b"broken", "UNREADABLE_IMAGE"),
        ("SKU-1.jpg", image_bytes("PNG"), "IMAGE_FORMAT_MISMATCH"),
        ("images.zip", b"broken", "MALFORMED_ARCHIVE"),
    ],
)
def test_unreadable_or_mislabeled_images_and_malformed_zips_block(
    name: str, content: bytes, expected_code: str
) -> None:
    result = parse_uploaded_images(((name, content),), ("SKU",))
    assert not result.ready
    assert expected_code in issue_codes(result)


@pytest.mark.parametrize("member_name", ["../SKU-1.png", "..\\SKU-1.png"])
def test_zip_traversal_blocks_and_hidden_os_files_are_ignored(member_name: str) -> None:
    archive = zip_bytes(
        {
            member_name: image_bytes(),
            "__MACOSX/._SKU-1.png": b"metadata",
            ".DS_Store": b"metadata",
        }
    )
    result = parse_uploaded_images((("images.zip", archive),), ("SKU",))
    assert not result.ready
    assert issue_codes(result) == {"UNSAFE_FILENAME", "MISSING_IMAGE"}


def test_decoded_pixel_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_service, "MAX_IMAGE_PIXELS", 1)
    result = parse_uploaded_images((("SKU-1.png", image_bytes()),), ("SKU",))
    assert not result.ready
    assert "IMAGE_TOO_MANY_PIXELS" in issue_codes(result)


def test_top_level_upload_count_includes_empty_zips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_service, "MAX_IMAGE_FILES", 1)
    empty_zip = zip_bytes({})
    result = parse_uploaded_images(
        (("one.zip", empty_zip), ("two.zip", empty_zip)),
        ("SKU",),
    )
    assert not result.ready
    assert "TOO_MANY_UPLOADS" in issue_codes(result)


def test_cumulative_zip_expansion_and_direct_byte_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    png = image_bytes()
    monkeypatch.setattr(image_service, "MAX_UNCOMPRESSED_BYTES", len(png) + 1)
    result = parse_uploaded_images(
        (
            ("one.zip", zip_bytes({"SKU-1.png": png})),
            ("two.zip", zip_bytes({"SKU-2.png": png})),
        ),
        ("SKU",),
    )
    assert not result.ready
    assert "UPLOAD_EXPANDS_TOO_LARGE" in issue_codes(result)

    monkeypatch.setattr(image_service, "MAX_IMAGE_BYTES", len(png) - 1)
    result = parse_uploaded_images((("SKU-1.png", png),), ("SKU",))
    assert not result.ready
    assert "IMAGE_TOO_LARGE" in issue_codes(result)


def test_validation_report_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_service, "MAX_VALIDATION_ISSUES", 3)
    result = parse_uploaded_images(
        (("one.txt", b"1"), ("two.txt", b"2"), ("three.txt", b"3")),
        ("SKU",),
    )
    assert not result.ready
    assert len(result.issues) == 3
    assert result.issues[-1].code == "ADDITIONAL_IMAGE_ERRORS"
