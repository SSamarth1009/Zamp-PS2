"""
Central configuration for the Vendor Onboarding & Verification Engine.

Everything that a business/compliance owner would want to tune lives here:
required documents, required fields, name-matching thresholds and ID formats.
No AI logic reads this file - it is consumed only by the deterministic
validation layer.
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SUBMISSIONS_DIR = DATA_DIR / "submissions"
VENDOR_MASTER_PATH = DATA_DIR / "vendor_master.json"
SUBMISSIONS_INDEX_PATH = DATA_DIR / "vendor_submissions.json"
EXPECTED_RESULTS_PATH = DATA_DIR / "expected_results.json"
DB_PATH = Path(os.getenv("VOE_DB_PATH", DATA_DIR / "audit.db"))

# --------------------------------------------------------------------------
# LLM configuration (optional - the engine degrades gracefully without it)
# --------------------------------------------------------------------------
# Load .env explicitly so uvicorn, streamlit and pytest all see the same key.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:          # dotenv is optional; real env vars still work
    pass

LLM_PROVIDER = "openai"
LLM_ENABLED = os.getenv("VOE_LLM_ENABLED", "auto").lower()   # auto | on | off
LLM_MODEL = os.getenv("VOE_LLM_MODEL", "gpt-5.6-luna")
LLM_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY", "")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
LLM_TIMEOUT_S = int(os.getenv("VOE_LLM_TIMEOUT", "60"))
LLM_REASONING_EFFORT = os.getenv("VOE_LLM_REASONING_EFFORT", "low")

# --------------------------------------------------------------------------
# Document taxonomy
# --------------------------------------------------------------------------
DOCUMENT_TYPES = [
    "vendor_application",
    "incorporation_certificate",
    "gst_certificate",
    "pan_document",
    "bank_verification",
    "industry_license",
    "unknown",
]

REQUIRED_DOCUMENTS = [
    "vendor_application",
    "incorporation_certificate",
    "gst_certificate",
    "pan_document",
    "bank_verification",
    "industry_license",
]

DOCUMENT_LABELS = {
    "vendor_application": "Vendor Application Form",
    "incorporation_certificate": "Certificate of Incorporation",
    "gst_certificate": "GST Registration Certificate",
    "pan_document": "PAN / Tax ID Document",
    "bank_verification": "Bank Verification Letter",
    "industry_license": "Industry Licence",
    "unknown": "Unrecognised Document",
}

# Fields that MUST be extracted for each document type, otherwise the packet
# is incomplete and the vendor has to be contacted.
REQUIRED_FIELDS = {
    "vendor_application": ["legal_name", "country", "registered_address", "pan",
                           "gstin", "registration_number", "account_holder_name",
                           "ifsc", "industry", "contact_email"],
    "incorporation_certificate": ["legal_name", "registration_number",
                                  "date_of_incorporation", "registered_address"],
    "gst_certificate": ["gstin", "legal_name", "registered_address", "state",
                        "registration_status"],
    "pan_document": ["pan", "legal_name"],
    "bank_verification": ["account_holder_name", "bank_name", "account_number", "ifsc"],
    "industry_license": ["license_number", "legal_name", "industry",
                         "issue_date", "expiry_date", "issuing_authority"],
}

# --------------------------------------------------------------------------
# Name / address matching thresholds
# --------------------------------------------------------------------------
# >= NAME_REVIEW_THRESHOLD  -> close enough to be a typo -> MANUAL REVIEW (WARN)
# <  NAME_REVIEW_THRESHOLD  -> treated as a genuine mismatch (FAIL)
NAME_REVIEW_THRESHOLD = 0.88
ADDRESS_REVIEW_THRESHOLD = 0.85

# Bank policy is stricter: the account title must normalise to the legal name.
# Anything else is an exception for a human, never an auto-approval.
BANK_NAME_STRICT = True

# --------------------------------------------------------------------------
# Identifier formats (India)
# --------------------------------------------------------------------------
PAN_REGEX = r"^[A-Z]{5}[0-9]{4}[A-Z]$"
GSTIN_REGEX = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$"
IFSC_REGEX = r"^[A-Z]{4}0[A-Z0-9]{6}$"
CIN_REGEX = r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$"
LICENSE_REGEX = r"^[A-Z]{3}-LIC-[0-9]{4}-[0-9]{4}$"
ACCOUNT_NUMBER_REGEX = r"^[0-9]{9,18}$"

GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "19": "West Bengal",
    "21": "Odisha", "23": "Madhya Pradesh", "24": "Gujarat", "27": "Maharashtra",
    "29": "Karnataka", "32": "Kerala", "33": "Tamil Nadu", "36": "Telangana",
    "37": "Andhra Pradesh",
}

# --------------------------------------------------------------------------
# Decision policy
# --------------------------------------------------------------------------
# Severity of a failing check determines the outcome. This mapping is the
# single place where "how bad is it" is decided.
SEVERITY_CRITICAL = "CRITICAL"   # -> REJECTED
SEVERITY_MAJOR = "MAJOR"         # -> PENDING (vendor action required)
SEVERITY_MINOR = "MINOR"         # -> PENDING (manual review) if WARN

DECISION_PRECEDENCE = ["REJECTED", "PENDING", "APPROVED"]