"""
Streamlit UI for the AI Vendor Onboarding & Verification Engine.

This is a thin client: every decision, check, and piece of evidence shown
here comes straight from the FastAPI backend (app/api.py). No business logic
lives in this file - it only calls endpoints and renders what comes back.

Run with:
    python -m streamlit run app/streamlit_app.py

The backend must already be running (python -m uvicorn app.api:app --reload --port 8000).
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import streamlit as st

DEFAULT_API_BASE = os.getenv("VOE_API_BASE", "http://127.0.0.1:8000")

STEP_ICON = {"ok": "\u2705", "warn": "\u26a0\ufe0f", "fail": "\u274c"}

DOC_SKIP_FIELDS = {"source_file", "declared_slot", "document_type", "raw_text",
                   "warnings", "classification_confidence", "classification_method",
                   "extraction_method"}

st.set_page_config(page_title="Vendor Onboarding & Verification Engine",
                   page_icon="\U0001F4C2", layout="wide")


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------
def call_api(method: str, path: str, **kwargs) -> tuple[Optional[Any], Optional[str]]:
    base = st.session_state.get("api_base", DEFAULT_API_BASE).rstrip("/")
    url = f"{base}{path}"
    try:
        resp = requests.request(method, url, timeout=120, **kwargs)
        resp.raise_for_status()
        return resp.json(), None
    except requests.exceptions.ConnectionError:
        return None, (f"Cannot reach the API at {url}. Start it with "
                      f"`python -m uvicorn app.api:app --reload --port 8000` and retry.")
    except requests.exceptions.HTTPError as exc:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            pass
        return None, f"API error {resp.status_code}: {detail or exc}"
    except requests.exceptions.RequestException as exc:
        return None, str(exc)


@st.cache_data(ttl=30, show_spinner=False)
def load_samples(api_base: str):
    return call_api("GET", "/samples")


@st.cache_data(ttl=30, show_spinner=False)
def load_vendors(api_base: str):
    return call_api("GET", "/vendors")


# ---------------------------------------------------------------------------
# Normalisation: /verify* returns a RunResult shape, /runs/{id} returns the
# flatter shape db.get_run() builds from the audit tables. Both are rendered
# by the same functions below, so we reconcile the two here once.
# ---------------------------------------------------------------------------
def normalize_result(raw: Dict[str, Any], from_history: bool) -> Dict[str, Any]:
    if from_history:
        documents = [{**d, "classification_confidence": d.get("confidence"),
                     "populated_fields": d.get("fields", {})}
                    for d in raw.get("documents", [])]
        return {
            "run_id": raw.get("id"),
            "vendor_id": raw.get("vendor_id"),
            "vendor_name": raw.get("vendor_name"),
            "decision": {"status": raw.get("status"), "reason": raw.get("reason"),
                        "passed": raw.get("passed"), "failed": raw.get("failed"),
                        "review": raw.get("review"), "skipped": raw.get("skipped")},
            "checks": raw.get("checks", []),
            "documents": documents,
            "steps": raw.get("steps", []),
            "explanation": raw.get("explanation", ""),
            "required_actions": raw.get("required_actions", []),
            "llm_used": bool(raw.get("llm_used")),
        }
    documents = []
    for d in raw.get("documents", []):
        populated = {k: v for k, v in d.items() if k not in DOC_SKIP_FIELDS and v not in (None, "")}
        documents.append({**d, "populated_fields": populated})
    return {
        "run_id": raw.get("run_id"),
        "vendor_id": raw.get("vendor_id"),
        "vendor_name": raw.get("vendor_name"),
        "decision": raw.get("decision", {}),
        "checks": raw.get("checks", []),
        "documents": documents,
        "steps": raw.get("steps", []),
        "explanation": raw.get("explanation", ""),
        "required_actions": raw.get("required_actions", []),
        "llm_used": bool(raw.get("llm_used")),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_pipeline_steps(steps: List[Dict[str, Any]], final_status: str, animate: bool) -> None:
    state = "error" if final_status == "REJECTED" else "complete"
    with st.status("Pipeline execution", expanded=True) as box:
        for step in steps:
            icon = STEP_ICON.get(step.get("status"), "\u2022")
            st.write(f"{icon} **{step['name']}** \u2014 {step['detail']} "
                    f"({step.get('duration_ms', 0)} ms)")
            if animate:
                time.sleep(0.12)
        box.update(label=f"Pipeline complete \u2014 {final_status}", state=state, expanded=True)


def render_decision(decision: Dict[str, Any], vendor_id: str, vendor_name: Optional[str]) -> None:
    status = decision.get("status", "UNKNOWN")
    header = f"**{status}** \u2014 {vendor_id} ({vendor_name or 'unknown'})"
    body = f"{header}\n\n{decision.get('reason', '')}"
    if status == "APPROVED":
        st.success(body)
    elif status == "PENDING":
        st.warning(body)
    else:
        st.error(body)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Passed", decision.get("passed", 0))
    c2.metric("Failed", decision.get("failed", 0))
    c3.metric("Needs review", decision.get("review", 0))
    c4.metric("Not applicable", decision.get("skipped", 0))


def render_checks(checks: List[Dict[str, Any]]) -> None:
    st.subheader("Checks performed")
    if not checks:
        st.write("No checks recorded.")
        return
    df = pd.DataFrame(checks)
    cols = [c for c in ["check_id", "name", "category", "status", "severity", "message"]
           if c in df.columns]
    st.dataframe(df[cols], use_container_width=True, hide_index=True)

    flagged = [c for c in checks if c.get("status") in ("FAIL", "WARN") and c.get("evidence")]
    if flagged:
        st.markdown("**Evidence for failed / review items**")
        for c in flagged:
            with st.expander(f"{c['check_id']} \u2014 {c['name']} ({c['status']})"):
                st.json(c["evidence"])
                if c.get("required_action"):
                    st.caption(f"Required action: {c['required_action']}")


def render_documents(documents: List[Dict[str, Any]]) -> None:
    st.subheader("Extracted documents")
    if not documents:
        st.write("No documents were processed.")
        return
    for d in documents:
        label = f"{d.get('source_file', '?')} \u2014 classified as {d.get('document_type', 'unknown')}"
        with st.expander(label):
            m1, m2, m3 = st.columns(3)
            conf = d.get("classification_confidence")
            m1.metric("Classification confidence", f"{conf:.2f}" if conf is not None else "n/a")
            m2.metric("Classification method", d.get("classification_method", "n/a"))
            m3.metric("Extraction method", d.get("extraction_method", "n/a"))
            fields = d.get("populated_fields") or {}
            if fields:
                st.table(pd.DataFrame(sorted(fields.items()), columns=["field", "value"]))
            else:
                st.write("No fields were extracted from this document.")


def render_actions_and_note(required_actions: List[str], explanation: str) -> None:
    st.subheader("Required action")
    if required_actions:
        for i, action in enumerate(required_actions, 1):
            st.markdown(f"{i}. {action}")
    else:
        st.write("None. Vendor may be activated for transacting.")

    with st.expander("Full report and draft vendor note"):
        st.text(explanation)


def render_run(norm: Dict[str, Any], steps_animate: bool = False) -> None:
    if norm["steps"]:
        render_pipeline_steps(norm["steps"], norm["decision"].get("status", ""), animate=steps_animate)
    render_decision(norm["decision"], norm["vendor_id"], norm["vendor_name"])
    if norm.get("llm_used") is not None:
        st.caption(f"Extraction path used: {'LLM' if norm['llm_used'] else 'rule-based (no LLM configured)'}")
    render_checks(norm["checks"])
    render_documents(norm["documents"])
    render_actions_and_note(norm["required_actions"], norm["explanation"])


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("Vendor Onboarding Engine")
    st.session_state.setdefault("api_base", DEFAULT_API_BASE)
    st.session_state["api_base"] = st.text_input("Backend API base URL", st.session_state["api_base"])

    health, err = call_api("GET", "/health")
    if err:
        st.error("Backend unreachable")
        st.caption(err)
    else:
        st.success("Backend connected")
        st.caption(f"LLM configured: {'yes' if health.get('llm_configured') else 'no (rule-based fallback active)'}")

    with st.expander("Architecture"):
        st.markdown(
            "- **AI** classifies documents, extracts fields, and phrases the "
            "vendor-facing note.\n"
            "- **Deterministic code** owns every check and the final "
            "APPROVED / PENDING / REJECTED decision.\n"
            "- The decision can never be changed by the AI layer, and every "
            "run is written to the audit trail in full."
        )
    st.caption("All bundled vendor data is synthetic and created for this prototype.")


# ---------------------------------------------------------------------------
# Main tabs
# ---------------------------------------------------------------------------
tab_verify, tab_history, tab_eval = st.tabs(
    ["Verify a vendor", "Run history", "Evaluation"])

# --- Verify a vendor -------------------------------------------------------
with tab_verify:
    st.header("Verify a vendor submission")

    mode = st.radio("Source", ["Use a bundled sample", "Upload a packet"], horizontal=True)

    if mode == "Use a bundled sample":
        samples, err = load_samples(st.session_state["api_base"])
        if err:
            st.error(err)
        else:
            sub = samples.get("samples", [])
            if not sub:
                st.info("No bundled sample submissions found. Run `python tools/generate_documents.py` first.")
            else:
                options = {f"{s['vendor_id']} \u2014 {s.get('scenario', '')}": s["vendor_id"] for s in sub}
                choice = st.selectbox("Sample vendor", list(options.keys()))
                vendor_id = options[choice]
                if st.button("Run verification", type="primary"):
                    with st.spinner(f"Running the pipeline for {vendor_id}..."):
                        result, err = call_api("POST", f"/verify/sample/{vendor_id}")
                    if err:
                        st.error(err)
                    else:
                        st.session_state["last_run"] = normalize_result(result, from_history=False)
                        st.session_state["last_run_animate"] = True
    else:
        vendor_id = st.text_input("Vendor ID (must exist in the master registry for full checks)",
                                  value="V001")
        uploaded = st.file_uploader("Upload PDF documents", type=["pdf"], accept_multiple_files=True)
        slots = st.text_input(
            "Declared slots, comma-separated, one per file in upload order (optional \u2014 "
            "leave blank to infer from filename)")
        if st.button("Run verification", type="primary", disabled=not uploaded):
            files = [("files", (f.name, f.getvalue(), "application/pdf")) for f in uploaded]
            data = {"vendor_id": vendor_id}
            if slots.strip():
                data["slots"] = slots.strip()
            with st.spinner("Running the pipeline..."):
                result, err = call_api("POST", "/verify", files=files, data=data)
            if err:
                st.error(err)
            else:
                st.session_state["last_run"] = normalize_result(result, from_history=False)
                st.session_state["last_run_animate"] = True

    vendors, verr = load_vendors(st.session_state["api_base"])
    if not verr:
        with st.expander("Vendor master registry (synthetic ground truth)"):
            vdf = pd.DataFrame(vendors.get("vendors", []))
            cols = [c for c in ["vendor_id", "legal_name", "pan", "gstin", "industry",
                                "license_expiry"] if c in vdf.columns]
            if not vdf.empty:
                st.dataframe(vdf[cols], use_container_width=True, hide_index=True)

    st.divider()
    if "last_run" in st.session_state:
        render_run(st.session_state["last_run"], steps_animate=st.session_state.pop("last_run_animate", False))
    else:
        st.info("Run a verification above to see the decision, evidence, and audit report.")

# --- Run history -------------------------------------------------------
with tab_history:
    st.header("Run history")
    col1, col2 = st.columns([3, 1])
    vendor_filter = col1.text_input("Filter by vendor ID (optional)", key="history_filter")
    limit = col2.number_input("Max rows", min_value=1, max_value=500, value=50, step=10)

    params = {"limit": limit}
    if vendor_filter.strip():
        params["vendor_id"] = vendor_filter.strip()
    runs, err = call_api("GET", "/runs", params=params)

    if err:
        st.error(err)
    else:
        rows = runs.get("runs", [])
        if not rows:
            st.info("No runs recorded yet. Verify a vendor first.")
        else:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            st.subheader("Inspect a run")
            run_id = st.number_input("Run ID", min_value=1, step=1)
            if st.button("Load run detail"):
                detail, derr = call_api("GET", f"/runs/{int(run_id)}")
                if derr:
                    st.error(derr)
                else:
                    st.session_state["history_run"] = normalize_result(detail, from_history=True)

    if "history_run" in st.session_state:
        st.divider()
        render_run(st.session_state["history_run"], steps_animate=False)

# --- Evaluation -------------------------------------------------------
with tab_eval:
    st.header("Evaluation against ground truth")
    st.write(
        "Re-runs every bundled scenario and scores the engine's decisions and "
        "field extraction against the values the generator originally printed "
        "onto the PDFs, including the deliberately corrupted ones."
    )
    persist = st.checkbox("Also write these runs to the audit trail", value=False)
    if st.button("Run evaluation suite", type="primary"):
        with st.spinner("Re-running every bundled scenario..."):
            report, err = call_api("POST", "/evaluate", params={"persist": persist})
        if err:
            st.error(err)
        else:
            st.session_state["eval_report"] = report

    if "eval_report" in st.session_state:
        report = st.session_state["eval_report"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Decision accuracy", f"{report['decision_accuracy']:.0%}")
        c2.metric("Decision + reason accuracy", f"{report['decision_and_reason_accuracy']:.0%}")
        c3.metric("Extraction accuracy", f"{report['extraction_accuracy']:.0%}",
                  help=f"{report['extraction_fields_checked']} fields checked")

        st.subheader("Per-vendor results")
        ddf = pd.DataFrame(report["decisions"])
        cols = [c for c in ["vendor_id", "scenario", "expected", "predicted",
                            "status_correct", "reason_correct"] if c in ddf.columns]
        st.dataframe(ddf[cols], use_container_width=True, hide_index=True)

        by_doc = report.get("extraction_by_document", {})
        if by_doc:
            st.subheader("Extraction accuracy by document type")
            st.bar_chart(pd.Series(by_doc, name="accuracy"))

        errors = report.get("extraction_errors", [])
        if errors:
            with st.expander(f"{len(errors)} field extraction mismatch(es)"):
                st.dataframe(pd.DataFrame(errors), use_container_width=True, hide_index=True)
        else:
            st.success("No field extraction mismatches.")
    else:
        st.info("Run the evaluation suite to see decision and extraction accuracy.")