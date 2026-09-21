"""
Pipeline orchestrator.

    files -> classify -> extract -> normalise -> validate (deterministic)
          -> decide (deterministic) -> explain -> persist

`on_step` is called as each stage completes so the Streamlit UI can render a
live execution view rather than a spinner. The same function is used by the
API, the CLI demo and the test harness, so there is exactly one code path.
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from app import db, explain, llm
from app.config import (DOCUMENT_LABELS, REQUIRED_DOCUMENTS, SUBMISSIONS_DIR,
                        SUBMISSIONS_INDEX_PATH, VENDOR_MASTER_PATH)
from app.extraction import classifier, extractor, pdf_text
from app.extraction.normalizer import normalize_document
from app.schemas import CanonicalDocument, PipelineStep, RunResult
from app.validation import rules
from app.validation.decision import decide, required_actions

StepCallback = Optional[Callable[[PipelineStep], None]]


# ---------------------------------------------------------------------------
# Master data access
# ---------------------------------------------------------------------------
def load_master() -> List[dict]:
    return json.loads(VENDOR_MASTER_PATH.read_text())["vendors"]


def master_record(vendor_id: str) -> Optional[dict]:
    return next((v for v in load_master() if v["vendor_id"] == vendor_id), None)


def load_submissions() -> List[dict]:
    if not SUBMISSIONS_INDEX_PATH.exists():
        return []
    return json.loads(SUBMISSIONS_INDEX_PATH.read_text())["submissions"]


def sample_packet(vendor_id: str) -> List[Tuple[str, Path]]:
    """(declared_slot, path) pairs for one of the bundled synthetic vendors."""
    sub = next((s for s in load_submissions() if s["vendor_id"] == vendor_id), None)
    if not sub:
        raise KeyError(f"No sample submission for {vendor_id}")
    return [(slot, SUBMISSIONS_DIR / rel) for slot, rel in sub["submitted_documents"].items()]


def infer_slot(filename: str) -> str:
    """Map an uploaded filename to the slot the vendor intended.

    Only used to detect 'wrong file in the wrong slot'. Classification itself
    never trusts this.
    """
    stem = Path(filename).stem.lower().replace("-", "_").replace(" ", "_")
    for slot in REQUIRED_DOCUMENTS:
        if slot in stem:
            return slot
    aliases = {"application": "vendor_application", "form": "vendor_application",
               "incorporation": "incorporation_certificate", "coi": "incorporation_certificate",
               "gst": "gst_certificate", "pan": "pan_document", "tax": "pan_document",
               "bank": "bank_verification", "cheque": "bank_verification",
               "licence": "industry_license", "license": "industry_license"}
    for key, slot in aliases.items():
        if key in stem:
            return slot
    return ""


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_pipeline(vendor_id: str, files: Sequence[Tuple[str, Path]],
                 on_step: StepCallback = None, persist: bool = True,
                 as_of: Optional[date] = None) -> RunResult:
    steps: List[PipelineStep] = []

    def step(name: str, status: str, detail: str, t0: float) -> None:
        s = PipelineStep(name=name, status=status, detail=detail,
                         duration_ms=int((time.perf_counter() - t0) * 1000))
        steps.append(s)
        if on_step:
            on_step(s)

    master = master_record(vendor_id)
    llm_used = False

    # -- 1. intake ---------------------------------------------------------
    t0 = time.perf_counter()
    texts: Dict[str, Tuple[str, str, str]] = {}
    for declared_slot, path in files:
        text, method = pdf_text.extract_text(path)
        texts[str(path)] = (declared_slot or infer_slot(Path(path).name), text, method)
    unreadable = sum(1 for _, (_, t, _) in texts.items() if not t.strip())
    step("Documents received", "warn" if unreadable else "ok",
         f"{len(files)} file(s) read"
         + (f", {unreadable} with no extractable text" if unreadable else ""), t0)

    # -- 2. classification -------------------------------------------------
    t0 = time.perf_counter()
    classified: List[Tuple[str, str, str, str, float, str]] = []
    for path, (declared_slot, text, _) in texts.items():
        doc_type, confidence, method = classifier.classify(text, Path(path).name)
        llm_used = llm_used or method == "llm"
        classified.append((path, declared_slot, text, doc_type, confidence, method))
    found = sorted({c[3] for c in classified if c[3] != "unknown"})
    misrouted = [c for c in classified if c[1] and c[3] != c[1]]
    step("Documents classified",
         "warn" if (misrouted or any(c[3] == "unknown" for c in classified)) else "ok",
         f"{len(found)} document type(s) identified: "
         f"{', '.join(DOCUMENT_LABELS.get(f, f) for f in found)}"
         + (f" | {len(misrouted)} file(s) not in the expected slot" if misrouted else ""), t0)

    # -- 3. extraction -----------------------------------------------------
    t0 = time.perf_counter()
    documents: List[CanonicalDocument] = []
    for path, declared_slot, text, doc_type, confidence, method in classified:
        doc = extractor.build_document(Path(path).name, declared_slot, text,
                                       doc_type, confidence, method)
        llm_used = llm_used or doc.extraction_method == "llm"
        documents.append(doc)
    total_fields = sum(len(d.populated()) for d in documents)
    step("Information extracted", "ok",
         f"{total_fields} field(s) mapped into the canonical schema via "
         f"{'LLM' if llm_used else 'rule-based'} extraction", t0)

    # -- 4. normalisation --------------------------------------------------
    t0 = time.perf_counter()
    documents = [normalize_document(d) for d in documents]
    warn = sum(len(d.warnings) for d in documents)
    step("Data normalised", "warn" if warn else "ok",
         "Identifiers, dates, casing and punctuation canonicalised"
         + (f" | {warn} value(s) could not be parsed" if warn else ""), t0)

    # -- 5-7. deterministic validation ------------------------------------
    ctx = rules.VendorContext(vendor_id=vendor_id, documents=documents, master=master,
                              as_of=as_of or date.today())
    checks = []

    groups = [
        ("Completeness validated", ["completeness"]),
        ("Cross-document validation", ["identity", "tax"]),
        ("Business rules applied", ["banking", "licence"]),
        ("Master registry reconciled", ["registry"]),
    ]
    t0 = time.perf_counter()
    all_checks = rules.run_all(ctx)
    for label, categories in groups:
        subset = [c for c in all_checks if c.category in categories]
        checks.extend(subset)
        failed = [c for c in subset if c.status == "FAIL"]
        review = [c for c in subset if c.status == "WARN"]
        if failed:
            status, detail = "fail", "; ".join(c.message for c in failed)
        elif review:
            status, detail = "warn", "; ".join(c.message for c in review)
        else:
            status, detail = "ok", f"{len([c for c in subset if c.status == 'PASS'])} check(s) passed"
        step(label, status, detail, t0)
        t0 = time.perf_counter()

    checks = all_checks  # preserve canonical rule order for the audit log

    # -- 8. decision -------------------------------------------------------
    t0 = time.perf_counter()
    decision = decide(checks)
    actions = required_actions(checks)
    step("Decision generated", {"APPROVED": "ok", "PENDING": "warn", "REJECTED": "fail"}[decision.status],
         f"{decision.status} - {decision.reason}", t0)

    # -- 9. explanation ----------------------------------------------------
    t0 = time.perf_counter()
    vendor_name = (master or {}).get("legal_name") or ctx.declared_legal_name
    report = explain.build_report(vendor_id, vendor_name, decision, checks, actions)
    note = explain.vendor_note(vendor_name, decision, checks, actions)
    if note:
        llm_used = True
    else:
        note = explain.fallback_note(vendor_name, decision, actions)
    step("Explanation generated", "ok",
         f"Decision report and vendor communication drafted "
         f"({'LLM' if llm.available() else 'template'})", t0)

    result = RunResult(
        vendor_id=vendor_id, vendor_name=vendor_name, decision=decision, checks=checks,
        documents=documents, steps=steps,
        explanation=report + "\n\n--- DRAFT NOTE TO VENDOR ---\n" + note,
        required_actions=actions, llm_used=llm_used,
    )

    # -- 10. audit trail ---------------------------------------------------
    if persist:
        t0 = time.perf_counter()
        run_id = db.save_run(result)
        step("Audit trail stored", "ok", f"Run #{run_id} written to SQLite", t0)
    return result


def run_sample(vendor_id: str, on_step: StepCallback = None,
               persist: bool = True) -> RunResult:
    return run_pipeline(vendor_id, sample_packet(vendor_id), on_step=on_step, persist=persist)