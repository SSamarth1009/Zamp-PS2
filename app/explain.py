"""
Explanation layer.

The decision is already made before this module runs. Its only job is to make
the decision readable. The deterministic report is always produced from the
check results, so the explanation can never contradict the outcome. The LLM,
when configured, is used purely to turn that report into a vendor-facing note
and is explicitly forbidden from changing the status or inventing findings.
"""
from __future__ import annotations

from typing import List, Optional

from app import llm
from app.schemas import CheckResult, Decision

ICONS = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[REVIEW]", "SKIPPED": "[N/A]"}


def build_report(vendor_id: str, vendor_name: Optional[str], decision: Decision,
                 checks: List[CheckResult], actions: List[str]) -> str:
    lines = [f"STATUS: {decision.status}",
             f"VENDOR: {vendor_id} - {vendor_name or 'unknown'}",
             "",
             f"REASON: {decision.reason}",
             "",
             f"CHECKS PERFORMED: {len(checks)}  "
             f"(passed {decision.passed} / failed {decision.failed} / "
             f"review {decision.review} / not applicable {decision.skipped})",
             ""]

    for c in checks:
        if c.status == "SKIPPED":
            continue
        lines.append(f"{ICONS[c.status]} {c.check_id}  {c.name}")
        if c.status != "PASS":
            lines.append(f"         {c.message}")

    evidence_blocks = [c for c in checks if c.status in ("FAIL", "WARN") and c.evidence]
    if evidence_blocks:
        lines += ["", "EVIDENCE"]
        for c in evidence_blocks:
            lines.append(f"  {c.check_id} ({c.name})")
            for k, v in c.evidence.items():
                lines.append(f"    - {k}: {v}")

    if actions:
        lines += ["", "REQUIRED ACTION"]
        lines += [f"  {i}. {a}" for i, a in enumerate(actions, 1)]
    else:
        lines += ["", "REQUIRED ACTION", "  None. Vendor may be activated for transacting."]

    skipped = [c for c in checks if c.status == "SKIPPED"]
    if skipped:
        lines += ["", "NOT APPLICABLE (input unavailable)"]
        lines += [f"  {c.check_id} {c.name} - {c.message}" for c in skipped]

    return "\n".join(lines)


SYSTEM = (
    "You write short, factual notes for a procurement onboarding desk. You will be given a "
    "decision that has ALREADY been made by a deterministic rule engine, together with the "
    "findings. Write a note to the vendor's contact.\n"
    "Hard constraints: never change or question the status; never introduce a finding that is "
    "not in the input; never invent identifiers, dates or amounts; do not apologise; plain "
    "text, no markdown. 120 words maximum. State what was found, what it means, and exactly "
    "what the vendor must send back (if anything)."
)


def vendor_note(vendor_name: Optional[str], decision: Decision,
                checks: List[CheckResult], actions: List[str]) -> Optional[str]:
    if not llm.available():
        return None
    findings = "\n".join(
        f"- [{c.status}] {c.check_id} {c.name}: {c.message}"
        for c in checks if c.status in ("FAIL", "WARN"))
    payload = (f"Vendor: {vendor_name}\nDecision: {decision.status}\n"
               f"Decision reason: {decision.reason}\n\nFindings:\n{findings or '- none'}\n\n"
               f"Required actions:\n" + ("\n".join(f"- {a}" for a in actions) or "- none"))
    return llm.complete(SYSTEM, payload, max_tokens=500)


def fallback_note(vendor_name: Optional[str], decision: Decision, actions: List[str]) -> str:
    """Template note used when no LLM is configured - same content, fixed phrasing."""
    opening = {
        "APPROVED": "your onboarding submission has cleared all verification checks and your "
                    "vendor record has been activated.",
        "PENDING": "your onboarding submission has been received but cannot be completed yet.",
        "REJECTED": "your onboarding submission could not be accepted in its current form.",
    }[decision.status]
    body = [f"Dear {vendor_name or 'Supplier'},", "",
            f"Thank you for your submission - {opening}", "", decision.reason]
    if actions:
        body += ["", "To proceed, please provide the following:"]
        body += [f"{i}. {a}" for i, a in enumerate(actions, 1)]
        body += ["", "Once received we will re-run the verification and confirm the outcome."]
    body += ["", "Regards,", "Vendor Onboarding Desk"]
    return "\n".join(body)