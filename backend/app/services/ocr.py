"""
OCR service for Israeli payslips.
Phase 2C: Tesseract-based OCR for scanned PDFs and image files.

All functions are synchronous; callers must wrap with asyncio.to_thread().
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def check_ocr_deps() -> tuple[bool, list[str]]:
    """
    Check that Tesseract with Hebrew language data and poppler (pdftoppm) are installed.

    Returns (available, missing_list).
      - available=True only when BOTH checks pass.
      - missing_list is empty when available=True; contains human-readable descriptions otherwise.

    Uses subprocess so the check works regardless of whether pytesseract is imported yet.
    Does NOT check the pytesseract Python package (assumed present if requirements.txt installed).
    """
    missing: list[str] = []

    # Check 1: tesseract with heb language data
    try:
        result = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        combined = result.stdout + result.stderr  # tesseract prints to stderr on some versions
        if "heb" not in combined.lower().split():
            missing.append("tesseract heb traineddata (run: brew install tesseract-lang)")
    except FileNotFoundError:
        missing.append("tesseract binary (run: brew install tesseract)")
    except subprocess.TimeoutExpired:
        missing.append("tesseract (timed out)")

    # Check 2: pdftoppm (poppler)
    try:
        result = subprocess.run(
            ["pdftoppm", "-v"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 and result.returncode != 99:
            # pdftoppm -v exits 99 on some versions when showing help — still means it's there
            missing.append("pdftoppm (run: brew install poppler)")
    except FileNotFoundError:
        missing.append("pdftoppm / poppler (run: brew install poppler)")
    except subprocess.TimeoutExpired:
        missing.append("pdftoppm (timed out)")

    available = len(missing) == 0
    return (available, missing)


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _convert_heic_to_pil(file_path: Path):
    """
    Convert a HEIC/HEIF file to a PIL Image.
    Uses pillow-heif's register_heif_opener() (idempotent).
    """
    from pillow_heif import register_heif_opener
    register_heif_opener()
    from PIL import Image
    return Image.open(str(file_path))


def _preprocess_image(img):
    """
    Prepare a PIL Image for OCR:
      1. EXIF auto-rotate (critical for smartphone portrait photos)
      2. Convert to grayscale
      3. Adaptive threshold (OpenCV if available, else simple PIL threshold)
    Returns a processed PIL Image in mode "L" (grayscale).
    """
    from PIL import ImageOps

    # Step 1: EXIF rotate
    img = ImageOps.exif_transpose(img)

    # Step 2: Grayscale
    img = img.convert("L")

    # Step 3: Threshold for cleaner OCR
    try:
        import cv2
        import numpy as np
        arr = np.array(img)
        # Adaptive Gaussian threshold: block_size=11, C=2
        thresholded = cv2.adaptiveThreshold(
            arr, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11, 2
        )
        from PIL import Image
        img = Image.fromarray(thresholded)
    except ImportError:
        # Fall back to simple point threshold if OpenCV not available
        img = img.point(lambda x: 0 if x < 128 else 255)

    return img


def _pdf_to_images(file_path: Path, max_pages: int = 3) -> list:
    """
    Rasterize a PDF to a list of PIL Images using pdf2image (wraps pdftoppm).
    Returns up to max_pages pages at 200 DPI.
    """
    from pdf2image import convert_from_path
    return convert_from_path(
        str(file_path),
        dpi=200,
        first_page=1,
        last_page=max_pages,
    )


# ---------------------------------------------------------------------------
# Main OCR entry point
# ---------------------------------------------------------------------------

def ocr_file(file_path: Path, mime_type: str) -> dict[int, str]:
    """
    Run Tesseract OCR on a file and return {page_index: text}.

    Routing by MIME type:
      - application/pdf  → rasterize with pdftoppm → OCR each page (up to 3)
      - image/heic / image/heif → convert with pillow-heif → OCR
      - image/* → open with PIL → OCR

    Uses lang="heb+eng" and --psm 6 (uniform block) for best Hebrew results.
    Does NOT log raw OCR text (privacy). Only char counts are logged.

    Returns {} on unrecoverable errors (caller treats as no text layer).
    """
    import pytesseract

    pages_text: dict[int, str] = {}

    try:
        mime_lower = (mime_type or "").lower()

        # Collect PIL images per page
        images: list = []

        if mime_lower == "application/pdf":
            images = _pdf_to_images(file_path)
        elif mime_lower in ("image/heic", "image/heif"):
            images = [_convert_heic_to_pil(file_path)]
        else:
            # JPEG, PNG, WebP, etc.
            from PIL import Image
            images = [Image.open(str(file_path))]

        for page_idx, img in enumerate(images):
            processed = _preprocess_image(img)
            text: str = pytesseract.image_to_string(
                processed,
                lang="heb+eng",
                config="--psm 6",
            )
            pages_text[page_idx] = text
            logger.info(
                "OCR page %d: %d chars extracted",
                page_idx,
                len(text.strip()),
            )

    except Exception as exc:
        logger.warning("ocr_file failed for %s: %s", file_path, exc)
        return {}

    return pages_text
