"""
OCR service for Israeli payslips.
Phase 2C: Tesseract-based OCR for scanned PDFs and image files.
Phase 12: Spatial table reconstruction — uses image_to_data bounding boxes
          to reassemble RTL Hebrew rows before passing text to the parser.

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
# Spatial reconstruction constants
# ---------------------------------------------------------------------------

# Words whose top-coordinate differs by ≤ this many pixels (on the 2× upscaled
# image) are grouped into the same logical row.  At 400 DPI equivalent, a typical
# Hebrew character is ~30-40 px tall, so 20 px comfortably groups characters on
# the same baseline without merging adjacent rows.
_ROW_GROUP_TOLERANCE_PX = 20

# Minimum Tesseract word confidence (0-100) to include a word. Anything below
# this threshold is likely noise from redaction halos or scanner artifacts.
_MIN_WORD_CONFIDENCE = 30


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
      2. Upscale 2× using LANCZOS — dramatically improves Tesseract on small payslip fonts
      3. Convert to grayscale
      4. Adaptive threshold with block_size scaled to 2× image (31px, not 11px)
         — 11px was correct for original size but becomes too coarse after upscaling.
         — Also uses Otsu's method as a second pass to neutralise heavy marker redactions.

    Returns a processed PIL Image in mode "L" (grayscale).
    """
    from PIL import ImageOps, Image as _PILImage

    # Step 1: EXIF rotate
    img = ImageOps.exif_transpose(img)

    # Step 2: Upscale 2× — Tesseract works best at ≥300 DPI equivalent.
    # 200 DPI (pdf2image default) × 2 = 400 DPI equivalent.
    # LANCZOS is the highest-quality PIL resampling filter for text images.
    w, h = img.size
    img = img.resize((w * 2, h * 2), _PILImage.LANCZOS)

    # Step 3: Grayscale
    img = img.convert("L")

    # Step 4: Binarisation — two-pass strategy for redacted documents
    # Pass A: Adaptive Gaussian threshold with block_size=31 (scaled for 2× image;
    #         original block_size=11 at 1× ≈ 31 at 2× keeping the same physical size).
    # Pass B: Otsu global threshold blended in — neutralises large solid-black
    #         redaction rectangles that confuse adaptive methods.
    try:
        import cv2
        import numpy as np
        arr = np.array(img)

        # Pass A: adaptive threshold (handles local lighting variation)
        adaptive = cv2.adaptiveThreshold(
            arr, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31, 10  # block_size=31 (was 11 pre-upscale), C=10
        )

        # Pass B: Otsu global threshold (turns heavy black rectangles to pure black,
        # leaving text regions as white-on-black cleanly)
        _, otsu = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Combine: take the lighter pixel from each pass so text (dark) is preserved
        # and noisy halos from redactions are suppressed
        combined = cv2.max(adaptive, otsu)

        from PIL import Image
        img = Image.fromarray(combined)
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
# Phase 12: Spatial line reconstruction
# ---------------------------------------------------------------------------

def _spatial_reconstruct_lines(img, lang: str = "heb+eng") -> str:
    """
    Run Tesseract with image_to_data to obtain per-word bounding boxes, then
    reconstruct spatially correct text lines from those coordinates.

    This function:
      1) Calls image_to_data to get (word, left, top, confidence) for each token.
      2) Filters out words with confidence < _MIN_WORD_CONFIDENCE.
      3) Groups words into rows if their `top` differs by ≤ _ROW_GROUP_TOLERANCE_PX.
      4) Sorts rows top-to-bottom.
      5) Within each row, sorts words RIGHT-to-LEFT (descending `left`) for Hebrew.
      6) Joins words within row by space, rows by newline.

    Returns reconstructed text; falls back to image_to_string on any exception.
    """
    import pytesseract
    from pytesseract import Output

    try:
        data = pytesseract.image_to_data(
            img,
            lang=lang,
            config="--psm 11",
            output_type=Output.DICT,
        )

        n = len(data["text"])
        words: list[tuple[int, int, str]] = []  # (top, left, word)

        for i in range(n):
            word = (data["text"][i] or "").strip()
            if not word:
                continue
            try:
                conf = int(data["conf"][i])
            except (ValueError, TypeError):
                conf = -1
            if conf < _MIN_WORD_CONFIDENCE:
                continue
            top = int(data["top"][i])
            left = int(data["left"][i])
            words.append((top, left, word))

        if not words:
            raise ValueError("no words survived confidence filter")

        # Group into rows by top coordinate
        words.sort(key=lambda w: w[0])  # by top
        rows: list[list[tuple[int, int, str]]] = []
        current_row: list[tuple[int, int, str]] = []
        row_anchor_top: int = -9999

        for top, left, word in words:
            if not current_row or abs(top - row_anchor_top) <= _ROW_GROUP_TOLERANCE_PX:
                current_row.append((top, left, word))
                row_anchor_top = sum(w[0] for w in current_row) // len(current_row)
            else:
                rows.append(current_row)
                current_row = [(top, left, word)]
                row_anchor_top = top

        if current_row:
            rows.append(current_row)

        # Build output text: RTL sort inside each row (desc left)
        output_lines: list[str] = []
        for row in rows:
            row_sorted = sorted(row, key=lambda w: w[1], reverse=True)
            output_lines.append(" ".join(w[2] for w in row_sorted))

        return "\n".join(output_lines)

    except Exception as exc:
        logger.warning(
            "_spatial_reconstruct_lines failed (%s), falling back to image_to_string", exc
        )
        return pytesseract.image_to_string(img, lang=lang, config="--psm 11")


# ---------------------------------------------------------------------------
# Main OCR entry point
# ---------------------------------------------------------------------------

def ocr_file(file_path: Path, mime_type: str) -> dict[int, str]:
    """
    Run Tesseract OCR on a file and return {page_index: text}.

    - application/pdf  → rasterize → OCR each page (up to 3)
    - image/heic/heif   → pillow-heif → OCR
    - image/*           → PIL open → OCR

    Uses spatial reconstruction to keep Hebrew RTL rows consistent for parsing.

    Returns {} on unrecoverable errors.
    """
    pages_text: dict[int, str] = {}

    try:
        mime_lower = (mime_type or "").lower()

        images: list = []
        if mime_lower == "application/pdf":
            images = _pdf_to_images(file_path)
        elif mime_lower in ("image/heic", "image/heif"):
            images = [_convert_heic_to_pil(file_path)]
        else:
            from PIL import Image
            images = [Image.open(str(file_path))]

        for page_idx, img in enumerate(images):
            processed = _preprocess_image(img)
            text: str = _spatial_reconstruct_lines(processed, lang="heb+eng")
            pages_text[page_idx] = text
            logger.info(
                "OCR page %d: %d chars extracted (spatial reconstruction)",
                page_idx,
                len(text.strip()),
            )

    except Exception as exc:
        logger.warning("ocr_file failed for %s: %s", file_path, exc)
        return {}

    return pages_text