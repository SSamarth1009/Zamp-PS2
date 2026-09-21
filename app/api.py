"""
FastAPI backend.

The UI is a thin client over these endpoints, so the same engine is reachable
from a browser, from curl, or from another system (an ERP or a ticketing tool)
without change.

    uvicorn app.api:app --reload --port 8000
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app import db, evaluate as evaluation, llm, pipeline
from app.config import DOCUMENT_LABELS, REQUIRED_DOCUMENTS

app = FastAPI(
    title="AI Vendor Onboarding & Verification Engine",
    version="1.0.0",
    description=("Takes a vendor document packet, extracts it with AI, validates it with "
                 "deterministic rules and returns APPROVED / PENDING / REJECTED with full "
                 "evidence. All bundled data is synthetic."),
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/health")
def health():
    return {"status": "ok", "llm_configured": llm.available(),
            "required_documents": REQUIRED_DOCUMENTS}


@app.get("/vendors")
def vendors():
    """The synthetic master registry (the prototype's source of truth)."""
    return {"vendors": pipeline.load_master()}


@app.get("/samples")
def samples():
    """Bundled synthetic submission packets, with the scenario each one demonstrates."""
    return {"samples": pipeline.load_submissions()}


@app.post("/verify/sample/{vendor_id}")
def verify_sample(vendor_id: str):
    try:
        result = pipeline.run_sample(vendor_id)
    except KeyError:
        raise HTTPException(404, f"No sample packet for vendor {vendor_id}")
    return result.model_dump()


@app.post("/verify")
async def verify(vendor_id: str = Form(...),
                 files: List[UploadFile] = File(...),
                 slots: Optional[str] = Form(None)):
    """
    Verify an uploaded packet.

    `slots` is an optional comma-separated list, one entry per file, naming the
    slot the vendor uploaded each file into. When omitted the slot is inferred
    from the filename. Classification itself never trusts either - it reads the
    document - which is how a wrongly-attached file is detected.
    """
    declared = [s.strip() for s in slots.split(",")] if slots else []
    tmp_dir = Path(tempfile.mkdtemp(prefix="voe_"))
    try:
        packet = []
        for i, upload in enumerate(files):
            target = tmp_dir / (upload.filename or f"file_{i}.pdf")
            with target.open("wb") as fh:
                shutil.copyfileobj(upload.file, fh)
            slot = declared[i] if i < len(declared) else pipeline.infer_slot(target.name)
            packet.append((slot, target))
        result = pipeline.run_pipeline(vendor_id, packet)
        return result.model_dump()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/runs")
def runs(limit: int = 50, vendor_id: Optional[str] = None):
    return {"runs": db.list_runs(limit=limit, vendor_id=vendor_id)}


@app.get("/runs/{run_id}")
def run_detail(run_id: int):
    payload = db.get_run(run_id)
    if not payload:
        raise HTTPException(404, f"Run {run_id} not found")
    return payload


@app.get("/stats")
def stats():
    return db.stats()


@app.get("/document-types")
def document_types():
    return {"required": REQUIRED_DOCUMENTS, "labels": DOCUMENT_LABELS}


@app.post("/evaluate")
def run_evaluation(persist: bool = False):
    """Re-run every bundled scenario and score it against the ground truth."""
    return evaluation.evaluate(persist=persist)