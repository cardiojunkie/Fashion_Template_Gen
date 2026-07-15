from __future__ import annotations

import re
import stat
import warnings
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Protocol
from zipfile import BadZipFile, LargeZipFile, ZipFile

from PIL import Image, ImageOps, UnidentifiedImageError

from fashion_cms.models import ImageResult, Severity, UploadedImage, ValidationIssue


SUPPORTED_SUFFIXES = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
IMAGE_NAME = re.compile(r"(.+)-([0-9]+)")
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 1_000
MAX_IMAGE_FILES = 500
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_TOTAL_UPLOAD_BYTES = 250 * 1024 * 1024
MAX_FILENAME_CHARACTERS = 1_024
MAX_VALIDATION_ISSUES = 200
IGNORED_NAMES = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}


@dataclass(frozen=True)
class StandardizedImage:
    content: bytes
    source_dimensions: tuple[int, int]
    output_dimensions: tuple[int, int]
    low_resolution: bool


class BackgroundRemovalAdapter(Protocol):
    def remove_background(self, content: bytes) -> bytes: ...


class _ImageDecodeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _issue(
    severity: Severity, code: str, message: str, location: str | None = None
) -> ValidationIssue:
    return ValidationIssue(severity=severity, code=code, message=message, location=location)


class _IssueList(list[ValidationIssue]):
    def append(self, issue: ValidationIssue) -> None:
        if len(self) < MAX_VALIDATION_ISSUES - 1:
            super().append(issue)
        elif len(self) == MAX_VALIDATION_ISSUES - 1:
            super().append(
                _issue(
                    Severity.CRITICAL,
                    "ADDITIONAL_IMAGE_ERRORS",
                    f"More than {MAX_VALIDATION_ISSUES:,} image findings were detected; "
                    "additional findings were omitted.",
                )
            )


def _safe_path(name: str) -> tuple[PurePosixPath | None, ValidationIssue | None]:
    if len(name) > MAX_FILENAME_CHARACTERS:
        return None, _issue(
            Severity.CRITICAL,
            "FILENAME_TOO_LONG",
            f"Filename exceeds {MAX_FILENAME_CHARACTERS:,} characters.",
            name[:120] + "...",
        )
    if "\x00" in name:
        return None, _issue(
            Severity.CRITICAL, "UNSAFE_FILENAME", "Filename contains a null byte.", name[:120]
        )
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or (path.parts and path.parts[0].endswith(":")):
        return None, _issue(
            Severity.CRITICAL,
            "UNSAFE_FILENAME",
            "Absolute and parent-traversal paths are not allowed.",
            name[:120],
        )
    return path, None


def _is_hidden(path: PurePosixPath) -> bool:
    return any(part.startswith(".") or part.casefold() in IGNORED_NAMES for part in path.parts)


def _expand_archive(
    archive_name: str,
    content: bytes,
    issues: list[ValidationIssue],
    remaining_files: int,
    remaining_bytes: int,
) -> list[tuple[str, str, bytes]]:
    if len(content) > MAX_ARCHIVE_BYTES:
        issues.append(
            _issue(
                Severity.CRITICAL,
                "ARCHIVE_TOO_LARGE",
                f"ZIP exceeds {MAX_ARCHIVE_BYTES // 1024 // 1024} MB.",
                archive_name,
            )
        )
        return []
    try:
        archive = ZipFile(BytesIO(content))
    except (BadZipFile, LargeZipFile, OSError) as exc:
        issues.append(
            _issue(
                Severity.CRITICAL,
                "MALFORMED_ARCHIVE",
                f"Cannot open ZIP: {exc}",
                archive_name,
            )
        )
        return []

    expanded: list[tuple[str, str, bytes]] = []
    try:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            issues.append(
                _issue(
                    Severity.CRITICAL,
                    "ARCHIVE_TOO_MANY_MEMBERS",
                    f"ZIP contains more than {MAX_ARCHIVE_MEMBERS:,} entries.",
                    archive_name,
                )
            )
            return []
        if sum(member.file_size for member in members) > MAX_UNCOMPRESSED_BYTES:
            issues.append(
                _issue(
                    Severity.CRITICAL,
                    "ARCHIVE_EXPANDS_TOO_LARGE",
                    f"ZIP expands beyond {MAX_UNCOMPRESSED_BYTES // 1024 // 1024} MB.",
                    archive_name,
                )
            )
            return []

        readable_members = []
        for member in members:
            path, path_issue = _safe_path(member.filename)
            if path_issue:
                issues.append(
                    path_issue.model_copy(
                        update={"location": f"{archive_name}!{member.filename[:120]}"}
                    )
                )
                continue
            if path is None or member.is_dir() or _is_hidden(path):
                continue
            location = f"{archive_name}!{member.filename[:120]}"
            if stat.S_ISLNK(member.external_attr >> 16):
                issues.append(
                    _issue(
                        Severity.CRITICAL,
                        "ARCHIVE_SYMLINK",
                        "ZIP symbolic links are not allowed.",
                        location,
                    )
                )
                continue
            if member.flag_bits & 1:
                issues.append(
                    _issue(
                        Severity.CRITICAL,
                        "ENCRYPTED_ARCHIVE_MEMBER",
                        "Encrypted ZIP entries are not supported.",
                        location,
                    )
                )
                continue
            readable_members.append((member, path, location))

        if len(readable_members) > remaining_files:
            issues.append(
                _issue(
                    Severity.CRITICAL,
                    "TOO_MANY_IMAGES",
                    f"Uploads contain more than {MAX_IMAGE_FILES:,} files.",
                    archive_name,
                )
            )
            return []
        if sum(member.file_size for member, _, _ in readable_members) > remaining_bytes:
            issues.append(
                _issue(
                    Severity.CRITICAL,
                    "UPLOAD_EXPANDS_TOO_LARGE",
                    f"Uploads expand beyond {MAX_UNCOMPRESSED_BYTES // 1024 // 1024} MB.",
                    archive_name,
                )
            )
            return []

        for member, path, location in readable_members:
            if member.file_size > MAX_IMAGE_BYTES:
                issues.append(
                    _issue(
                        Severity.CRITICAL,
                        "IMAGE_TOO_LARGE",
                        f"File exceeds {MAX_IMAGE_BYTES // 1024 // 1024} MB.",
                        location,
                    )
                )
                continue
            try:
                with archive.open(member) as stream:
                    data = stream.read(MAX_IMAGE_BYTES + 1)
            except (BadZipFile, NotImplementedError, OSError, RuntimeError) as exc:
                issues.append(
                    _issue(
                        Severity.CRITICAL,
                        "ARCHIVE_READ_ERROR",
                        f"Cannot read ZIP entry: {exc}",
                        location,
                    )
                )
                continue
            if len(data) > MAX_IMAGE_BYTES:
                issues.append(
                    _issue(
                        Severity.CRITICAL,
                        "IMAGE_TOO_LARGE",
                        f"File expands beyond {MAX_IMAGE_BYTES // 1024 // 1024} MB.",
                        location,
                    )
                )
                continue
            expanded.append((location, path.name, data))
    finally:
        archive.close()
    return expanded


def _expand_uploads(
    uploads: tuple[tuple[str, bytes], ...], issues: list[ValidationIssue]
) -> list[tuple[str, str, bytes]]:
    if len(uploads) > MAX_IMAGE_FILES:
        issues.append(
            _issue(
                Severity.CRITICAL,
                "TOO_MANY_UPLOADS",
                f"Upload no more than {MAX_IMAGE_FILES:,} files or ZIPs at once.",
            )
        )
        return []
    if any(
        not isinstance(name, str) or not isinstance(content, bytes) for name, content in uploads
    ):
        issues.append(
            _issue(
                Severity.CRITICAL,
                "INVALID_UPLOAD",
                "Each upload must have a filename and byte content.",
            )
        )
        return []
    if sum(len(content) for _, content in uploads) > MAX_TOTAL_UPLOAD_BYTES:
        issues.append(
            _issue(
                Severity.CRITICAL,
                "UPLOAD_TOO_LARGE",
                f"Uploads exceed {MAX_TOTAL_UPLOAD_BYTES // 1024 // 1024} MB in total.",
            )
        )
        return []

    files: list[tuple[str, str, bytes]] = []
    expanded_bytes = 0
    for name, content in uploads:
        path, path_issue = _safe_path(name)
        if path_issue:
            issues.append(path_issue)
            continue
        if path is None or _is_hidden(path):
            continue
        if path.suffix.casefold() == ".zip":
            expanded = _expand_archive(
                path.name,
                content,
                issues,
                MAX_IMAGE_FILES - len(files),
                MAX_UNCOMPRESSED_BYTES - expanded_bytes,
            )
            files.extend(expanded)
            expanded_bytes += sum(len(data) for _, _, data in expanded)
            continue
        if len(content) > MAX_IMAGE_BYTES:
            issues.append(
                _issue(
                    Severity.CRITICAL,
                    "IMAGE_TOO_LARGE",
                    f"File exceeds {MAX_IMAGE_BYTES // 1024 // 1024} MB.",
                    path.name,
                )
            )
            continue
        if len(files) >= MAX_IMAGE_FILES:
            issues.append(
                _issue(
                    Severity.CRITICAL,
                    "TOO_MANY_IMAGES",
                    f"Uploads contain more than {MAX_IMAGE_FILES:,} files.",
                )
            )
            return []
        if expanded_bytes + len(content) > MAX_UNCOMPRESSED_BYTES:
            issues.append(
                _issue(
                    Severity.CRITICAL,
                    "UPLOAD_EXPANDS_TOO_LARGE",
                    f"Uploads exceed {MAX_UNCOMPRESSED_BYTES // 1024 // 1024} MB.",
                )
            )
            return []
        files.append((path.name, path.name, content))
        expanded_bytes += len(content)
    return files


def _open_validated_image(
    content: bytes,
    *,
    max_pixels: int,
    expected_format: str | None = None,
) -> tuple[Image.Image, str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as probe:
                if probe.format not in set(SUPPORTED_SUFFIXES.values()):
                    raise _ImageDecodeError(
                        "UNSUPPORTED_IMAGE_CONTENT",
                        "File content is not JPEG, PNG, or WEBP.",
                    )
                image_format = probe.format
                if expected_format is not None and image_format != expected_format:
                    raise _ImageDecodeError(
                        "IMAGE_FORMAT_MISMATCH",
                        f"Extension expects {expected_format}, but content is {image_format}.",
                    )
                if probe.width * probe.height > max_pixels:
                    raise _ImageDecodeError(
                        "IMAGE_TOO_MANY_PIXELS",
                        f"Image exceeds {max_pixels:,} decoded pixels.",
                    )
                probe.verify()
            with Image.open(BytesIO(content)) as image:
                with ImageOps.exif_transpose(image) as oriented:
                    oriented.load()
                    return oriented.copy(), image_format
    except _ImageDecodeError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise _ImageDecodeError("UNREADABLE_IMAGE", "Cannot decode image data.") from exc


def _decode_image(
    source_name: str, filename: str, sku: str, ordinal: int, content: bytes
) -> tuple[UploadedImage | None, ValidationIssue | None]:
    suffix = PurePosixPath(filename).suffix.casefold()
    expected_format = SUPPORTED_SUFFIXES[suffix]
    try:
        oriented, _ = _open_validated_image(
            content,
            max_pixels=MAX_IMAGE_PIXELS,
            expected_format=expected_format,
        )
    except _ImageDecodeError as exc:
        return None, _issue(Severity.CRITICAL, exc.code, str(exc), source_name)

    with oriented:
        width, height = oriented.size

    return (
        UploadedImage(
            source_name=source_name,
            filename=filename,
            sku=sku,
            ordinal=ordinal,
            image_format=expected_format,
            width=width,
            height=height,
            content=content,
        ),
        None,
    )


def parse_uploaded_images(
    uploads: tuple[tuple[str, bytes], ...], skus: tuple[str, ...]
) -> ImageResult:
    issues: list[ValidationIssue] = _IssueList()
    files = _expand_uploads(tuple(uploads), issues)
    sku_order = {sku: position for position, sku in enumerate(dict.fromkeys(skus))}
    parsed: dict[tuple[str, int], list[tuple[str, str, bytes]]] = defaultdict(list)

    for source_name, filename, content in files:
        path = PurePosixPath(filename)
        suffix = path.suffix.casefold()
        if suffix not in SUPPORTED_SUFFIXES:
            issues.append(
                _issue(
                    Severity.WARNING,
                    "UNSUPPORTED_IMAGE_TYPE",
                    "Supported image types are .jpg, .jpeg, .png, and .webp.",
                    source_name,
                )
            )
            continue
        match = IMAGE_NAME.fullmatch(path.stem)
        if not match:
            issues.append(
                _issue(
                    Severity.WARNING,
                    "INVALID_IMAGE_NAME",
                    "Use the filename pattern SKU-positiveOrdinal.ext.",
                    source_name,
                )
            )
            continue
        sku, ordinal_text = match.groups()
        try:
            ordinal = int(ordinal_text)
        except ValueError:
            ordinal = 0
        if ordinal <= 0:
            issues.append(
                _issue(
                    Severity.WARNING,
                    "INVALID_IMAGE_NAME",
                    "Image ordinal must be a positive integer.",
                    source_name,
                )
            )
            continue
        if sku not in sku_order:
            issues.append(
                _issue(
                    Severity.WARNING,
                    "ORPHAN_IMAGE",
                    f"No workbook row has SKU {(sku[:77] + '...' if len(sku) > 80 else sku)!r}.",
                    source_name,
                )
            )
            continue
        parsed[(sku, ordinal)].append((source_name, filename, content))

    images: list[UploadedImage] = []
    for (sku, ordinal), candidates in parsed.items():
        if len(candidates) > 1:
            examples = ", ".join(candidate[0][:120] for candidate in candidates[:5])
            if len(candidates) > 5:
                examples += f", and {len(candidates) - 5:,} more"
            issues.append(
                _issue(
                    Severity.CRITICAL,
                    "DUPLICATE_IMAGE_ORDINAL",
                    f"SKU {(sku[:77] + '...' if len(sku) > 80 else sku)!r} ordinal "
                    f"{ordinal} has multiple files: {examples}",
                )
            )
            continue
        source_name, filename, content = candidates[0]
        image, image_issue = _decode_image(source_name, filename, sku, ordinal, content)
        if image_issue:
            issues.append(image_issue)
        elif image:
            images.append(image)

    skus_with_images = {image.sku for image in images}
    for sku in sku_order:
        if sku not in skus_with_images:
            issues.append(
                _issue(
                    Severity.WARNING,
                    "MISSING_IMAGE",
                    f"No valid uploaded image was found for SKU "
                    f"{(sku[:77] + '...' if len(sku) > 80 else sku)!r}.",
                    sku[:120],
                )
            )
    images.sort(key=lambda image: (sku_order[image.sku], image.ordinal, image.source_name))
    return ImageResult(images=tuple(images), issues=tuple(issues))


def open_oriented_image(content: bytes) -> Image.Image:
    image, _ = _open_validated_image(content, max_pixels=MAX_IMAGE_PIXELS)
    return image


def _positive_dimensions(name: str, dimensions: tuple[int, int]) -> tuple[int, int]:
    if (
        not isinstance(dimensions, tuple)
        or len(dimensions) != 2
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in dimensions
        )
    ):
        raise ValueError(f"{name} must contain two positive integers.")
    return dimensions


def standardize_pad_white(
    content: bytes,
    *,
    content_box: tuple[int, int] = (1400, 1400),
    canvas_size: tuple[int, int] = (1500, 1500),
    upscale: bool = False,
    quality: int = 95,
    max_pixels: int | None = None,
) -> StandardizedImage:
    """Orient and fit a supported image onto a centered white RGB JPEG canvas."""
    if not isinstance(content, bytes):
        raise ValueError("Image content must be bytes.")
    box_width, box_height = _positive_dimensions("content_box", content_box)
    canvas_width, canvas_height = _positive_dimensions("canvas_size", canvas_size)
    if box_width > canvas_width or box_height > canvas_height:
        raise ValueError("content_box must fit inside canvas_size.")
    if canvas_width * canvas_height > MAX_IMAGE_PIXELS:
        raise ValueError(f"Canvas exceeds {MAX_IMAGE_PIXELS:,} pixels.")
    if not isinstance(upscale, bool):
        raise ValueError("upscale must be true or false.")
    if not isinstance(quality, int) or isinstance(quality, bool) or not 1 <= quality <= 100:
        raise ValueError("quality must be an integer from 1 to 100.")
    pixel_limit = MAX_IMAGE_PIXELS if max_pixels is None else max_pixels
    if not isinstance(pixel_limit, int) or isinstance(pixel_limit, bool) or pixel_limit <= 0:
        raise ValueError("max_pixels must be a positive integer.")

    try:
        source, _ = _open_validated_image(content, max_pixels=pixel_limit)
    except _ImageDecodeError as exc:
        raise ValueError(str(exc)) from exc

    with source:
        source_dimensions = source.size
        low_resolution = (
            min(
                box_width / source.width,
                box_height / source.height,
            )
            > 1
        )
        if "A" in source.getbands() or "transparency" in source.info:
            with source.convert("RGBA") as rgba:
                converted = Image.new("RGB", source.size, "white")
                with rgba.getchannel("A") as alpha:
                    converted.paste(rgba, mask=alpha)
        else:
            converted = source.convert("RGB")

    with converted:
        if upscale or converted.width > box_width or converted.height > box_height:
            placed = ImageOps.contain(
                converted,
                (box_width, box_height),
                method=Image.Resampling.LANCZOS,
            )
        else:
            placed = converted.copy()

    with placed, Image.new("RGB", (canvas_width, canvas_height), "white") as canvas:
        canvas.paste(
            placed,
            ((canvas_width - placed.width) // 2, (canvas_height - placed.height) // 2),
        )
        output = BytesIO()
        canvas.save(output, format="JPEG", quality=quality, optimize=True)
        return StandardizedImage(
            content=output.getvalue(),
            source_dimensions=source_dimensions,
            output_dimensions=canvas.size,
            low_resolution=low_resolution,
        )
