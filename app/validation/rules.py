"""
Deterministic validation rules.

EVERY rule in this file is plain Python. No model is consulted, nothing is
probabilistic, and each rule returns a structured CheckResult carrying its own
evidence and the action the vendor must take. Re-running the same packet
always produces the same checks in the same order - which is what makes the
audit trail defensible.

Rule ids are stable and are what the evaluation harness asserts against.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from app.config import (ACCOUNT_NUMBER_REGEX, ADDRESS_REVIEW_THRESHOLD, BANK_NAME_STRICT,
                        CIN_REGEX, DOCUMENT_LABELS, GSTIN_REGEX, GST_STATE_CODES,
                        IFSC_REGEX, LICENSE_REGEX, NAME_REVIEW_THRESHOLD, PAN_REGEX,
                        REQUIRED_DOCUMENTS, REQUIRED_FIELDS, SEVERITY_CRITICAL,
                        SEVERITY_MAJOR, SEVERITY_MINOR)
from app.normalize import (compare_addresses, compare_names, gstin_checksum_valid,
                           is_expired, normalize_id, normalize_industry,
                           pan_from_gstin, state_code_from_gstin)
from app.schemas import CanonicalDocument, CheckResult


@dataclass
class VendorContext:
    """Everything the rules are allowed to look at."""
    vendor_id: str
    documents: List[CanonicalDocument]
    master: Optional[dict] = None
    as_of: date = field(default_factory=date.today)

    def __post_init__(self) -> None:
        self.by_type: Dict[str, CanonicalDocument] = {}
        self.duplicates: List[CanonicalDocument] = []
        for doc in self.documents:
            if doc.document_type == "unknown":
                continue
            if doc.document_type in self.by_type:
                self.duplicates.append(doc)
            else:
                self.by_type[doc.document_type] = doc

    def get(self, doc_type: str) -> Optional[CanonicalDocument]:
        return self.by_type.get(doc_type)

    def field_from(self, doc_types: List[str], field_name: str):
        """First non-empty value of a field, searching documents in priority order."""
        for doc_type in doc_types:
            doc = self.by_type.get(doc_type)
            if doc and getattr(doc, field_name, None):
                return getattr(doc, field_name), doc_type
        return None, None

    @property
    def declared_legal_name(self) -> Optional[str]:
        value, _ = self.field_from(
            ["vendor_application", "incorporation_certificate", "gst_certificate",
             "pan_document", "industry_license"], "legal_name")
        return value


def mk(check_id, name, category, status, severity, message,
       evidence=None, action=None) -> CheckResult:
    return CheckResult(check_id=check_id, name=name, category=category, status=status,
                       severity=severity, message=message, evidence=evidence or {},
                       required_action=action)


# ===========================================================================
# 1. Completeness
# ===========================================================================
def check_required_documents(ctx: VendorContext) -> List[CheckResult]:
    missing = [d for d in REQUIRED_DOCUMENTS if d not in ctx.by_type]
    if not missing:
        return [mk("DOC-001", "All required documents present", "completeness", "PASS",
                   SEVERITY_MAJOR, f"All {len(REQUIRED_DOCUMENTS)} required documents received.",
                   {"received": sorted(ctx.by_type)})]
    labels = [DOCUMENT_LABELS[m] for m in missing]
    return [mk("DOC-001", "All required documents present", "completeness", "FAIL",
               SEVERITY_MAJOR,
               f"{len(missing)} required document(s) not received: {', '.join(labels)}.",
               {"missing": missing, "received": sorted(ctx.by_type)},
               f"Upload the following document(s): {', '.join(labels)}.")]


def check_document_types(ctx: VendorContext) -> List[CheckResult]:
    mismatches, unknown = [], []
    for doc in ctx.documents:
        if doc.document_type == "unknown":
            unknown.append({"file": doc.source_file, "declared_as": doc.declared_slot})
        elif doc.declared_slot and doc.declared_slot != doc.document_type:
            mismatches.append({
                "file": doc.source_file,
                "uploaded_as": DOCUMENT_LABELS.get(doc.declared_slot, doc.declared_slot),
                "actually_is": DOCUMENT_LABELS.get(doc.document_type, doc.document_type),
                "confidence": doc.classification_confidence,
            })
    if not mismatches and not unknown:
        return [mk("DOC-002", "Uploaded files match their declared document type",
                   "completeness", "PASS", SEVERITY_MAJOR,
                   "Every file was classified as the document type it was uploaded as.")]
    parts = []
    if mismatches:
        parts.append("; ".join(f"{m['file']} was uploaded as {m['uploaded_as']} "
                               f"but reads as a {m['actually_is']}" for m in mismatches))
    if unknown:
        parts.append(f"{len(unknown)} file(s) could not be recognised as a supported document type")
    return [mk("DOC-002", "Uploaded files match their declared document type",
               "completeness", "FAIL", SEVERITY_MAJOR, ". ".join(parts) + ".",
               {"misclassified": mismatches, "unrecognised": unknown},
               "Re-upload the correct file for each document slot.")]


def check_readability(ctx: VendorContext) -> List[CheckResult]:
    unreadable = [d.source_file for d in ctx.documents if not d.raw_text.strip()]
    if not unreadable:
        return [mk("DOC-003", "Documents machine readable", "completeness", "PASS",
                   SEVERITY_MAJOR, "Text could be extracted from every uploaded file.")]
    return [mk("DOC-003", "Documents machine readable", "completeness", "FAIL",
               SEVERITY_MAJOR,
               f"No text could be extracted from {len(unreadable)} file(s).",
               {"unreadable": unreadable},
               "Re-upload a text-based PDF or a higher quality scan.")]


def check_required_fields(ctx: VendorContext) -> List[CheckResult]:
    gaps = {}
    for doc_type, required in REQUIRED_FIELDS.items():
        doc = ctx.get(doc_type)
        if not doc:
            continue  # absence is DOC-001's job
        missing = [f for f in required if not getattr(doc, f, None)]
        if missing:
            gaps[DOCUMENT_LABELS[doc_type]] = missing
    if not gaps:
        return [mk("FLD-001", "Mandatory fields extracted from every document",
                   "completeness", "PASS", SEVERITY_MAJOR,
                   "All mandatory fields were found on the submitted documents.")]
    flat = "; ".join(f"{k}: {', '.join(v)}" for k, v in gaps.items())
    return [mk("FLD-001", "Mandatory fields extracted from every document",
               "completeness", "FAIL", SEVERITY_MAJOR,
               f"Mandatory information could not be read from the documents ({flat}).",
               {"missing_fields": gaps},
               "Provide a legible document showing the missing particulars.")]


# ===========================================================================
# 2. Tax identity
# ===========================================================================
def check_pan_format(ctx: VendorContext) -> List[CheckResult]:
    pan, source = ctx.field_from(["pan_document", "vendor_application"], "pan")
    if not pan:
        return [mk("TAX-001", "PAN format valid", "tax", "SKIPPED", SEVERITY_CRITICAL,
                   "No PAN was extracted; covered by the completeness checks.")]
    if re.match(PAN_REGEX, normalize_id(pan)):
        return [mk("TAX-001", "PAN format valid", "tax", "PASS", SEVERITY_CRITICAL,
                   f"PAN {pan} matches the statutory AAAAA9999A format.",
                   {"pan": pan, "source_document": DOCUMENT_LABELS.get(source, source)})]
    return [mk("TAX-001", "PAN format valid", "tax", "FAIL", SEVERITY_CRITICAL,
               f"PAN '{pan}' is not a valid Indian PAN. Expected five letters, "
               f"four digits and one letter (AAAAA9999A).",
               {"pan_submitted": pan, "expected_format": "AAAAA9999A",
                "source_document": DOCUMENT_LABELS.get(source, source)},
               "Submit a PAN card / allotment letter showing a correctly formatted PAN.")]


def check_gstin_format(ctx: VendorContext) -> List[CheckResult]:
    gstin, source = ctx.field_from(["gst_certificate", "vendor_application"], "gstin")
    if not gstin:
        return [mk("TAX-002", "GSTIN format and checksum valid", "tax", "SKIPPED",
                   SEVERITY_CRITICAL, "No GSTIN was extracted; covered by completeness checks.")]
    g = normalize_id(gstin)
    structure_ok = bool(re.match(GSTIN_REGEX, g))
    checksum_ok = gstin_checksum_valid(g)
    state_ok = state_code_from_gstin(g) in GST_STATE_CODES
    if structure_ok and checksum_ok and state_ok:
        return [mk("TAX-002", "GSTIN format and checksum valid", "tax", "PASS",
                   SEVERITY_CRITICAL,
                   f"GSTIN {g} is structurally valid and the checksum digit verifies.",
                   {"gstin": g, "state": GST_STATE_CODES.get(state_code_from_gstin(g)),
                    "source_document": DOCUMENT_LABELS.get(source, source)})]
    problems = []
    if not structure_ok:
        problems.append("structure does not match 99AAAAA9999A9Z9")
    if not state_ok:
        problems.append(f"state code '{state_code_from_gstin(g)}' is not a valid state code")
    if structure_ok and not checksum_ok:
        problems.append("checksum digit does not verify")
    return [mk("TAX-002", "GSTIN format and checksum valid", "tax", "FAIL",
               SEVERITY_CRITICAL,
               f"GSTIN '{gstin}' failed validation ({'; '.join(problems)}).",
               {"gstin_submitted": gstin, "problems": problems,
                "source_document": DOCUMENT_LABELS.get(source, source)},
               "Submit the GST registration certificate showing the correct 15-character GSTIN.")]


def check_pan_consistency(ctx: VendorContext) -> List[CheckResult]:
    app, pan_doc = ctx.get("vendor_application"), ctx.get("pan_document")
    if not (app and app.pan and pan_doc and pan_doc.pan):
        return [mk("TAX-003", "PAN consistent across documents", "tax", "SKIPPED",
                   SEVERITY_MAJOR, "PAN not available on both the application and the PAN document.")]
    if normalize_id(app.pan) == normalize_id(pan_doc.pan):
        return [mk("TAX-003", "PAN consistent across documents", "tax", "PASS",
                   SEVERITY_MAJOR, "The PAN on the application matches the PAN document.",
                   {"application": app.pan, "pan_document": pan_doc.pan})]
    return [mk("TAX-003", "PAN consistent across documents", "tax", "FAIL", SEVERITY_MAJOR,
               "The PAN declared on the application form differs from the PAN document.",
               {"application_form": app.pan, "pan_document": pan_doc.pan},
               "Confirm which PAN belongs to the entity and resubmit the matching document.")]


def check_pan_inside_gstin(ctx: VendorContext) -> List[CheckResult]:
    pan, pan_src = ctx.field_from(["pan_document", "vendor_application"], "pan")
    gstin, gst_src = ctx.field_from(["gst_certificate", "vendor_application"], "gstin")
    if not pan or not gstin:
        return [mk("TAX-004", "PAN embedded in GSTIN matches PAN document", "tax", "SKIPPED",
                   SEVERITY_MAJOR, "PAN or GSTIN unavailable.")]
    embedded = pan_from_gstin(gstin)
    if not embedded:
        return [mk("TAX-004", "PAN embedded in GSTIN matches PAN document", "tax", "SKIPPED",
                   SEVERITY_MAJOR, "GSTIN is not 15 characters, so no PAN can be extracted from it.")]
    if embedded == normalize_id(pan):
        return [mk("TAX-004", "PAN embedded in GSTIN matches PAN document", "tax", "PASS",
                   SEVERITY_MAJOR,
                   f"Characters 3-12 of the GSTIN ({embedded}) match the PAN on record.",
                   {"gstin": gstin, "pan_in_gstin": embedded, "pan_document": pan})]
    return [mk("TAX-004", "PAN embedded in GSTIN matches PAN document", "tax", "FAIL",
               SEVERITY_MAJOR,
               f"The PAN embedded in the GSTIN ({embedded}) does not match the PAN submitted ({pan}).",
               {"gstin": gstin, "pan_in_gstin": embedded, "pan_submitted": pan,
                "pan_source": DOCUMENT_LABELS.get(pan_src, pan_src),
                "gstin_source": DOCUMENT_LABELS.get(gst_src, gst_src)},
               "The GST registration and the PAN must belong to the same entity. "
               "Resubmit the correct PAN or GST certificate.")]


def check_gst_state(ctx: VendorContext) -> List[CheckResult]:
    gst = ctx.get("gst_certificate")
    if not gst or not gst.gstin:
        return [mk("TAX-005", "GST state code consistent with registered address", "tax",
                   "SKIPPED", SEVERITY_MINOR, "GST certificate unavailable.")]
    code = state_code_from_gstin(gst.gstin)
    state_from_code = GST_STATE_CODES.get(code)
    stated = (gst.state or "")
    address = (gst.registered_address or "")
    if not state_from_code:
        return [mk("TAX-005", "GST state code consistent with registered address", "tax",
                   "FAIL", SEVERITY_MINOR, f"GSTIN state code '{code}' is not recognised.",
                   {"state_code": code}, "Confirm the state of registration.")]
    hit = (state_from_code.lower() in stated.lower()
           or state_from_code.lower() in address.lower())
    if hit:
        return [mk("TAX-005", "GST state code consistent with registered address", "tax",
                   "PASS", SEVERITY_MINOR,
                   f"GSTIN prefix {code} corresponds to {state_from_code}, consistent with "
                   f"the registered address.",
                   {"state_code": code, "state": state_from_code})]
    return [mk("TAX-005", "GST state code consistent with registered address", "tax",
               "WARN", SEVERITY_MINOR,
               f"GSTIN prefix {code} indicates {state_from_code}, but the certificate states "
               f"'{stated or 'unknown'}'.",
               {"state_code": code, "state_from_code": state_from_code, "state_on_document": stated},
               "Confirm the place of business for this GST registration.")]


# ===========================================================================
# 3. Legal identity / names / address
# ===========================================================================
NAME_SOURCES = ["vendor_application", "incorporation_certificate", "gst_certificate",
                "pan_document", "industry_license"]


def check_name_consistency(ctx: VendorContext) -> List[CheckResult]:
    """Cross-document name comparison. The bank title is deliberately excluded
    and handled by BNK-001 under a stricter policy."""
    names = [(dt, ctx.by_type[dt].legal_name) for dt in NAME_SOURCES
             if ctx.by_type.get(dt) and ctx.by_type[dt].legal_name]
    if len(names) < 2:
        return [mk("NAM-001", "Legal name consistent across documents", "identity", "SKIPPED",
                   SEVERITY_MAJOR, "Fewer than two documents carry a legal name.")]
    baseline_type, baseline = names[0]
    reviews, mismatches, evidence = [], [], {DOCUMENT_LABELS[baseline_type]: baseline}
    for doc_type, name in names[1:]:
        verdict, score = compare_names(baseline, name, NAME_REVIEW_THRESHOLD)
        evidence[DOCUMENT_LABELS[doc_type]] = name
        if verdict == "REVIEW":
            reviews.append((doc_type, name, score))
        elif verdict == "MISMATCH":
            mismatches.append((doc_type, name, score))
    if mismatches:
        detail = "; ".join(f"{DOCUMENT_LABELS[d]} says '{n}' (similarity {s})"
                           for d, n, s in mismatches)
        return [mk("NAM-001", "Legal name consistent across documents", "identity", "FAIL",
                   SEVERITY_MAJOR,
                   f"The legal name differs materially across documents. Baseline "
                   f"'{baseline}' from the {DOCUMENT_LABELS[baseline_type]}; {detail}.",
                   evidence,
                   "Submit documents that all carry the same registered legal name, or "
                   "provide a name-change certificate.")]
    if reviews:
        detail = "; ".join(f"{DOCUMENT_LABELS[d]} says '{n}' (similarity {s})"
                           for d, n, s in reviews)
        return [mk("NAM-001", "Legal name consistent across documents", "identity", "WARN",
                   SEVERITY_MINOR,
                   f"Near-identical but non-identical legal names detected - possible typo "
                   f"or stale document. Baseline '{baseline}'; {detail}.",
                   evidence,
                   "Confirm the exact registered legal name and reissue the affected document.")]
    return [mk("NAM-001", "Legal name consistent across documents", "identity", "PASS",
               SEVERITY_MAJOR,
               f"All {len(names)} documents carry the same legal name after normalisation.",
               evidence)]


def check_name_vs_registry(ctx: VendorContext) -> List[CheckResult]:
    if not ctx.master:
        return [mk("NAM-002", "Legal name matches vendor master registry", "registry",
                   "SKIPPED", SEVERITY_MAJOR, "Vendor not present in the master registry.")]
    declared = ctx.declared_legal_name
    verdict, score = compare_names(ctx.master["legal_name"], declared, NAME_REVIEW_THRESHOLD)
    evidence = {"registry": ctx.master["legal_name"], "submitted": declared, "similarity": score}
    if verdict == "EXACT":
        return [mk("NAM-002", "Legal name matches vendor master registry", "registry", "PASS",
                   SEVERITY_MAJOR, "Submitted legal name matches the master registry.", evidence)]
    if verdict == "REVIEW":
        return [mk("NAM-002", "Legal name matches vendor master registry", "registry", "WARN",
                   SEVERITY_MINOR,
                   "Submitted legal name is close to but not identical with the registry record.",
                   evidence, "Procurement to confirm the entity against the registry record.")]
    return [mk("NAM-002", "Legal name matches vendor master registry", "registry", "FAIL",
               SEVERITY_MAJOR,
               "Submitted legal name does not match the vendor master registry record.",
               evidence, "Confirm the correct legal entity for this vendor record.")]


def check_address_consistency(ctx: VendorContext) -> List[CheckResult]:
    sources = ["vendor_application", "incorporation_certificate", "gst_certificate"]
    addrs = [(dt, ctx.by_type[dt].registered_address) for dt in sources
             if ctx.by_type.get(dt) and ctx.by_type[dt].registered_address]
    if len(addrs) < 2:
        return [mk("ADR-001", "Registered address consistent across documents", "identity",
                   "SKIPPED", SEVERITY_MINOR, "Fewer than two documents carry an address.")]
    base_type, base = addrs[0]
    evidence = {DOCUMENT_LABELS[base_type]: base}
    worst, worst_doc, worst_score = "EXACT", None, 1.0
    for doc_type, addr in addrs[1:]:
        verdict, score = compare_addresses(base, addr, ADDRESS_REVIEW_THRESHOLD)
        evidence[DOCUMENT_LABELS[doc_type]] = addr
        if verdict == "MISMATCH" or (verdict == "REVIEW" and worst == "EXACT"):
            worst, worst_doc, worst_score = verdict, doc_type, score
    if worst == "EXACT":
        return [mk("ADR-001", "Registered address consistent across documents", "identity",
                   "PASS", SEVERITY_MINOR, "Registered address agrees across all documents.",
                   evidence)]
    status = "FAIL" if worst == "MISMATCH" else "WARN"
    return [mk("ADR-001", "Registered address consistent across documents", "identity",
               status, SEVERITY_MINOR,
               f"Registered address on the {DOCUMENT_LABELS[worst_doc]} differs from the "
               f"{DOCUMENT_LABELS[base_type]} (similarity {worst_score}).",
               evidence, "Confirm the current registered office address.")]


def check_registration_number(ctx: VendorContext) -> List[CheckResult]:
    results = []
    cin, source = ctx.field_from(["incorporation_certificate", "vendor_application"],
                                 "registration_number")
    if not cin:
        results.append(mk("REG-001", "Company registration number valid", "identity", "SKIPPED",
                          SEVERITY_MAJOR, "No registration number extracted."))
    elif re.match(CIN_REGEX, normalize_id(cin)):
        results.append(mk("REG-001", "Company registration number valid", "identity", "PASS",
                          SEVERITY_MAJOR, f"CIN {cin} matches the expected 21-character format.",
                          {"cin": cin, "source_document": DOCUMENT_LABELS.get(source, source)}))
    else:
        results.append(mk("REG-001", "Company registration number valid", "identity", "FAIL",
                          SEVERITY_MAJOR,
                          f"Registration number '{cin}' does not match the expected CIN format "
                          f"(U99999XX9999XXX999999).",
                          {"cin_submitted": cin}, "Provide the certificate of incorporation "
                                                  "showing the correct CIN."))
    app, inc = ctx.get("vendor_application"), ctx.get("incorporation_certificate")
    if app and inc and app.registration_number and inc.registration_number:
        if normalize_id(app.registration_number) == normalize_id(inc.registration_number):
            results.append(mk("REG-002", "Registration number consistent across documents",
                              "identity", "PASS", SEVERITY_MAJOR,
                              "The CIN on the application matches the certificate of incorporation.",
                              {"application": app.registration_number,
                               "incorporation_certificate": inc.registration_number}))
        else:
            results.append(mk("REG-002", "Registration number consistent across documents",
                              "identity", "FAIL", SEVERITY_MAJOR,
                              "The CIN declared on the application differs from the "
                              "certificate of incorporation.",
                              {"application": app.registration_number,
                               "incorporation_certificate": inc.registration_number},
                              "Confirm the correct CIN and resubmit."))
    else:
        results.append(mk("REG-002", "Registration number consistent across documents",
                          "identity", "SKIPPED", SEVERITY_MAJOR,
                          "Registration number not available on both documents."))
    return results


# ===========================================================================
# 4. Banking
# ===========================================================================
def check_bank_account_holder(ctx: VendorContext) -> List[CheckResult]:
    bank = ctx.get("bank_verification")
    if not bank or not bank.account_holder_name:
        return [mk("BNK-001", "Bank account holder matches legal entity", "banking", "SKIPPED",
                   SEVERITY_MAJOR, "No bank verification document available.")]
    legal = ctx.declared_legal_name
    verdict, score = compare_names(legal, bank.account_holder_name, NAME_REVIEW_THRESHOLD)
    evidence = {"legal_name": legal, "bank_account_holder": bank.account_holder_name,
                "similarity": score, "source_document": DOCUMENT_LABELS["bank_verification"]}
    if verdict == "EXACT":
        return [mk("BNK-001", "Bank account holder matches legal entity", "banking", "PASS",
                   SEVERITY_MAJOR,
                   "The bank account title matches the legal entity name after normalisation.",
                   evidence)]
    action = ("Confirm the legal entity that owns this bank account, or provide a bank "
              "letter / cancelled cheque in the exact registered legal name.")
    if verdict == "REVIEW" and not BANK_NAME_STRICT:
        return [mk("BNK-001", "Bank account holder matches legal entity", "banking", "WARN",
                   SEVERITY_MINOR, "Bank account title is close to the legal name but not identical.",
                   evidence, action)]
    closeness = ("a near match, which usually means a different group entity or a typo"
                 if verdict == "REVIEW" else "materially different")
    return [mk("BNK-001", "Bank account holder matches legal entity", "banking", "FAIL",
               SEVERITY_MAJOR,
               f"The bank account is held by '{bank.account_holder_name}' while the vendor's "
               f"legal name is '{legal}' - {closeness} (similarity {score}). Payments must not "
               f"be released to an unverified account holder.",
               evidence, action)]


def check_bank_details(ctx: VendorContext) -> List[CheckResult]:
    bank = ctx.get("bank_verification")
    if not bank:
        return [mk("BNK-002", "Bank identifiers valid and consistent", "banking", "SKIPPED",
                   SEVERITY_MAJOR, "No bank verification document available.")]
    problems, evidence = [], {}
    if bank.ifsc:
        evidence["ifsc"] = bank.ifsc
        if not re.match(IFSC_REGEX, normalize_id(bank.ifsc)):
            problems.append(f"IFSC '{bank.ifsc}' is not in the AAAA0XXXXXX format")
    else:
        problems.append("IFSC missing")
    if bank.account_number:
        evidence["account_number"] = "****" + bank.account_number[-4:]
        if not re.match(ACCOUNT_NUMBER_REGEX, normalize_id(bank.account_number)):
            problems.append("account number is not a plausible 9-18 digit account number")
    else:
        problems.append("account number missing")

    app = ctx.get("vendor_application")
    if app and app.ifsc and bank.ifsc and normalize_id(app.ifsc) != normalize_id(bank.ifsc):
        problems.append(f"IFSC on the application ({app.ifsc}) differs from the bank letter "
                        f"({bank.ifsc})")
        evidence["ifsc_on_application"] = app.ifsc
    if app and app.bank_name and bank.bank_name:
        evidence["bank_on_application"] = app.bank_name
        evidence["bank_on_letter"] = bank.bank_name
        if compare_names(app.bank_name, bank.bank_name, 0.8)[0] == "MISMATCH":
            problems.append(f"bank name on the application ({app.bank_name}) differs from the "
                            f"bank letter ({bank.bank_name})")
    if problems:
        return [mk("BNK-002", "Bank identifiers valid and consistent", "banking", "FAIL",
                   SEVERITY_MAJOR, "Banking details failed validation: " + "; ".join(problems) + ".",
                   evidence, "Provide a bank letter or cancelled cheque with a valid IFSC and "
                             "account number matching the application.")]
    return [mk("BNK-002", "Bank identifiers valid and consistent", "banking", "PASS",
               SEVERITY_MAJOR, "IFSC and account number are well formed and agree with the "
                               "application form.", evidence)]


def check_bank_vs_registry(ctx: VendorContext) -> List[CheckResult]:
    bank = ctx.get("bank_verification")
    if not ctx.master or not bank:
        return [mk("BNK-003", "Bank details match vendor master registry", "registry", "SKIPPED",
                   SEVERITY_MAJOR, "Registry record or bank document unavailable.")]
    diffs, evidence = [], {}
    if bank.account_number and normalize_id(bank.account_number) != normalize_id(
            ctx.master["account_number"]):
        diffs.append("account number differs from the registry record")
        evidence["account_number_submitted"] = "****" + bank.account_number[-4:]
        evidence["account_number_registry"] = "****" + ctx.master["account_number"][-4:]
    if bank.ifsc and normalize_id(bank.ifsc) != normalize_id(ctx.master["ifsc"]):
        diffs.append("IFSC differs from the registry record")
        evidence["ifsc_submitted"] = bank.ifsc
        evidence["ifsc_registry"] = ctx.master["ifsc"]
    if bank.account_holder_name and compare_names(
            ctx.master["bank_account_name"], bank.account_holder_name,
            NAME_REVIEW_THRESHOLD)[0] != "EXACT":
        diffs.append("account holder differs from the registry record")
        evidence["holder_submitted"] = bank.account_holder_name
        evidence["holder_registry"] = ctx.master["bank_account_name"]
    if diffs:
        return [mk("BNK-003", "Bank details match vendor master registry", "registry", "FAIL",
                   SEVERITY_MAJOR,
                   "Submitted banking details do not agree with the vendor master registry: "
                   + "; ".join(diffs) + ".", evidence,
                   "Banking changes must be confirmed with the vendor through an out-of-band "
                   "channel before payment details are updated.")]
    return [mk("BNK-003", "Bank details match vendor master registry", "registry", "PASS",
               SEVERITY_MAJOR, "Banking details agree with the vendor master registry.")]


# ===========================================================================
# 5. Licence / compliance
# ===========================================================================
def check_license_number(ctx: VendorContext) -> List[CheckResult]:
    lic = ctx.get("industry_license")
    if not lic:
        return [mk("LIC-001", "Industry licence number valid", "licence", "SKIPPED",
                   SEVERITY_MAJOR, "No industry licence submitted.")]
    if not lic.license_number:
        return [mk("LIC-001", "Industry licence number valid", "licence", "FAIL",
                   SEVERITY_MAJOR, "The licence document does not show a licence number.",
                   {}, "Provide a licence document showing the licence number.")]
    if re.match(LICENSE_REGEX, lic.license_number):
        return [mk("LIC-001", "Industry licence number valid", "licence", "PASS",
                   SEVERITY_MAJOR, f"Licence number {lic.license_number} is well formed.",
                   {"license_number": lic.license_number})]
    return [mk("LIC-001", "Industry licence number valid", "licence", "FAIL", SEVERITY_MAJOR,
               f"Licence number '{lic.license_number}' does not match the expected "
               f"AAA-LIC-YYYY-NNNN format.",
               {"license_number": lic.license_number}, "Provide a valid licence document.")]


def check_license_holder(ctx: VendorContext) -> List[CheckResult]:
    lic = ctx.get("industry_license")
    if not lic or not lic.legal_name:
        return [mk("LIC-002", "Licence issued to the vendor entity", "licence", "SKIPPED",
                   SEVERITY_MAJOR, "Licence document or licensee name unavailable.")]
    legal = ctx.declared_legal_name
    verdict, score = compare_names(legal, lic.legal_name, NAME_REVIEW_THRESHOLD)
    evidence = {"legal_name": legal, "licensee": lic.legal_name, "similarity": score}
    if verdict == "EXACT":
        return [mk("LIC-002", "Licence issued to the vendor entity", "licence", "PASS",
                   SEVERITY_MAJOR, "The licence is held by the vendor entity.", evidence)]
    if verdict == "REVIEW":
        return [mk("LIC-002", "Licence issued to the vendor entity", "licence", "WARN",
                   SEVERITY_MINOR, "The licensee name is close to but not identical with the "
                                   "vendor legal name.", evidence,
                   "Confirm the licence belongs to the contracting entity.")]
    return [mk("LIC-002", "Licence issued to the vendor entity", "licence", "FAIL",
               SEVERITY_MAJOR,
               f"The licence is issued to '{lic.legal_name}', not to the vendor entity "
               f"'{legal}'.", evidence,
               "Provide a licence issued in the name of the contracting entity.")]


def check_license_validity(ctx: VendorContext) -> List[CheckResult]:
    lic = ctx.get("industry_license")
    if not lic:
        return [mk("LIC-003", "Industry licence in force", "licence", "SKIPPED",
                   SEVERITY_CRITICAL, "No industry licence submitted.")]
    if not lic.expiry_date:
        return [mk("LIC-003", "Industry licence in force", "licence", "FAIL", SEVERITY_MAJOR,
                   "The licence does not show a validity date.", {},
                   "Provide a licence document showing the validity period.")]
    expired = is_expired(lic.expiry_date, ctx.as_of)
    evidence = {"expiry_date": lic.expiry_date, "evaluated_on": ctx.as_of.isoformat(),
                "license_number": lic.license_number}
    if expired is None:
        return [mk("LIC-003", "Industry licence in force", "licence", "FAIL", SEVERITY_MAJOR,
                   f"The licence validity date '{lic.expiry_date}' could not be interpreted.",
                   evidence, "Provide a licence document with a legible validity date.")]
    if expired:
        return [mk("LIC-003", "Industry licence in force", "licence", "FAIL", SEVERITY_CRITICAL,
                   f"The industry licence expired on {lic.expiry_date}. An expired statutory "
                   f"licence disqualifies the vendor from onboarding.", evidence,
                   "Submit the renewed industry licence. Onboarding can be resumed once a "
                   "licence valid as of today is provided.")]
    return [mk("LIC-003", "Industry licence in force", "licence", "PASS", SEVERITY_CRITICAL,
               f"The industry licence is valid until {lic.expiry_date}.", evidence)]


def check_license_industry(ctx: VendorContext) -> List[CheckResult]:
    lic, app = ctx.get("industry_license"), ctx.get("vendor_application")
    if not lic or not app or not lic.industry or not app.industry:
        return [mk("LIC-004", "Licensed activity matches declared industry", "licence",
                   "SKIPPED", SEVERITY_MINOR, "Industry not stated on both documents.")]
    if normalize_industry(lic.industry) == normalize_industry(app.industry):
        return [mk("LIC-004", "Licensed activity matches declared industry", "licence", "PASS",
                   SEVERITY_MINOR, f"The licence covers the declared activity ({app.industry}).",
                   {"declared": app.industry, "licensed": lic.industry})]
    return [mk("LIC-004", "Licensed activity matches declared industry", "licence", "WARN",
               SEVERITY_MINOR,
               f"The vendor declared '{app.industry}' but the licence covers '{lic.industry}'.",
               {"declared": app.industry, "licensed": lic.industry},
               "Confirm the licence covers the goods or services being procured.")]


# ===========================================================================
# 6. Registry cross-reference
# ===========================================================================
def check_registry_identifiers(ctx: VendorContext) -> List[CheckResult]:
    if not ctx.master:
        return [mk("REG-003", "Tax and licence identifiers match the master registry",
                   "registry", "FAIL", SEVERITY_MAJOR,
                   f"Vendor {ctx.vendor_id} was not found in the vendor master registry.",
                   {"vendor_id": ctx.vendor_id},
                   "Create the vendor master record before onboarding, or correct the vendor id.")]
    diffs, evidence = [], {}
    pan, _ = ctx.field_from(["pan_document", "vendor_application"], "pan")
    gstin, _ = ctx.field_from(["gst_certificate", "vendor_application"], "gstin")
    cin, _ = ctx.field_from(["incorporation_certificate", "vendor_application"],
                            "registration_number")
    lic = ctx.get("industry_license")
    for label, submitted, expected in [
        ("PAN", pan, ctx.master["pan"]),
        ("GSTIN", gstin, ctx.master["gstin"]),
        ("CIN", cin, ctx.master["registration_number"]),
        ("licence number", lic.license_number if lic else None, ctx.master["license_number"]),
    ]:
        if submitted and normalize_id(submitted) != normalize_id(expected):
            diffs.append(label)
            evidence[f"{label}_submitted"] = submitted
            evidence[f"{label}_registry"] = expected
    if diffs:
        return [mk("REG-003", "Tax and licence identifiers match the master registry",
                   "registry", "FAIL", SEVERITY_MAJOR,
                   f"The following identifiers do not match the vendor master registry: "
                   f"{', '.join(diffs)}.", evidence,
                   "Reconcile the submitted documents with the vendor master record.")]
    return [mk("REG-003", "Tax and licence identifiers match the master registry", "registry",
               "PASS", SEVERITY_MAJOR,
               "PAN, GSTIN, CIN and licence number all agree with the vendor master registry.")]


# ---------------------------------------------------------------------------
# Rule registry - execution order is the order shown in the UI and the audit log
# ---------------------------------------------------------------------------
ALL_RULES = [
    check_required_documents,
    check_document_types,
    check_readability,
    check_required_fields,
    check_pan_format,
    check_gstin_format,
    check_pan_consistency,
    check_pan_inside_gstin,
    check_gst_state,
    check_name_consistency,
    check_name_vs_registry,
    check_address_consistency,
    check_registration_number,
    check_bank_account_holder,
    check_bank_details,
    check_bank_vs_registry,
    check_license_number,
    check_license_holder,
    check_license_validity,
    check_license_industry,
    check_registry_identifiers,
]


def run_all(ctx: VendorContext) -> List[CheckResult]:
    results: List[CheckResult] = []
    for rule in ALL_RULES:
        results.extend(rule(ctx))
    return results