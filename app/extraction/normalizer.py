"""
Step 3: normalisation.

Deterministic clean-up applied to every extracted document before any rule
runs. This is what makes downstream comparisons honest: a rule should fail
because the data really disagrees, not because one document printed
"HDFC0001234" and another printed "hdfc 0001234".

Normalisation NEVER repairs a value. "ABC123" stays "ABC123" so that the PAN
format rule can fail on it.
"""
from __future__ import annotations

import re
from typing import Optional

from app.normalize import normalize_id, parse_date
from app.schemas import CanonicalDocument

ID_FIELDS = ["pan", "gstin", "ifsc", "registration_number", "license_number", "account_number"]
DATE_FIELDS = ["date_of_incorporation", "issue_date", "expiry_date"]
TEXT_FIELDS = ["legal_name", "trade_name", "country", "registered_address", "state",
               "company_type", "registration_status", "account_holder_name", "bank_name",
               "industry", "issuing_authority", "license_status", "contact_person"]


def _squash(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip(" .,;")
    return cleaned or None


def normalize_document(doc: CanonicalDocument) -> CanonicalDocument:
    for field in TEXT_FIELDS:
        setattr(doc, field, _squash(getattr(doc, field)))

    for field in ID_FIELDS:
        raw = getattr(doc, field)
        if raw:
            value = normalize_id(raw)
            if field == "license_number":
                value = re.sub(r"[^A-Z0-9-]", "", str(raw).upper())
            setattr(doc, field, value)

    for field in DATE_FIELDS:
        raw = getattr(doc, field)
        if raw:
            parsed = parse_date(_squash(raw))
            if parsed:
                setattr(doc, field, parsed.isoformat())
            else:
                doc.warnings.append(f"Unparseable date in '{field}': {raw}")

    if doc.contact_email:
        doc.contact_email = doc.contact_email.strip().lower()
    return doc