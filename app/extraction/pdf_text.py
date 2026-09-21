"""
Step 0 of the pipeline: get text out of the PDF.

PyMuPDF is used for the text layer. If a PDF has no text layer (a scan or a
photo of a document) we fall back to OCR when pytesseract is installed,
otherwise we return an empty string and the completeness rules will flag the
document as unreadable rather than silently passing it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pymupdf


def extract_text(path: str | Path) -> Tuple[str, str]:
    """Returns (text, method) where method is 'text_layer' | 'ocr' | 'none'."""
    path = Path(path)
    doc = pymupdf.open(str(path))
    try:
        text = "\n".join(page.get_text("text") for page in doc)
        if text.strip():
            return text, "text_layer"
        ocr_text = _ocr(doc)
        return (ocr_text, "ocr") if ocr_text.strip() else ("", "none")
    finally:
        doc.close()


def _ocr(doc) -> str:
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return ""
    import io
    out = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        out.append(pytesseract.image_to_string(img))
    return "\n".join(out)