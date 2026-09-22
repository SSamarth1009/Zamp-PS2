"""
Document generator.

vendor_master.json  ->  per-vendor document payloads  ->  defect injection  ->  PDFs

Generating every PDF from ONE canonical source guarantees that a "clean"
vendor is genuinely internally consistent, so any inconsistency the engine
reports later is an inconsistency we deliberately injected - never an
accident of hand-authoring.

Each document type is rendered with a DIFFERENT label vocabulary and layout
(e.g. "Legal Entity Name" vs "Name of Company" vs "Legal Name of Business"
vs "Account Holder Name"). That is what the extraction layer has to map into
the single canonical schema.

Every page is stamped: DEMO / SYNTHETIC DOCUMENT - NOT VALID FOR OFFICIAL USE.
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import (EXPECTED_RESULTS_PATH, SUBMISSIONS_DIR,  # noqa: E402
                        SUBMISSIONS_INDEX_PATH, VENDOR_MASTER_PATH)

DISCLAIMER = "DEMO / SYNTHETIC DOCUMENT - NOT VALID FOR OFFICIAL USE"
W, H = A4

# ---------------------------------------------------------------------------
# Defect injection plan. The master registry stays correct; only the submitted
# documents are corrupted.
# ---------------------------------------------------------------------------
DEFECTS = {
    "V001": {"description": "Clean submission, fully consistent."},
    "V002": {
        "description": "Industry licence not attached.",
        "omit": ["industry_license"],
    },
    "V003": {
        "description": "Bank account title does not match the legal entity.",
        "overrides": {"bank_verification": {"account_holder_name":
                                            "Sierra Logistics Services Pvt Ltd"}},
    },
    "V004": {
        "description": "PAN quoted in a malformed format.",
        "overrides": {"pan_document": {"pan": "ABC123"},
                      "vendor_application": {"pan": "ABC123"}},
    },
    "V005": {
        "description": "One document carries a singular/plural variant of the legal name.",
        "overrides": {"incorporation_certificate": {"legal_name": "Kaveri Textile Pvt Ltd"}},
    },
    "V006": {
        "description": "Multiple failures: GST certificate missing, malformed PAN, "
                       "bank title mismatch, expired licence.",
        "omit": ["gst_certificate"],
        "overrides": {
            "pan_document": {"pan": "AAOCO67"},
            "vendor_application": {"pan": "AAOCO67"},
            "bank_verification": {"account_holder_name": "Orion Pharmaceuticals Pvt Ltd"},
        },
    },
    "V007": {
        "description": "Wrong file attached: the GST certificate was uploaded in the "
                       "bank-verification slot.",
        "substitute": {"bank_verification": "gst_certificate"},
    },
}

EXPECTED = {
    "V001": ("APPROVED", []),
    "V002": ("PENDING", ["DOC-001"]),
    "V003": ("PENDING", ["BNK-001"]),
    "V004": ("REJECTED", ["TAX-001"]),
    "V005": ("PENDING", ["NAM-001"]),
    "V006": ("REJECTED", ["TAX-001", "DOC-001", "BNK-001", "LIC-003"]),
    "V007": ("PENDING", ["DOC-001", "DOC-002"]),
}


# ---------------------------------------------------------------------------
# Canonical payload per document type
# ---------------------------------------------------------------------------
def build_payloads(v: dict) -> dict:
    return {
        "vendor_application": {
            "legal_name": v["legal_name"], "trade_name": v["trade_name"],
            "country": v["country"], "registered_address": v["registered_address"],
            "registration_number": v["registration_number"], "pan": v["pan"],
            "gstin": v["gstin"], "bank_account_name": v["bank_account_name"],
            "bank_name": v["bank_name"], "ifsc": v["ifsc"], "industry": v["industry"],
            "contact_person": v["contact_person"], "contact_email": v["contact_email"],
        },
        "incorporation_certificate": {
            "legal_name": v["legal_name"], "registration_number": v["registration_number"],
            "date_of_incorporation": v["date_of_incorporation"],
            "registered_address": v["registered_address"], "company_type": v["company_type"],
        },
        "gst_certificate": {
            "gstin": v["gstin"], "legal_name": v["legal_name"],
            "trade_name": v["trade_name"], "registered_address": v["registered_address"],
            "state": v["state"], "registration_status": "Active",
        },
        "pan_document": {"pan": v["pan"], "legal_name": v["legal_name"],
                         "date_of_incorporation": v["date_of_incorporation"]},
        "bank_verification": {
            "account_holder_name": v["bank_account_name"], "bank_name": v["bank_name"],
            "account_number": v["account_number"], "ifsc": v["ifsc"],
        },
        "industry_license": {
            "license_number": v["license_number"], "legal_name": v["legal_name"],
            "industry": v["industry"], "issue_date": v["license_issue_date"],
            "expiry_date": v["license_expiry"], "issuing_authority": v["issuing_authority"],
            "license_status": "Issued",
        },
    }


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def _chrome(c: canvas.Canvas, authority: str, title: str, subtitle: str = "") -> float:
    c.setFillColor(HexColor("#123a63"))
    c.rect(0, H - 70, W, 70, stroke=0, fill=1)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont("Helvetica-Bold", 13)
    c.drawString(45, H - 40, authority)
    c.setFont("Helvetica", 8.5)
    c.drawString(45, H - 56, DISCLAIMER)

    c.setFillColor(HexColor("#000000"))
    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(W / 2, H - 110, title)
    y = H - 132
    if subtitle:
        c.setFont("Helvetica-Oblique", 9.5)
        c.drawCentredString(W / 2, y, subtitle)
        y -= 20
    c.setStrokeColor(HexColor("#123a63"))
    c.line(45, y, W - 45, y)

    c.saveState()
    c.setFillColor(HexColor("#dfe6ee"))
    c.setFont("Helvetica-Bold", 44)
    c.translate(W / 2, H / 2)
    c.rotate(32)
    c.drawCentredString(0, 0, "DEMO / SYNTHETIC")
    c.restoreState()
    return y - 28


def _rows(c: canvas.Canvas, y: float, rows: list[tuple[str, str]],
          label_w: int = 34, font: str = "Helvetica", size: float = 10.5) -> float:
    for label, value in rows:
        if value in (None, ""):
            value = "-"
        c.setFont(font, size)
        c.setFillColor(HexColor("#000000"))
        c.drawString(55, y, f"{label.ljust(label_w)} : {value}")
        y -= 19
    return y


def _prose(c: canvas.Canvas, y: float, lines: list[str], size: float = 9.5) -> float:
    c.setFont("Helvetica", size)
    for line in lines:
        c.drawString(55, y, line)
        y -= 14
    return y - 8


def _footer(c: canvas.Canvas, note: str) -> None:
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(HexColor("#6b7280"))
    c.drawString(45, 48, note)
    c.drawString(45, 36, DISCLAIMER)


# ---------------------------------------------------------------------------
# One renderer per document type - deliberately different vocabularies
# ---------------------------------------------------------------------------
def render_vendor_application(c, d):
    y = _chrome(c, "GLOBAL PROCUREMENT SHARED SERVICES", "VENDOR REGISTRATION APPLICATION FORM",
                "Form VP-01 | To be completed by the supplier")
    y = _prose(c, y, ["Section A - Entity particulars"])
    y = _rows(c, y, [
        ("Legal Entity Name", d.get("legal_name")),
        ("Trade / Brand Name", d.get("trade_name")),
        ("Country of Registration", d.get("country")),
        ("Registered Office Address", d.get("registered_address")),
        ("Corporate Identity Number", d.get("registration_number")),
        ("Industry Segment", d.get("industry")),
    ])
    y = _prose(c, y - 6, ["Section B - Tax particulars"])
    y = _rows(c, y, [
        ("Permanent Account Number", d.get("pan")),
        ("GST Identification Number", d.get("gstin")),
    ])
    y = _prose(c, y - 6, ["Section C - Remittance particulars"])
    y = _rows(c, y, [
        ("Bank Account Title", d.get("bank_account_name")),
        ("Bank", d.get("bank_name")),
        ("IFSC Code", d.get("ifsc")),
    ])
    y = _prose(c, y - 6, ["Section D - Contact"])
    y = _rows(c, y, [
        ("Authorised Contact Person", d.get("contact_person")),
        ("Email", d.get("contact_email")),
    ])
    _prose(c, y - 10, ["I confirm the particulars furnished above are true to the best of my knowledge.",
                       "Signature: ____________________     Date: ____________________"])
    _footer(c, "Supplier self-declaration")


def render_incorporation(c, d):
    y = _chrome(c, "OFFICE OF THE REGISTRAR OF COMPANIES (SIMULATED)",
                "CERTIFICATE OF INCORPORATION",
                "Issued under the Companies Act, 2013 - simulated instrument")
    y = _prose(c, y, [
        "I hereby certify that the company named below is incorporated under the",
        "Companies Act, 2013 and that the company is limited by shares.",
    ])
    y = _rows(c, y - 4, [
        ("Name of Company", d.get("legal_name")),
        ("Corporate Identity Number", d.get("registration_number")),
        ("Date of Incorporation", d.get("date_of_incorporation")),
        ("Class of Company", d.get("company_type")),
        ("Registered Office", d.get("registered_address")),
    ])
    _prose(c, y - 14, ["Given under my hand at the office of the Registrar.",
                       "Digital Signature Certificate affixed (simulated)."])
    _footer(c, "Registry instrument - synthetic")


def render_gst(c, d):
    y = _chrome(c, "GOODS AND SERVICES TAX - SIMULATED PORTAL",
                "CERTIFICATE OF REGISTRATION", "Form GST REG-06 (simulated)")
    y = _rows(c, y, [
        ("Registration Number (GSTIN)", d.get("gstin")),
        ("Legal Name of Business", d.get("legal_name")),
        ("Trade Name, if any", d.get("trade_name")),
        ("Constitution of Business", "Private Limited Company"),
        ("Principal Place of Business", d.get("registered_address")),
        ("State / UT", d.get("state")),
        ("Status of Registration", d.get("registration_status")),
        ("Type of Registration", "Regular"),
    ], label_w=30)
    _prose(c, y - 10, ["This is a system generated certificate and does not require signature.",
                       "Issued for demonstration purposes only."])
    _footer(c, "Tax registration certificate - synthetic")


def render_pan(c, d):
    y = _chrome(c, "INCOME TAX DEPARTMENT - SIMULATED", "PERMANENT ACCOUNT NUMBER (PAN)",
                "Allotment intimation for a non-individual assessee")
    y = _rows(c, y, [
        ("Permanent Account Number", d.get("pan")),
        ("Name (as per records)", d.get("legal_name")),
        ("Status of Assessee", "Company"),
        ("Date of Incorporation", d.get("date_of_incorporation")),
    ], label_w=28, font="Helvetica-Bold", size=11)
    _prose(c, y - 12, ["PAN once allotted is permanent and quoting it is mandatory on all",
                       "prescribed documents and returns."])
    _footer(c, "Tax identity document - synthetic")


def render_bank(c, d):
    y = _chrome(c, d.get("bank_name", "BANK") + "  |  BRANCH OPERATIONS",
                "BANK ACCOUNT VERIFICATION LETTER",
                "In lieu of cancelled cheque - issued on branch letterhead")
    y = _prose(c, y, ["To whomsoever it may concern,",
                      "We confirm that the following current account is maintained with our branch:"])
    y = _rows(c, y - 4, [
        ("Account Holder Name", d.get("account_holder_name")),
        ("Bank Name", d.get("bank_name")),
        ("Account Number", d.get("account_number")),
        ("IFSC", d.get("ifsc")),
        ("Account Type", "Current Account"),
    ], label_w=22)
    _prose(c, y - 12, ["The account is active and in good standing as on the date of this letter.",
                       "Authorised Signatory / Branch Manager"])
    _footer(c, "Banking instrument - synthetic")


def render_license(c, d):
    y = _chrome(c, (d.get("issuing_authority") or "LICENSING AUTHORITY").upper(),
                "INDUSTRY OPERATING LICENCE", "Licence to carry on the specified activity")
    y = _rows(c, y, [
        ("Licence No.", d.get("license_number")),
        ("Licensee", d.get("legal_name")),
        ("Activity / Industry", d.get("industry")),
        ("Date of Issue", d.get("issue_date")),
        ("Valid Upto", d.get("expiry_date")),
        ("Issuing Authority", d.get("issuing_authority")),
        ("Licence Status", d.get("license_status")),
    ], label_w=22)
    _prose(c, y - 12, ["The licensee shall comply with all conditions endorsed on this licence.",
                       "Renewal must be applied for before the validity date shown above."])
    _footer(c, "Compliance licence - synthetic")


RENDERERS = {
    "vendor_application": render_vendor_application,
    "incorporation_certificate": render_incorporation,
    "gst_certificate": render_gst,
    "pan_document": render_pan,
    "bank_verification": render_bank,
    "industry_license": render_license,
}


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def generate() -> None:
    master = json.loads(VENDOR_MASTER_PATH.read_text())
    submissions, expected = [], []

    for v in master["vendors"]:
        vid = v["vendor_id"]
        plan = DEFECTS.get(vid, {})
        payloads = build_payloads(v)
        for doc_type, overrides in plan.get("overrides", {}).items():
            payloads[doc_type].update(overrides)

        out_dir = SUBMISSIONS_DIR / vid
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("*.pdf"):
            old.unlink()

        submitted = {}
        for slot in RENDERERS:
            if slot in plan.get("omit", []):
                continue
            actual_type = plan.get("substitute", {}).get(slot, slot)
            data = deepcopy(payloads[actual_type])
            path = out_dir / f"{slot}.pdf"
            c = canvas.Canvas(str(path), pagesize=A4)
            c.setTitle(f"{vid} {slot} (synthetic)")
            RENDERERS[actual_type](c, data)
            c.showPage()
            c.save()
            submitted[slot] = f"{vid}/{slot}.pdf"

        submissions.append({
            "vendor_id": vid,
            "vendor_name": v["legal_name"],
            "scenario": plan.get("description", ""),
            "submitted_documents": submitted,
        })
        status, codes = EXPECTED[vid]
        expected.append({
            "vendor_id": vid, "vendor_name": v["legal_name"],
            "scenario": plan.get("description", ""),
            "expected_status": status, "expected_issue_codes": codes,
        })
        print(f"{vid}: {len(submitted)} documents -> {out_dir}")

    SUBMISSIONS_INDEX_PATH.write_text(json.dumps(
        {"_disclaimer": DISCLAIMER, "submissions": submissions}, indent=2))
    EXPECTED_RESULTS_PATH.write_text(json.dumps(
        {"_note": "Ground truth for evaluation.", "expected": expected}, indent=2))
    print(f"\nwrote {SUBMISSIONS_INDEX_PATH}\nwrote {EXPECTED_RESULTS_PATH}")


if __name__ == "__main__":
    generate()