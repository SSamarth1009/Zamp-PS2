"""
SQLite audit trail.

Every run is written in full: the decision, every check with its evidence,
every extracted document with the method used to read it, and the pipeline
step timeline. That means any historical decision can be re-explained months
later without re-running the model - which is the whole point of an audit
trail in a procurement context.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from app.config import DB_PATH
from app.schemas import RunResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id     TEXT NOT NULL,
    vendor_name   TEXT,
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL,
    reason        TEXT,
    passed        INTEGER, failed INTEGER, review INTEGER, skipped INTEGER,
    llm_used      INTEGER DEFAULT 0,
    duration_ms   INTEGER DEFAULT 0,
    explanation   TEXT,
    vendor_note   TEXT,
    actions_json  TEXT
);
CREATE TABLE IF NOT EXISTS run_checks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq           INTEGER,
    check_id      TEXT, name TEXT, category TEXT, status TEXT, severity TEXT,
    message       TEXT, evidence_json TEXT, required_action TEXT
);
CREATE TABLE IF NOT EXISTS run_documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    source_file   TEXT, declared_slot TEXT, document_type TEXT,
    confidence    REAL, classification_method TEXT, extraction_method TEXT,
    fields_json   TEXT
);
CREATE TABLE IF NOT EXISTS run_steps (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq           INTEGER, name TEXT, status TEXT, detail TEXT, duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_vendor ON runs(vendor_id);
"""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def save_run(result: RunResult) -> int:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO runs (vendor_id, vendor_name, created_at, status, reason,
                                 passed, failed, review, skipped, llm_used, duration_ms,
                                 explanation, vendor_note, actions_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (result.vendor_id, result.vendor_name, result.started_at, result.decision.status,
             result.decision.reason, result.decision.passed, result.decision.failed,
             result.decision.review, result.decision.skipped, int(result.llm_used),
             sum(s.duration_ms for s in result.steps), result.explanation, "",
             json.dumps(result.required_actions)))
        run_id = int(cur.lastrowid)

        conn.executemany(
            """INSERT INTO run_checks (run_id, seq, check_id, name, category, status, severity,
                                       message, evidence_json, required_action)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [(run_id, i, c.check_id, c.name, c.category, c.status, c.severity, c.message,
              json.dumps(c.evidence), c.required_action) for i, c in enumerate(result.checks)])

        conn.executemany(
            """INSERT INTO run_documents (run_id, source_file, declared_slot, document_type,
                                          confidence, classification_method, extraction_method,
                                          fields_json)
               VALUES (?,?,?,?,?,?,?,?)""",
            [(run_id, d.source_file, d.declared_slot, d.document_type,
              d.classification_confidence, d.classification_method, d.extraction_method,
              json.dumps(d.populated())) for d in result.documents])

        conn.executemany(
            """INSERT INTO run_steps (run_id, seq, name, status, detail, duration_ms)
               VALUES (?,?,?,?,?,?)""",
            [(run_id, i, s.name, s.status, s.detail, s.duration_ms)
             for i, s in enumerate(result.steps)])
    result.run_id = run_id
    return run_id


def list_runs(limit: int = 50, vendor_id: Optional[str] = None) -> List[Dict[str, Any]]:
    init_db()
    sql = ("SELECT id AS run_id, vendor_id, vendor_name, created_at, status, failed, review, "
           "passed, duration_ms, llm_used FROM runs")
    params: list = []
    if vendor_id:
        sql += " WHERE vendor_id = ?"
        params.append(vendor_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["issues"] = (d.pop("failed") or 0) + (d.pop("review") or 0)
        out.append(d)
    return out


def get_run(run_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    with connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            return None
        checks = conn.execute(
            "SELECT * FROM run_checks WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()
        docs = conn.execute(
            "SELECT * FROM run_documents WHERE run_id = ?", (run_id,)).fetchall()
        steps = conn.execute(
            "SELECT * FROM run_steps WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()
    payload = dict(run)
    payload["required_actions"] = json.loads(payload.pop("actions_json") or "[]")
    payload["checks"] = [{**dict(c), "evidence": json.loads(c["evidence_json"] or "{}")}
                         for c in checks]
    for c in payload["checks"]:
        c.pop("evidence_json", None)
    payload["documents"] = [{**dict(d), "fields": json.loads(d["fields_json"] or "{}")}
                            for d in docs]
    for d in payload["documents"]:
        d.pop("fields_json", None)
    payload["steps"] = [dict(s) for s in steps]
    return payload


def stats() -> Dict[str, Any]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM runs GROUP BY status").fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
    return {"total_runs": total, "by_status": {r["status"]: r["n"] for r in rows}}