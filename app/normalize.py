"""
Deterministic normalisation utilities.

Normalisation happens BEFORE comparison so that "ABC Technologies Private
Limited" and "ABC TECHNOLOGIES PVT. LTD." are not reported as a mismatch.
Only after normalisation do we fall back to fuzzy similarity, and fuzzy
similarity never auto-approves or auto-rejects on its own - it only decides
between "mismatch" and "needs a human".
"""
from __future__ import annotations

import re
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Company-name normalisation
# ---------------------------------------------------------------------------
_SUFFIX_MAP = {
    "PVT": "PRIVATE", "PVT.": "PRIVATE", "PRIVATE": "PRIVATE",
    "LTD": "LIMITED", "LTD.": "LIMITED", "LIMITED": "LIMITED",
    "LLP": "LLP", "PLC": "PLC",
    "CO": "COMPANY", "CO.": "COMPANY", "CORP": "CORPORATION",
    "INC": "INCORPORATED", "&": "AND",
}
_NOISE_TOKENS = {"THE", "M/S", "MS"}


def normalize_company_name(value: Optional[str]) -> str:
    if not value:
        return ""
    text = value.upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    tokens = [t for t in text.split() if t and t not in _NOISE_TOKENS]
    tokens = [_SUFFIX_MAP.get(t, t) for t in tokens]
    return " ".join(tokens).strip()


_ADDRESS_MAP = {
    "RD": "ROAD", "ST": "STREET", "BLDG": "BUILDING", "FLR": "FLOOR",
    "NO": "NUMBER", "PLT": "PLOT", "IND": "INDUSTRIAL", "ESTT": "ESTATE",
    "BLR": "BENGALURU", "BANGALORE": "BENGALURU", "MUM": "MUMBAI",
    "PH": "PHASE", "OPP": "OPPOSITE", "NR": "NEAR",
}


def normalize_address(value: Optional[str]) -> str:
    if not value:
        return ""
    text = value.upper()
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    tokens = [_ADDRESS_MAP.get(t, t) for t in text.split() if t]
    return " ".join(tokens).strip()


def normalize_id(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"[\s\-_.]+", "", value.upper())


def normalize_industry(value: Optional[str]) -> str:
    if not value:
        return ""
    text = value.upper()
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    stop = {"AND", "SERVICES", "SERVICE", "SOLUTIONS", "SECTOR", "SEGMENT"}
    return " ".join(t for t in text.split() if t not in stop).strip()


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------
def similarity(a: str, b: str) -> float:
    """Token-sorted similarity ratio in [0, 1]; word-order insensitive."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    a_sorted = " ".join(sorted(a.split()))
    b_sorted = " ".join(sorted(b.split()))
    return max(
        SequenceMatcher(None, a, b).ratio(),
        SequenceMatcher(None, a_sorted, b_sorted).ratio(),
    )


def compare_names(a: Optional[str], b: Optional[str],
                  review_threshold: float = 0.88) -> Tuple[str, float]:
    """
    Returns (verdict, score) where verdict is EXACT | REVIEW | MISMATCH.

    EXACT    - identical after normalisation, safe to pass automatically
    REVIEW   - very close (likely a typo / truncation) -> human must confirm
    MISMATCH - materially different -> deterministic failure
    """
    na, nb = normalize_company_name(a), normalize_company_name(b)
    if not na or not nb:
        return "MISSING", 0.0
    if na == nb:
        return "EXACT", 1.0
    score = similarity(na, nb)
    return ("REVIEW" if score >= review_threshold else "MISMATCH"), round(score, 4)


def compare_addresses(a: Optional[str], b: Optional[str],
                      review_threshold: float = 0.85) -> Tuple[str, float]:
    na, nb = normalize_address(a), normalize_address(b)
    if not na or not nb:
        return "MISSING", 0.0
    if na == nb:
        return "EXACT", 1.0
    score = similarity(na, nb)
    return ("REVIEW" if score >= review_threshold else "MISMATCH"), round(score, 4)


# ---------------------------------------------------------------------------
# GSTIN checksum (official algorithm, base-36)
# ---------------------------------------------------------------------------
_GST_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_digit(first_14: str) -> str:
    total = 0
    for i, ch in enumerate(first_14.upper()):
        if ch not in _GST_ALPHABET:
            return "?"
        factor = 2 if (i % 2) else 1
        product = _GST_ALPHABET.index(ch) * factor
        total += product // 36 + product % 36
    return _GST_ALPHABET[(36 - total % 36) % 36]


def gstin_checksum_valid(gstin: Optional[str]) -> bool:
    g = normalize_id(gstin)
    if len(g) != 15:
        return False
    return gstin_check_digit(g[:14]) == g[14]


def pan_from_gstin(gstin: Optional[str]) -> str:
    g = normalize_id(gstin)
    return g[2:12] if len(g) == 15 else ""


def state_code_from_gstin(gstin: Optional[str]) -> str:
    g = normalize_id(gstin)
    return g[:2] if len(g) >= 2 else ""


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
_DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y",
                 "%b %d, %Y", "%B %d, %Y", "%d.%m.%Y"]


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def is_expired(value: Optional[str], as_of: Optional[date] = None) -> Optional[bool]:
    parsed = parse_date(value)
    if parsed is None:
        return None
    return parsed < (as_of or date.today())