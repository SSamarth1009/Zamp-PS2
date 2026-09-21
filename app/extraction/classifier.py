"""
Step 1: document classification.

What the file IS is decided from its content, never from its filename or from
the slot the vendor uploaded it into. That is precisely how "vendor attached
the wrong document" gets caught (see check DOC-002).
"""
from __future__ import annotations

from typing import Tuple

from app import llm
from app.config import DOCUMENT_TYPES

# Distinctive phrases per document type, used by the offline classifier.
MARKERS = {
    "vendor_application": [
        ("vendor registration application", 0.97), ("supplier registration form", 0.9),
        ("form vp-01", 0.9), ("bank account title", 0.6), ("industry segment", 0.5),
    ],
    "incorporation_certificate": [
        ("certificate of incorporation", 0.97), ("registrar of companies", 0.85),
        ("class of company", 0.6), ("incorporated under the", 0.6),
    ],
    "gst_certificate": [
        ("goods and services tax", 0.95), ("gst reg-06", 0.95),
        ("legal name of business", 0.8), ("principal place of business", 0.7),
    ],
    "pan_document": [
        ("permanent account number", 0.93), ("income tax department", 0.85),
        ("status of assessee", 0.7),
    ],
    "bank_verification": [
        ("bank account verification", 0.97), ("cancelled cheque", 0.9),
        ("account holder name", 0.7), ("branch manager", 0.5),
    ],
    "industry_license": [
        ("industry operating licence", 0.97), ("operating license", 0.9),
        ("valid upto", 0.7), ("licensee", 0.6), ("licence no", 0.6),
    ],
}

SYSTEM = (
    "You classify scanned business documents for a procurement onboarding desk. "
    "Reply with JSON only: {\"document_type\": <one of "
    f"{DOCUMENT_TYPES}>, \"confidence\": <0-1 float>, \"reason\": <short string>}}. "
    "Judge only from the content. If the document does not clearly belong to one of "
    "the listed types, return \"unknown\"."
)


def classify(text: str, filename: str = "") -> Tuple[str, float, str]:
    """Returns (document_type, confidence, method)."""
    if not text.strip():
        return "unknown", 0.0, "heuristic"

    result = llm.complete_json(
        SYSTEM,
        f"Filename (untrusted, may be wrong): {filename}\n\n"
        f"--- DOCUMENT TEXT ---\n{text[:6000]}",
        max_tokens=300,
    )
    if result and result.get("document_type") in DOCUMENT_TYPES:
        return (result["document_type"],
                float(result.get("confidence", 0.8)), "llm")

    return (*classify_heuristic(text), "heuristic")


def classify_heuristic(text: str) -> Tuple[str, float]:
    low = text.lower()
    best_type, best_score = "unknown", 0.0
    for doc_type, markers in MARKERS.items():
        score = max((w for phrase, w in markers if phrase in low), default=0.0)
        hits = sum(1 for phrase, _ in markers if phrase in low)
        score = min(1.0, score + 0.02 * max(0, hits - 1))
        if score > best_score:
            best_type, best_score = doc_type, score
    return (best_type, round(best_score, 2)) if best_score >= 0.5 else ("unknown", round(best_score, 2))