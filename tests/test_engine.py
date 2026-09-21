"""
Test suite.

Three layers are tested independently:
  * normalisation / matching primitives
  * individual rules in isolation, via a hand-built context
  * the whole pipeline on every bundled scenario (the acceptance test)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evaluate import evaluate  # noqa: E402
from app.normalize import (compare_names, gstin_check_digit, gstin_checksum_valid,  # noqa: E402
                           is_expired, normalize_company_name, pan_from_gstin)
from app.pipeline import run_sample  # noqa: E402
from app.schemas import CanonicalDocument, CheckResult  # noqa: E402
from app.validation import rules  # noqa: E402
from app.validation.decision import decide  # noqa: E402

EXPECTED = {"V001": "APPROVED", "V002": "PENDING", "V003": "PENDING", "V004": "REJECTED",
            "V005": "PENDING", "V006": "REJECTED", "V007": "PENDING"}


# --------------------------------------------------------------------------
# Normalisation primitives
# --------------------------------------------------------------------------
def test_suffix_normalisation_is_not_a_mismatch():
    assert normalize_company_name("ABC Technologies Private Limited") == \
           normalize_company_name("ABC TECHNOLOGIES PVT. LTD.")
    assert compare_names("ABC Technologies Private Limited",
                         "ABC Technologies Pvt Ltd")[0] == "EXACT"


def test_near_miss_names_go_to_review_not_rejection():
    verdict, score = compare_names("Kaveri Textiles Pvt Ltd", "Kaveri Textile Pvt Ltd")
    assert verdict == "REVIEW" and 0.88 <= score < 1.0


def test_materially_different_names_are_mismatches():
    assert compare_names("Apex Industrial Solutions Pvt Ltd",
                         "Zenith Trading Co")[0] == "MISMATCH"


def test_gstin_checksum():
    assert gstin_check_digit("29AAPCA1234F1Z") == "R"
    assert gstin_checksum_valid("29AAPCA1234F1ZR")
    assert not gstin_checksum_valid("29AAPCA1234F1ZA")
    assert pan_from_gstin("29AAPCA1234F1ZR") == "AAPCA1234F"


def test_expiry():
    assert is_expired("2024-05-31", date(2026, 1, 1)) is True
    assert is_expired("2028-03-31", date(2026, 1, 1)) is False
    assert is_expired("not a date") is None


# --------------------------------------------------------------------------
# Individual rules
# --------------------------------------------------------------------------
def ctx_with(**docs) -> rules.VendorContext:
    documents = []
    for doc_type, fields in docs.items():
        documents.append(CanonicalDocument(source_file=f"{doc_type}.pdf",
                                           declared_slot=doc_type,
                                           document_type=doc_type,
                                           raw_text="x", **fields))
    return rules.VendorContext(vendor_id="TEST", documents=documents, master=None)


def test_missing_documents_are_listed_explicitly():
    result = rules.check_required_documents(ctx_with(vendor_application={}))[0]
    assert result.status == "FAIL"
    assert "gst_certificate" in result.evidence["missing"]
    assert result.required_action


def test_wrong_document_in_slot_is_detected():
    doc = CanonicalDocument(source_file="bank_verification.pdf",
                            declared_slot="bank_verification",
                            document_type="gst_certificate", raw_text="x")
    ctx = rules.VendorContext(vendor_id="TEST", documents=[doc])
    result = rules.check_document_types(ctx)[0]
    assert result.status == "FAIL"
    assert result.evidence["misclassified"][0]["uploaded_as"] == "Bank Verification Letter"


def test_invalid_pan_is_critical():
    result = rules.check_pan_format(ctx_with(pan_document={"pan": "ABC123"}))[0]
    assert result.status == "FAIL" and result.severity == "CRITICAL"


def test_valid_pan_passes():
    assert rules.check_pan_format(ctx_with(pan_document={"pan": "AAPCA1234F"}))[0].status == "PASS"


def test_gstin_checksum_failure_is_caught():
    result = rules.check_gstin_format(ctx_with(gst_certificate={"gstin": "29AAPCA1234F1ZA"}))[0]
    assert result.status == "FAIL"
    assert any("checksum" in p for p in result.evidence["problems"])


def test_bank_holder_mismatch_never_auto_approves():
    ctx = ctx_with(vendor_application={"legal_name": "Sierra Logistics Pvt Ltd"},
                   bank_verification={"account_holder_name": "Sierra Logistics Services Pvt Ltd"})
    result = rules.check_bank_account_holder(ctx)[0]
    assert result.status in ("FAIL", "WARN")
    assert result.required_action


def test_expired_licence_is_critical():
    ctx = ctx_with(industry_license={"license_number": "PHM-LIC-2022-0455",
                                     "expiry_date": "2024-05-31", "legal_name": "X Ltd"})
    ctx.as_of = date(2026, 9, 1)
    result = rules.check_license_validity(ctx)[0]
    assert result.status == "FAIL" and result.severity == "CRITICAL"


# --------------------------------------------------------------------------
# Decision precedence
# --------------------------------------------------------------------------
def c(status, severity="MAJOR", cid="X-001"):
    return CheckResult(check_id=cid, name="n", category="tax", status=status, severity=severity)


def test_critical_failure_rejects():
    assert decide([c("PASS"), c("FAIL", "CRITICAL")]).status == "REJECTED"


def test_major_failure_pends():
    assert decide([c("PASS"), c("FAIL", "MAJOR")]).status == "PENDING"


def test_review_pends_rather_than_rejects():
    assert decide([c("PASS"), c("WARN", "MINOR")]).status == "PENDING"


def test_all_pass_approves():
    assert decide([c("PASS"), c("PASS"), c("SKIPPED")]).status == "APPROVED"


def test_critical_beats_major():
    d = decide([c("FAIL", "MAJOR", "A-1"), c("FAIL", "CRITICAL", "B-1")])
    assert d.status == "REJECTED" and d.triggered_by == ["B-1"]


# --------------------------------------------------------------------------
# End-to-end acceptance
# --------------------------------------------------------------------------
@pytest.mark.parametrize("vendor_id,expected", EXPECTED.items())
def test_scenarios_end_to_end(vendor_id, expected):
    result = run_sample(vendor_id, persist=False)
    assert result.decision.status == expected
    assert result.explanation
    if expected != "APPROVED":
        assert result.required_actions


def test_decision_is_deterministic():
    a = run_sample("V006", persist=False)
    b = run_sample("V006", persist=False)
    assert [(c.check_id, c.status) for c in a.checks] == [(c.check_id, c.status) for c in b.checks]


def test_evaluation_suite_is_fully_correct():
    report = evaluate(persist=False)
    assert report["decision_accuracy"] == 1.0
    assert report["decision_and_reason_accuracy"] == 1.0
    assert report["extraction_accuracy"] >= 0.95