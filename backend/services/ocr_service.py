"""
OCR service — extract land-document fields using PaddleOCR PP-OCRv5.

Uses the Devanagari model so both English and Marathi/Hindi text
on 7/12 extracts, property cards, etc. are handled.
"""

import logging
import re
from typing import Any, Dict

import cv2
import numpy as np
from paddleocr import PaddleOCR

logger = logging.getLogger("terratrust.ocr")

# ---------------------------------------------------------------------------
# Initialise PaddleOCR lazily (expensive to start, may have version issues)
# ---------------------------------------------------------------------------
_ocr_engine = None


def _get_ocr_engine():
    """Lazy-initialise and return the PaddleOCR engine."""
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = PaddleOCR(
            text_recognition_model_name="devanagari_PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=True,
            use_doc_unwarping=True,
            use_textline_orientation=True,
            device="cpu",
        )
        logger.info("PaddleOCR engine initialised.")
    return _ocr_engine

# ---------------------------------------------------------------------------
# Regex patterns for field extraction (English + Marathi / Devanagari)
# ---------------------------------------------------------------------------
SURVEY_RE = re.compile(
    r"(?:Survey|Gat|Gut|सर्व्हे|गट)\s*[Nn]o\.?\s*[:\-]?\s*(\d+(?:/\d+)?)",
    re.IGNORECASE,
)

OWNER_RE = re.compile(
    r"(?:Name of Owner|Owner|मालकाचे नाव|धारकाचे नाव)\s*[:\-]?\s*"
    r"([A-Z][a-zA-Z\s]+|[\u0900-\u097F\s]+)",
    re.IGNORECASE,
)

VILLAGE_RE = re.compile(
    r"(?:Village|गाव|ग्राम)\s*[:\-]?\s*([A-Za-z\s]+|[\u0900-\u097F\s]+)",
    re.IGNORECASE,
)

TALUKA_RE = re.compile(
    r"(?:Taluka|तालुका|Tehsil|तहसील)\s*[:\-]?\s*([A-Za-z\s]+|[\u0900-\u097F\s]+)",
    re.IGNORECASE,
)

DISTRICT_RE = re.compile(
    r"(?:District|जिल्हा|Dist)\s*[:\-]?\s*([A-Za-z\s]+|[\u0900-\u097F\s]+)",
    re.IGNORECASE,
)


def preprocess_document_image(image_bytes: bytes) -> np.ndarray:
    """Pre-process a raw document image for better OCR accuracy.

    Steps
    -----
    1. Decode bytes → BGR image.
    2. Convert to greyscale.
    3. Adaptive thresholding for contrast normalisation.
    4. Non-local means denoising.

    Returns
    -------
    numpy.ndarray
        Cleaned greyscale image suitable for PaddleOCR.
    """
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode the uploaded image.")

    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(
        grey, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    denoised = cv2.fastNlMeansDenoising(thresh, h=10)
    return denoised


def extract_fields_from_document(image_bytes: bytes) -> Dict[str, Any]:
    """Run OCR on a land document and extract structured fields.

    Parameters
    ----------
    image_bytes : bytes
        Raw bytes of the uploaded document image.

    Returns
    -------
    dict
        Extracted fields: survey_number, owner_name, village,
        taluka, district, and extraction_confidence (0–1).

    Raises
    ------
    ValueError
        If the mandatory ``survey_number`` or ``owner_name`` cannot
        be found in the document.
    """
    processed = preprocess_document_image(image_bytes)

    # PaddleOCR expects a numpy array (BGR or greyscale)
    result = _get_ocr_engine().predict(processed)

    # Assemble all recognised text lines into a single string
    all_text_lines: list[str] = []
    if result and len(result) > 0:
        for item in result:
            if hasattr(item, "rec_texts"):
                all_text_lines.extend(item.rec_texts)
            elif isinstance(item, dict) and "rec_texts" in item:
                all_text_lines.extend(item["rec_texts"])
            elif isinstance(item, (list, tuple)):
                for sub in item:
                    if isinstance(sub, (list, tuple)) and len(sub) >= 2:
                        text = sub[1] if isinstance(sub[1], str) else str(sub[1][0]) if isinstance(sub[1], (list, tuple)) else str(sub[1])
                        all_text_lines.append(text)
                    elif isinstance(sub, str):
                        all_text_lines.append(sub)

    full_text = " ".join(all_text_lines)
    logger.debug("OCR full text: %s", full_text[:500])

    # --- Extract fields ------------------------------------------------
    fields: Dict[str, Any] = {
        "survey_number": None,
        "owner_name": None,
        "village": None,
        "taluka": None,
        "district": None,
    }

    m = SURVEY_RE.search(full_text)
    if m:
        fields["survey_number"] = m.group(1).strip()

    m = OWNER_RE.search(full_text)
    if m:
        fields["owner_name"] = m.group(1).strip()

    m = VILLAGE_RE.search(full_text)
    if m:
        fields["village"] = m.group(1).strip()

    m = TALUKA_RE.search(full_text)
    if m:
        fields["taluka"] = m.group(1).strip()

    m = DISTRICT_RE.search(full_text)
    if m:
        fields["district"] = m.group(1).strip()

    # --- Confidence calculation ----------------------------------------
    filled = sum(1 for v in fields.values() if v)
    fields["extraction_confidence"] = round(filled / 5, 2)

    # --- Mandatory field check -----------------------------------------
    if not fields["survey_number"] or not fields["owner_name"]:
        raise ValueError(
            "Could not extract required fields from the document. "
            f"survey_number={'found' if fields['survey_number'] else 'MISSING'}, "
            f"owner_name={'found' if fields['owner_name'] else 'MISSING'}."
        )

    return fields
