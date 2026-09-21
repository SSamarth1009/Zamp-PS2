"""
Step 2: information extraction into the canonical schema.

Two interchangeable extractors:

  1. LLM extractor  - handles arbitrary phrasing and prose ("I hereby certify
     that ... is incorporated under ..."), maps any label vocabulary onto the
     canonical field names, and returns strict JSON.
  2. Rule extractor - a label-synonym map over "Label : Value" lines. It keeps
     the whole system runnable (and demo-able) with no API key, and doubles as
     a cross-check on the LLM output.

Both produce exactly the same canonical field names, so nothing downstream
knows or cares which one ran.
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from app import llm
from app.schemas import CanonicalDocument

CANONICAL_FIELDS = [
    "legal_name", "trade_name", "country", "registered_address", "state",
    "registration_number", "date_of_incorporation", "company_type", "pan",
    "gstin", "registration_status", "account_holder_name", "bank_name",
    "account_number", "ifsc", "industry", "license_number", "issue_date",
    "expiry_date", "issuing_authority", "license_status", "contact_person",
    "contact_email",
]

# Label vocabulary -> canonical field. Keys are normalised (lowercase, letters
# and spaces only). Longest key wins, so "registration number gstin" beats
# "registration number".
LABEL_MAP = {
    "legal entity name": "legal_name",
    "name of company": "legal_name",
    "legal name of business": "legal_name",
    "name as per records": "legal_name",
    "name company": "legal_name",
    "name of applicant": "legal_name",
    "licensee": "legal_name",
    "trade brand name": "trade_name",
    "trade name if any": "trade_name",
    "trade name": "trade_name",
    "country of registration": "country",
    "registered office address": "registered_address",
    "registered office": "registered_address",
    "principal place of business": "registered_address",
    "address": "registered_address",
    "state ut": "state",
    "state": "state",
    "corporate identity number": "registration_number",
    "corporate identity number cin": "registration_number",
    "cin": "registration_number",
    "company registration number": "registration_number",
    "date of incorporation": "date_of_incorporation",
    "class of company": "company_type",
    "constitution of business": "company_type",
    "status of assessee": "company_type",
    "permanent account number": "pan",
    "pan": "pan",
    "gst identification number": "gstin",
    "registration number gstin": "gstin",
    "gstin": "gstin",
    "status of registration": "registration_status",
    "type of registration": None,
    "account holder name": "account_holder_name",
    "bank account title": "account_holder_name",
    "ac title": "account_holder_name",
    "bank name": "bank_name",
    "bank": "bank_name",
    "account number": "account_number",
    "account no": "account_number",
    "ifsc code": "ifsc",
    "ifsc": "ifsc",
    "industry segment": "industry",
    "activity industry": "industry",
    "industry": "industry",
    "licence no": "license_number",
    "license no": "license_number",
    "licence number": "license_number",
    "license number": "license_number",
    "date of issue": "issue_date",
    "issue date": "issue_date",
    "valid upto": "expiry_date",
    "valid until": "expiry_date",
    "expiry date": "expiry_date",
    "issuing authority": "issuing_authority",
    "licence status": "license_status",
    "license status": "license_status",
    "authorised contact person": "contact_person",
    "contact person": "contact_person",
    "email": "contact_email",
}

SYSTEM = (
    "You are an information extraction engine for a vendor onboarding desk. "
    "Read the document and return STRICT JSON only - no prose, no markdown. "
    "Use exactly these keys (omit a key entirely if the document does not state it): "
    + ", ".join(CANONICAL_FIELDS) + ". "
    "Rules: copy values verbatim as printed, do not correct or complete them, "
    "do not infer a value from another document or from general knowledge, "
    "render dates as YYYY-MM-DD, and never guess. If an identifier looks malformed, "
    "still return it exactly as printed - a downstream rule engine validates formats."
)


def extract(text: str, document_type: str) -> Tuple[Dict[str, Optional[str]], str]:
    """Returns (fields, method) where method is 'llm' or 'rule'."""
    fields = llm.complete_json(
        SYSTEM,
        f"Document type (already classified): {document_type}\n\n"
        f"--- DOCUMENT TEXT ---\n{text[:8000]}",
        max_tokens=1200,
    )
    if isinstance(fields, dict):
        cleaned = {k: (str(v).strip() if v not in (None, "") else None)
                   for k, v in fields.items() if k in CANONICAL_FIELDS}
        if any(cleaned.values()):
            # Belt and braces: fill anything the model missed from the rule pass.
            for k, v in extract_rule_based(text).items():
                cleaned.setdefault(k, v)
                if cleaned.get(k) in (None, ""):
                    cleaned[k] = v
            return cleaned, "llm"
    return extract_rule_based(text), "rule"


def _norm_label(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", label.lower())).strip()


def extract_rule_based(text: str) -> Dict[str, Optional[str]]:
    fields: Dict[str, Optional[str]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        value = value.strip()
        if not value or value == "-":
            continue
        key = _norm_label(label)
        if not key:
            continue
        # longest matching label wins
        matches = [m for m in LABEL_MAP if m == key] or \
                  sorted([m for m in LABEL_MAP if key.endswith(m) or key.startswith(m)],
                         key=len, reverse=True)
        if not matches:
            continue
        field = LABEL_MAP[matches[0]]
        if field and not fields.get(field):
            fields[field] = value
    return fields


def build_document(source_file: str, declared_slot: str, text: str,
                   document_type: str, confidence: float,
                   classification_method: str) -> CanonicalDocument:
    fields, method = extract(text, document_type)
    doc = CanonicalDocument(
        source_file=source_file,
        declared_slot=declared_slot,
        document_type=document_type,
        classification_confidence=confidence,
        classification_method=classification_method,
        extraction_method=method,
        raw_text=text,
        **{k: v for k, v in fields.items() if k in CANONICAL_FIELDS},
    )
    return doc