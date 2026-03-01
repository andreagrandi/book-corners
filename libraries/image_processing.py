from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import IO
from uuid import uuid4

from django.core.files.base import ContentFile
from django.utils.text import slugify
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_LIBRARY_PHOTO_DIMENSION = 1600
LIBRARY_THUMBNAIL_MAX_WIDTH = 400
LIBRARY_PHOTO_JPEG_QUALITY = 85
MIN_ASPECT_RATIO = 4 / 5  # 0.8 — Instagram lower bound (4:5 portrait)
MAX_ASPECT_RATIO = 1.91  # Instagram upper bound (≈ 1.91:1 landscape)


def _crop_to_aspect_ratio_bounds(*, image: Image.Image) -> Image.Image:
    """Center-crop an image so its aspect ratio falls within Instagram bounds.
    Returns the image unchanged when the ratio is already within range."""
    width, height = image.size
    ratio = width / height

    if ratio > MAX_ASPECT_RATIO:
        new_width = round(height * MAX_ASPECT_RATIO)
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))

    if ratio < MIN_ASPECT_RATIO:
        new_height = round(width / MIN_ASPECT_RATIO)
        top = (height - new_height) // 2
        return image.crop((0, top, width, top + new_height))

    return image


def _normalize_base_filename(*, original_name: str) -> str:
    """Create a safe basename for optimized photo outputs.
    Ensures generated filenames remain predictable and filesystem friendly."""
    stem = Path(original_name).stem
    normalized_stem = slugify(stem)
    return normalized_stem or "library-photo"


def _resize_to_max_dimension(*, image: Image.Image, max_dimension: int) -> Image.Image:
    """Resize an image to fit within a square max dimension.
    Preserves aspect ratio and prevents upscaling of small images."""
    resized_image = image.copy()
    resized_image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    return resized_image


def _resize_to_max_width(*, image: Image.Image, max_width: int) -> Image.Image:
    """Resize an image to a bounded width while preserving aspect ratio.
    Returns a copy when the image is already narrower than the target width."""
    if image.width <= max_width:
        return image.copy()

    target_height = max(1, round((image.height * max_width) / image.width))
    return image.resize((max_width, target_height), Image.Resampling.LANCZOS)


def _encode_jpeg(*, image: Image.Image, quality: int) -> bytes:
    """Encode an RGB image to optimized JPEG bytes.
    Applies a consistent compression level for upload outputs."""
    payload = BytesIO()
    image.save(payload, format="JPEG", quality=quality, optimize=True)
    return payload.getvalue()


def build_library_photo_files(
    *,
    image_file: IO[bytes],
    original_name: str,
) -> tuple[tuple[str, ContentFile], tuple[str, ContentFile]]:
    """Build optimized main and thumbnail files from an uploaded image.
    Produces JPEG outputs with bounded dimensions for web rendering."""
    start_position: int | None = None
    if hasattr(image_file, "tell"):
        try:
            start_position = image_file.tell()
        except (OSError, ValueError):
            start_position = None

    try:
        if hasattr(image_file, "seek"):
            image_file.seek(0)

        with Image.open(image_file) as source_image:
            normalized_image = ImageOps.exif_transpose(source_image)
            rgb_image = normalized_image.convert("RGB")

            resized_main_image = _resize_to_max_dimension(
                image=rgb_image,
                max_dimension=MAX_LIBRARY_PHOTO_DIMENSION,
            )
            thumbnail_image = _resize_to_max_width(
                image=rgb_image,
                max_width=LIBRARY_THUMBNAIL_MAX_WIDTH,
            )
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError("Could not process uploaded image.") from error
    finally:
        if start_position is not None and hasattr(image_file, "seek"):
            try:
                image_file.seek(start_position)
            except (OSError, ValueError):
                pass

    resized_main_bytes = _encode_jpeg(
        image=resized_main_image,
        quality=LIBRARY_PHOTO_JPEG_QUALITY,
    )
    thumbnail_bytes = _encode_jpeg(image=thumbnail_image, quality=LIBRARY_PHOTO_JPEG_QUALITY)

    base_name = _normalize_base_filename(original_name=original_name)
    unique_suffix = uuid4().hex[:12]
    main_filename = f"{base_name}-{unique_suffix}.jpg"
    thumbnail_filename = f"{base_name}-{unique_suffix}-thumb.jpg"

    return (
        (main_filename, ContentFile(resized_main_bytes)),
        (thumbnail_filename, ContentFile(thumbnail_bytes)),
    )


def ensure_instagram_aspect_ratio(*, library) -> None:
    """Crop the library photo in-place if its aspect ratio exceeds Instagram bounds.
    Saves the cropped version back to the photo field so Instagram can fetch it."""
    with library.photo.open("rb") as f:
        with Image.open(f) as img:
            width, height = img.size
            ratio = width / height
            if MIN_ASPECT_RATIO <= ratio <= MAX_ASPECT_RATIO:
                return

            rgb = img.convert("RGB")
            cropped = _crop_to_aspect_ratio_bounds(image=rgb)

    jpeg_bytes = _encode_jpeg(image=cropped, quality=LIBRARY_PHOTO_JPEG_QUALITY)
    photo_name = library.photo.name
    library.photo.save(photo_name, ContentFile(jpeg_bytes), save=True)
