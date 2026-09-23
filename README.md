# AI Vendor Onboarding & Verification Engine

An automated vendor-onboarding analyst. It takes a packet of six vendor
documents, reads them with AI, validates every claim with deterministic
code, and returns **APPROVED / PENDING / REJECTED** with full evidence,
a required-action list, and a draft note back to the vendor — all backed
by a queryable SQLite audit trail.

Built for the **PS-2 — Operations: Vendor Onboarding** take-home case study.
All bundled vendor data, documents and identifiers are **synthetic** and
stamped `DEMO / SYNTHETIC DOCUMENT — NOT VALID FOR OFFICIAL USE`.

---

## 1. Core design principle

> **AI perceives. Code decides.**

| | AI / LLM | Deterministic code |
|---|---|---|
| Does | Classify a document from its content; extract fields into a canonical schema; map arbitrary label vocabulary onto one field set; phrase the vendor-facing explanation | Format & checksum validation; cross-document comparison; registry reconciliation; the final APPROVED/PENDING/REJECTED decision |
| Never does | Decide the outcome | Depend on a model's opinion |

No model output ever reaches the decision function. Given the same set of
checks, the engine always returns the same status — that reproducibility
is what makes a rejection defensible later, to the vendor or to an auditor.

The system runs **with or without an LLM key**. Every AI call is optional;
on failure or absence, each step falls back to a deterministic path
(heuristic classifier, rule-based label-map extractor, template vendor
note), and the run record always states which path was used.

---

## 2. Pipeline

```
files
  → classify            (AI, heuristic fallback)
  → extract              (AI, rule-based fallback)
  → normalise             (deterministic)
  → validate — completeness, tax, identity, banking, licence, registry   (deterministic, 22 rules)
  → decide                (deterministic, severity precedence)
  → explain               (deterministic report + optional AI-phrased note)
  → persist to audit trail
```

`on_step()` fires after every stage, so the same orchestration function
(`pipeline.run_pipeline`) drives the live Streamlit execution view, the
FastAPI response, and the pytest suite — one code path, three surfaces.

---

## 3. Document set

| # | Document | Key check it enables |
|---|---|---|
| 1 | Vendor Application Form | Primary self-declaration — entity, tax, banking, contact |
| 2 | Certificate of Incorporation | Legal identity — registration number, incorporation date |
| 3 | GST Registration Certificate | Tax identity — GSTIN, state, registration status |
| 4 | PAN / Tax ID Document | Independent PAN check, cross-referenced against the GSTIN |
| 5 | Bank Verification Letter | Account holder name **vs. legal entity name** |
| 6 | Industry Licence | Licence validity, expiry, and issuing authority |

---

## 4. Project structure

```
PS2/
├── app/
│   ├── api.py                 FastAPI app — all HTTP endpoints
│   ├── config.py               Central config: paths, LLM settings, thresholds,
│   │                           ID regex formats, severity/decision policy
│   ├── db.py                   SQLite audit trail (schema, save/list/get runs)
│   ├── evaluate.py             Evaluation harness — scores decisions & extraction
│   │                           against ground truth
│   ├── llm.py                  Thin OpenAI-compatible client; provider isolated here
│   ├── normalize.py            Deterministic normalisation & fuzzy-matching primitives
│   │                           (names, addresses, IDs, dates, GSTIN checksum)
│   ├── pipeline.py             Orchestrator — runs every stage in order
│   ├── schemas.py               Pydantic models: CanonicalDocument, CheckResult,
│   │                           Decision, PipelineStep, RunResult
│   ├── streamlit_app.py         Streamlit UI (thin client over the API)
│   │
│   ├── extraction/
│   │   ├── pdf_text.py         Step 0 — PyMuPDF text layer + OCR fallback
│   │   ├── classifier.py        Step 1 — document classification (LLM + heuristic)
│   │   ├── extractor.py        Step 2 — field extraction into canonical schema
│   │   └── normalizer.py       Step 3 — applies normalize.py to a CanonicalDocument
│   │
│   └── validation/
│       ├── rules.py             All 22 deterministic validation rules
│       └── decision.py         Severity precedence → final status + required actions
│
├── tools/
│   ├── build_master.py         Generates data/vendor_master.json (ground truth)
│   └── generate_documents.py   Renders synthetic PDFs + submission/expected-results index
│
├── tests/
│   └── test_engine.py           26 pytest tests: primitives, rules, decision, e2e, evaluation
│
├── data/                        Generated — gitignored, created by the tools above
│   ├── vendor_master.json
│   ├── vendor_submissions.json
│   ├── expected_results.json
│   ├── audit.db
│   └── submissions/
│       └── V001/ … V007/*.pdf
│
├── .env                         VOE_/OPENAI_ environment variables (gitignored)
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 5. Tech stack

| Layer | Tool | Role |
|---|---|---|
| Backend / API | **FastAPI** + **Uvicorn** | One engine, reachable from browser, curl, or another system |
| Frontend | **Streamlit** | Thin client — every value shown is fetched from the API, nothing computed locally |
| Schemas | **Pydantic** | CanonicalDocument / CheckResult / Decision / RunResult typed contracts across every layer |
| PDF ingestion | **PyMuPDF (fitz)** + optional **pytesseract** / **Pillow** | Native text layer first, OCR fallback for scans, never a silent failure |
| Document generation | **ReportLab** | Renders the six synthetic PDF types from the master registry |
| LLM | **openai** (OpenAI-compatible Chat Completions client) | Classification, extraction, vendor-note phrasing — fully optional |
| Storage / audit | **SQLite** (via `sqlite3`) | Every run persisted in full: decision, checks, evidence, documents, step timeline |
| Data client (UI) | **requests**, **pandas** | Streamlit's HTTP calls and tabular rendering |
| Testing | **pytest** | Unit tests on primitives/rules/decision + end-to-end acceptance tests |
| Config | **python-dotenv** | Loads `.env` so uvicorn, Streamlit and pytest all see the same key |

---

## 6. Setup

### 6.1 Prerequisites
- Python 3.11+ (a conda/venv environment is recommended)
- Internet access to PyPI (or an internal mirror) for `pip install`
- An OpenAI-compatible API key (**optional** — the engine runs fully offline without one)

### 6.2 Create the environment and install dependencies

```bash
conda create -n zamp python=3.11 -y
conda activate zamp
pip install -r requirements.txt
```

`requirements.txt`:

```txt
# --- Backend (FastAPI) ---
fastapi==0.115.0
uvicorn[standard]==0.30.6
pydantic==2.9.2
python-dotenv==1.0.1
openai==1.51.0
httpx==0.27.2          # pinned: newer httpx drops the 'proxies' kwarg openai still passes
pymupdf==1.24.10

# --- Frontend (Streamlit) ---
streamlit==1.38.0
pandas==2.2.2
requests==2.32.3

# --- Document generation (tools/) ---
reportlab==4.2.2

# --- Testing ---
pytest==8.3.3

# --- Optional: OCR fallback for scanned PDFs ---
# pytesseract==0.3.13
# Pillow==10.4.0
```

### 6.3 Environment variables (`.env` in the project root)

```env
# Optional — engine runs fully offline (rule-based fallback) without these
OPENAI_API_KEY=sk-...
VOE_LLM_ENABLED=auto            # auto | on | off
VOE_LLM_MODEL=<your-model-name>
OPENAI_BASE_URL=https://api.openai.com/v1

# Optional
VOE_DB_PATH=data/audit.db
VOE_LLM_TIMEOUT=60
VOE_LLM_REASONING_EFFORT=low
VOE_API_BASE=http://127.0.0.1:8000   # picked up by the Streamlit sidebar default
```

`config.py` loads `.env` explicitly, so uvicorn, Streamlit and pytest all
see the same key without extra setup.

> **If your project folder lives inside OneDrive** (common on a managed
> laptop), move it to a plain local path first — e.g. `C:\dev\PS2`.
> OneDrive's file locking/sync can intermittently corrupt or delay reads
> of the SQLite audit file and the generated PDFs.

---

## 7. Initial data generation (run once)

The repo ships with **no data** — `vendor_master.json` and the sample PDFs
are generated, not committed. Run both scripts from the project root
before starting the app:

```bash
python tools/build_master.py
python tools/generate_documents.py
```

- `build_master.py` writes `data/vendor_master.json` — the synthetic
  ground-truth registry for 7 vendors (V001–V007), including a real
  base-36 GSTIN checksum.
- `generate_documents.py` reads that registry, renders the six PDF types
  per vendor with **six different label vocabularies**, deliberately
  injects the defect for each test scenario, and writes
  `data/vendor_submissions.json` + `data/expected_results.json`.

Re-run both any time you want to regenerate a clean dataset (e.g. after
editing a vendor in `build_master.py`).

---

## 8. Running the project

Two processes, in two terminals, both from the project root with the
`zamp` environment active.

**Terminal 1 — backend:**
```bash
python -m uvicorn app.api:app --reload --port 8000
```

**Terminal 2 — frontend:**
```bash
streamlit run app/streamlit_app.py
```

Open the URL Streamlit prints (default `http://localhost:8501`). Confirm
the sidebar shows **"Backend connected"** — if not, check the "Backend
API base URL" field matches where uvicorn is listening
(`http://127.0.0.1:8000` by default).

### Quick sanity checks
```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/samples
```

---

## 9. Using the UI

**Verify a vendor** — pick a bundled sample (V001–V007) or upload a real
packet, click **Run verification**. Watch the 10-step pipeline animate
live, then review the decision banner, the checks table (expand any
FAIL/WARN row for its evidence), extracted document fields, required
actions, and the draft vendor note.

**Run history** — every run is persisted in full. Filter by vendor ID,
load any past run ID, and it re-renders exactly like a live run — pulled
from SQLite, no re-execution needed.

**Evaluation** — re-runs all 7 bundled scenarios against
`expected_results.json` and reports decision accuracy, decision+reason
accuracy (did the *right* issue codes trigger, not just the right label),
and field-level extraction accuracy per document type.

---

## 10. API reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Backend status + whether an LLM is configured |
| GET | `/vendors` | Synthetic master registry |
| GET | `/samples` | Bundled sample submissions (`{"samples": [...]}`) |
| POST | `/verify/sample/{vendor_id}` | Run the pipeline on a bundled sample |
| POST | `/verify` | Run the pipeline on an uploaded packet (`files`, `vendor_id`, optional `slots`) |
| GET | `/runs` | Run history (`limit`, optional `vendor_id`) |
| GET | `/runs/{run_id}` | Full detail for one past run |
| GET | `/stats` | Aggregate counts by decision status |
| GET | `/document-types` | Required document list + display labels |
| POST | `/evaluate` | Re-run every scenario and score it (`persist` optional) |

---

## 11. Validation rules (22 total, `app/validation/rules.py`)

| Category | IDs | Checks |
|---|---|---|
| Completeness | `DOC-001…003`, `FLD-001` | All documents present · upload matches classified type · machine-readable · mandatory fields extracted |
| Tax identity | `TAX-001…005` | PAN format · GSTIN structure + checksum · PAN embedded in GSTIN · GST state-code consistency |
| Legal identity | `NAM-001…002`, `ADR-001`, `REG-001…002` | Name consistent across documents & vs. registry · address consistent · CIN format & cross-document match |
| Banking | `BNK-001…003` | Account holder vs. legal entity (strict) · IFSC/account format · matches registry |
| Licence | `LIC-001…004` | Licence number format · issued to the vendor · not expired · industry matches declaration |
| Registry | `REG-003` | PAN, GSTIN, CIN, licence number reconciled against the master registry |

Every rule returns a structured `CheckResult` (status, severity, message,
evidence, required action) — never free text.

## 12. Decision precedence (`app/validation/decision.py`)

```
≥ 1 CRITICAL check FAILs        → REJECTED
any MAJOR fails / anything WARNs → PENDING
every applicable check passes    → APPROVED
```

~30 lines of boolean logic, no model input. Checked top-down, stops at
the first match — same checks in, same decision out, every time.

---

## 13. Testing

```bash
pytest tests/test_engine.py -v
```

26 tests across three layers: normalisation/matching primitives,
individual rules against a hand-built context, and end-to-end acceptance
tests over all 7 bundled scenarios (including a determinism check and the
full evaluation-suite assertion).

---

## 14. Synthetic test scenarios

| Vendor | Scenario | Expected |
|---|---|---|
| V001 | Clean submission, fully consistent | APPROVED |
| V002 | Industry licence not attached | PENDING |
| V003 | Bank account title ≠ legal entity | PENDING |
| V004 | PAN quoted in a malformed format | REJECTED |
| V005 | Singular/plural legal-name variant | PENDING (review) |
| V006 | Multiple failures at once | REJECTED |
| V007 | Wrong file in the bank-verification slot | PENDING |

---

## 15. Known gotchas

- **`TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`**
  — version mismatch between `openai` and `httpx`. Fixed by the `httpx==0.27.2`
  pin in `requirements.txt`; alternatively `pip install -U openai`.
- **`FileNotFoundError: vendor_master.json`** — the generation scripts in
  §7 haven't been run yet.
- **Streamlit shows "No bundled sample submissions found" after generating
  data** — usually a stale `@st.cache_data` result; use the **⋮ → Clear
  cache** menu, or restart the Streamlit process.
- **Slow or flaky file writes** — see the OneDrive note in §6.3.

---

## 16. Production vs. prototype

This prototype uses a synthetic `vendor_master.json` as the authoritative
source of truth, purely to make the deterministic-validation story
demonstrable end-to-end without real vendor data. In a production
implementation, this layer would be replaced or supplemented by approved
internal procurement systems and external verification services (e.g.
government tax-registry or banking penny-drop verification APIs). No
identifier, company, or document in this repository corresponds to a real
registered entity.
