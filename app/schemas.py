"""
Canonical schemas.

The single most important idea in this project: every document layout, no
matter how it is worded, is mapped by the AI layer into ONE flat canonical
document schema. The deterministic validation layer then only ever sees
`CanonicalDocument` objects - it never sees raw PDF text and never sees an
LLM free-text opinion.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CanonicalDocument(BaseModel):
    """One extracted document, normalised into the common schema."""

    source_file: str
    declared_slot: Optional[str] = None      # what the vendor said it was
    document_type: str = "unknown"           # what the classifier decided
    classification_confidence: float = 0.0
    classification_method: str = "heuristic"  # heuristic | llm
    extraction_method: str = "rule"           # rule | llm

    # --- canonical fields (superset across all six document types) ---
    legal_name: Optional[str] = None
    trade_name: Optional[str] = None
    country: Optional[str] = None
    registered_address: Optional[str] = None
    state: Optional[str] = None
    registration_number: Optional[str] = None
    date_of_incorporation: Optional[str] = None
    company_type: Optional[str] = None
    pan: Optional[str] = None
    gstin: Optional[str] = None
    registration_status: Optional[str] = None
    account_holder_name: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc: Optional[str] = None
    industry: Optional[str] = None
    license_number: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None
    issuing_authority: Optional[str] = None
    license_status: Optional[str] = None
    contact_person: Optional[str] = None
    contact_email: Optional[str] = None

    raw_text: str = Field(default="", exclude=True)
    warnings: List[str] = Field(default_factory=list)

    def value(self, field: str) -> Optional[str]:
        return getattr(self, field, None)

    def populated(self) -> Dict[str, Any]:
        skip = {"source_file", "declared_slot", "document_type", "raw_text",
                "warnings", "classification_confidence", "classification_method",
                "extraction_method"}
        return {k: v for k, v in self.model_dump().items()
                if k not in skip and v not in (None, "")}


class CheckResult(BaseModel):
    """Result of ONE deterministic validation rule."""

    check_id: str
    name: str
    category: str                  # completeness | identity | tax | banking | licence | registry
    status: str                    # PASS | FAIL | WARN | SKIPPED
    severity: str = "MAJOR"        # CRITICAL | MAJOR | MINOR
    message: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)
    required_action: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"

    @property
    def review(self) -> bool:
        return self.status == "WARN"


class Decision(BaseModel):
    status: str                    # APPROVED | PENDING | REJECTED
    reason: str
    triggered_by: List[str] = Field(default_factory=list)
    passed: int = 0
    failed: int = 0
    review: int = 0
    skipped: int = 0


class PipelineStep(BaseModel):
    name: str
    status: str                    # ok | warn | fail
    detail: str = ""
    duration_ms: int = 0


class RunResult(BaseModel):
    run_id: Optional[int] = None
    vendor_id: str
    vendor_name: Optional[str] = None
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    decision: Decision
    checks: List[CheckResult]
    documents: List[CanonicalDocument]
    steps: List[PipelineStep]
    explanation: str = ""
    required_actions: List[str] = Field(default_factory=list)
    llm_used: bool = False