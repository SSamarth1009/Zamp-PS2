"""
Evaluation harness.

Two things are measured, because they fail for different reasons:

  1. Decision accuracy   - did the engine reach the outcome an onboarding
     analyst would have reached, and for the right reason (expected issue
     codes must appear among the triggered checks)?
  2. Extraction accuracy - did the AI layer read each field off the page
     correctly? Ground truth is the payload the generator printed onto the
     PDF, including the deliberately corrupted values.

Separating them matters: a decision can be right while extraction is wrong
(and vice versa), and you want to know which layer to fix.
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import EXPECTED_RESULTS_PATH  # noqa: E402
from app.normalize import normalize_id  # noqa: E402
from app.pipeline import load_master, run_sample, sample_packet  # noqa: E402
from tools.generate_documents import DEFECTS, build_payloads  # noqa: E402

FIELD_NORMALISERS = {"pan", "gstin", "ifsc", "registration_number",
                     "license_number", "account_number"}


def _truth_payloads(vendor: dict) -> Dict[str, Dict[str, Any]]:
    payloads = build_payloads(vendor)
    for doc_type, overrides in DEFECTS.get(vendor["vendor_id"], {}).get("overrides", {}).items():
        payloads[doc_type].update(overrides)
    return payloads


def _same(field: str, expected: Any, actual: Any) -> bool:
    if expected is None or expected == "":
        return True
    if actual in (None, ""):
        return False
    if field in FIELD_NORMALISERS:
        return normalize_id(str(expected)) == normalize_id(str(actual))
    return " ".join(str(expected).split()).lower() == " ".join(str(actual).split()).lower()


def evaluate(persist: bool = False) -> Dict[str, Any]:
    expected = {e["vendor_id"]: e for e in json.loads(
        EXPECTED_RESULTS_PATH.read_text())["expected"]}
    master = {v["vendor_id"]: v for v in load_master()}

    decisions: List[Dict[str, Any]] = []
    field_rows: List[Dict[str, Any]] = []

    for vendor_id, exp in expected.items():
        result = run_sample(vendor_id, persist=persist)
        triggered = set(result.decision.triggered_by)
        detected = {c.check_id for c in result.checks if c.status in ("FAIL", "WARN")}
        status_ok = result.decision.status == exp["expected_status"]
        # "Reason correct" means every issue we deliberately planted was found,
        # not merely that the final label happened to be right.
        codes_ok = set(exp["expected_issue_codes"]).issubset(detected)
        decisions.append({
            "vendor_id": vendor_id,
            "vendor_name": exp["vendor_name"],
            "scenario": exp["scenario"],
            "expected": exp["expected_status"],
            "predicted": result.decision.status,
            "status_correct": status_ok,
            "expected_issue_codes": exp["expected_issue_codes"],
            "decision_drivers": sorted(triggered),
            "detected_issues": sorted(detected),
            "reason_correct": codes_ok,
            "correct": status_ok and codes_ok,
        })

        # ---- field-level extraction accuracy -------------------------
        truth = _truth_payloads(master[vendor_id])
        submitted_slots = {slot: path for slot, path in sample_packet(vendor_id)}
        plan = DEFECTS.get(vendor_id, {})
        by_file = {d.source_file: d for d in result.documents}
        for slot in submitted_slots:
            actual_type = plan.get("substitute", {}).get(slot, slot)
            doc = by_file.get(f"{slot}.pdf")
            if not doc:
                continue
            for field, expected_value in deepcopy(truth[actual_type]).items():
                canonical = "account_holder_name" if field == "bank_account_name" else field
                ok = _same(canonical, expected_value, doc.value(canonical))
                field_rows.append({"vendor_id": vendor_id, "document": slot,
                                   "field": canonical, "expected": expected_value,
                                   "extracted": doc.value(canonical), "correct": ok})

    total = len(decisions)
    correct = sum(1 for d in decisions if d["correct"])
    status_correct = sum(1 for d in decisions if d["status_correct"])
    f_total = len(field_rows)
    f_correct = sum(1 for r in field_rows if r["correct"])

    by_doc: Dict[str, Dict[str, int]] = {}
    for r in field_rows:
        entry = by_doc.setdefault(r["document"], {"total": 0, "correct": 0})
        entry["total"] += 1
        entry["correct"] += int(r["correct"])

    return {
        "decisions": decisions,
        "decision_accuracy": round(status_correct / total, 4) if total else 0.0,
        "decision_and_reason_accuracy": round(correct / total, 4) if total else 0.0,
        "extraction_accuracy": round(f_correct / f_total, 4) if f_total else 0.0,
        "extraction_fields_checked": f_total,
        "extraction_by_document": {
            k: round(v["correct"] / v["total"], 4) for k, v in sorted(by_doc.items())},
        "extraction_errors": [r for r in field_rows if not r["correct"]],
    }


if __name__ == "__main__":
    report = evaluate()
    print(f"{'Vendor':7} {'Expected':10} {'Predicted':10} {'OK':3}  Scenario")
    print("-" * 96)
    for d in report["decisions"]:
        print(f"{d['vendor_id']:7} {d['expected']:10} {d['predicted']:10} "
              f"{'Y' if d['correct'] else 'N':3}  {d['scenario'][:58]}")
    print("-" * 96)
    print(f"Decision accuracy            : {report['decision_accuracy']:.1%}")
    print(f"Decision + reason accuracy   : {report['decision_and_reason_accuracy']:.1%}")
    print(f"Extraction accuracy          : {report['extraction_accuracy']:.1%} "
          f"({report['extraction_fields_checked']} fields)")
    for doc, acc in report["extraction_by_document"].items():
        print(f"   {doc:28}: {acc:.1%}")
    for err in report["extraction_errors"]:
        print(f"   MISS {err['vendor_id']} {err['document']}.{err['field']}: "
              f"expected {err['expected']!r}, got {err['extracted']!r}")