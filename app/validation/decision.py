"""
The decision engine.

This is the only place in the codebase where an onboarding outcome is
produced, and it is 30 lines of boolean logic over structured check results.
No model output reaches this function. Given the same checks it always
returns the same status, which is what lets you defend a rejection.

Precedence (highest first):

  REJECTED  - at least one CRITICAL check failed. The submission cannot be
              onboarded as it stands: an identifier is not valid, or a
              statutory licence is not in force.
  PENDING   - something is missing, inconsistent, or needs a human. Fixable.
  APPROVED  - every applicable check passed and nothing was flagged for review.
"""
from __future__ import annotations

from typing import List, Tuple

from app.config import SEVERITY_CRITICAL
from app.schemas import CheckResult, Decision


def decide(checks: List[CheckResult]) -> Decision:
    critical_failures = [c for c in checks if c.status == "FAIL" and c.severity == SEVERITY_CRITICAL]
    other_failures = [c for c in checks if c.status == "FAIL" and c.severity != SEVERITY_CRITICAL]
    reviews = [c for c in checks if c.status == "WARN"]

    counts = dict(
        passed=sum(1 for c in checks if c.status == "PASS"),
        failed=sum(1 for c in checks if c.status == "FAIL"),
        review=len(reviews),
        skipped=sum(1 for c in checks if c.status == "SKIPPED"),
    )

    if critical_failures:
        status = "REJECTED"
        triggered = [c.check_id for c in critical_failures]
        reason = (f"Rejected on {len(critical_failures)} critical failure(s) - "
                  + _join("", [_short(c) for c in critical_failures])
                  + ". The submission cannot be onboarded in its current form.")
    elif other_failures:
        status = "PENDING"
        triggered = [c.check_id for c in other_failures]
        reason = (f"Held for vendor action on {len(other_failures)} failed check(s) - "
                  + _join("", [_short(c) for c in other_failures]) + ".")
    elif reviews:
        status = "PENDING"
        triggered = [c.check_id for c in reviews]
        reason = (f"Held for manual review on {len(reviews)} item(s) - "
                  + _join("", [_short(c) for c in reviews])
                  + ". These are close matches rather than clear mismatches, so they are "
                    "routed to a human reviewer instead of being auto-rejected.")
    else:
        status = "APPROVED"
        triggered = []
        reason = (f"All {counts['passed']} applicable checks passed: documents complete, "
                  f"identifiers valid, cross-document data consistent and licence in force.")

    return Decision(status=status, reason=reason, triggered_by=triggered, **counts)


def _join(prefix: str, names: List[str]) -> str:
    return prefix + "; ".join(names)


def _short(check: CheckResult) -> str:
    """First sentence of the finding, so the reason reads as a finding, not a label."""
    first = (check.message or check.name).split(". ")[0].strip().rstrip(".")
    return f"{check.check_id}: {first}"


def required_actions(checks: List[CheckResult]) -> List[str]:
    """De-duplicated, ordered list of what the vendor (or reviewer) must do."""
    actions: List[str] = []
    for c in checks:
        if c.status in ("FAIL", "WARN") and c.required_action and c.required_action not in actions:
            actions.append(c.required_action)
    return actions


def summarise(checks: List[CheckResult]) -> Tuple[List[CheckResult], List[CheckResult], List[CheckResult]]:
    passed = [c for c in checks if c.status == "PASS"]
    failed = [c for c in checks if c.status == "FAIL"]
    review = [c for c in checks if c.status == "WARN"]
    return passed, failed, review