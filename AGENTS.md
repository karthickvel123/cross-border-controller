# AGENTS.md — Agent-Ready Repository Contract

> **Version:** 1.0.0 · **Date:** 2026-11-22 · **Track:** Razorpay AI Builder Program — Track 04: AI Finance Controller
> **Purpose:** This document defines the exact repository structure, non-negotiable invariants, test/eval commands, and prohibited edits that any AI coding agent (Antigravity, Copilot, Cursor, Codex, etc.) **MUST** respect when working in this codebase.

---

## Table of Contents

1. [Directory Map](#1-directory-map)
2. [Non-Negotiable Invariants (G1–G5)](#2-non-negotiable-invariants)
3. [Import Boundary Rules](#3-import-boundary-rules)
4. [Exact Test & Eval Commands](#4-exact-test--eval-commands)
5. [Prohibited Edits](#5-prohibited-edits)
6. [File Ownership & Review Gates](#6-file-ownership--review-gates)
7. [Agent Workflow Protocol](#7-agent-workflow-protocol)
8. [Naming Conventions](#8-naming-conventions)
9. [Dependency Management](#9-dependency-management)

---

## 1. Directory Map

```
cross-border-controller/
├── .github/
│   └── workflows/
│       ├── ci.yml                          # Main CI pipeline
│       └── eval.yml                        # Eval harness workflow
├── .importlinter                           # G2 boundary enforcement config
├── .gitleaks.toml                          # Secret scanning config
├── Makefile                                # Top-level make targets
├── docker-compose.yml                      # PostgreSQL 16 + API + UI
├── pyproject.toml                          # Python project config (ruff, mypy, pytest)
├── requirements.txt                        # Pinned production deps
├── requirements-dev.txt                    # Dev/test deps (pytest, hypothesis, etc.)
│
├── ARCHITECTURE.md                         # ← You are here (sibling)
├── AGENTS.md                               # ← This file
│
├── backend/
│   ├── src/
│   │   ├── __init__.py
│   │   ├── main.py                         # FastAPI app entrypoint
│   │   ├── config.py                       # Settings (DATA_MODE, DB URL, etc.)
│   │   │
│   │   ├── deterministic_core/             # ★ G2-ISOLATED: Zero LLM imports
│   │   │   ├── __init__.py
│   │   │   ├── recon_engine.py             # Settlement matching & residue detection
│   │   │   ├── fx_fee_separator.py         # FX/Fee/GST integer arithmetic
│   │   │   ├── compliance_router.py        # PA-CB / IFSCA / EDF / EDPMS routing
│   │   │   ├── lrs_tcs_calculator.py       # Section 394(1) TCS slab computation
│   │   │   ├── edf_edpms_generator.py      # Closure pack generation
│   │   │   ├── approval_lock.py            # G4 atomic conditional update
│   │   │   ├── audit_chain.py              # G5 SHA-256 hash chain writer
│   │   │   ├── webhook_verifier.py         # HMAC verification (Issue #135 safe)
│   │   │   ├── pan_redactor.py             # Luhn check + HMAC-SHA256 masking
│   │   │   ├── money.py                    # int64 paise/cents type + operations
│   │   │   └── feters_codes.py             # Reference table for FETERS purpose codes
│   │   │
│   │   ├── llm_layer/                      # Read-only investigation layer
│   │   │   ├── __init__.py
│   │   │   ├── investigator.py             # Residue investigation orchestrator
│   │   │   ├── factset_verifier.py         # Verbatim token verification gate
│   │   │   ├── prompt_builder.py           # XML-tag isolated prompt construction
│   │   │   └── litellm_router.py           # Provider-agnostic LLM routing
│   │   │
│   │   ├── api/                            # FastAPI route handlers
│   │   │   ├── __init__.py
│   │   │   ├── webhooks.py                 # POST /webhooks/razorpay
│   │   │   ├── recon.py                    # GET /recon, POST /recon/trigger
│   │   │   ├── exceptions.py               # GET/POST /exceptions, approval flow
│   │   │   ├── compliance.py               # GET /compliance/status
│   │   │   ├── audit.py                    # GET /audit/chain, /audit/verify
│   │   │   ├── evaluate.py                 # SSE /evaluate stream
│   │   │   └── health.py                   # GET /health
│   │   │
│   │   ├── models/                         # Pydantic v2 strict models
│   │   │   ├── __init__.py
│   │   │   ├── razorpay.py                 # Razorpay API response models
│   │   │   ├── recon.py                    # Recon row models
│   │   │   ├── compliance.py               # Compliance classification models
│   │   │   ├── exceptions.py               # Exception models
│   │   │   └── audit.py                    # Audit event models
│   │   │
│   │   └── db/                             # Database layer
│   │       ├── __init__.py
│   │       ├── connection.py               # Async connection pool (PG) / SQLite WAL
│   │       ├── migrations/                 # Alembic or raw SQL migrations
│   │       │   └── 001_initial.sql         # → mirrors docs/schema.sql
│   │       └── queries/                    # Named SQL queries (no ORM)
│   │           ├── recon.sql
│   │           ├── exceptions.sql
│   │           ├── compliance.sql
│   │           └── audit.sql
│   │
│   └── tests/
│       ├── conftest.py                     # Fixtures, DB setup, seed data
│       ├── test_recon_engine.py
│       ├── test_fx_fee_separator.py
│       ├── test_compliance_router.py
│       ├── test_lrs_tcs_calculator.py
│       ├── test_edf_edpms_generator.py
│       ├── test_approval_lock.py
│       ├── test_audit_chain.py
│       ├── test_webhook_verifier.py
│       ├── test_pan_redactor.py
│       ├── test_factset_verifier.py
│       ├── test_money.py
│       ├── test_api_webhooks.py
│       ├── test_api_exceptions.py
│       └── property/                       # Hypothesis property-based tests
│           ├── test_conservation_of_value.py   # G1
│           └── test_tcs_slab_monotonicity.py
│
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── tailwind.config.ts
│   ├── next.config.js
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx                    # Dashboard
│   │   │   ├── recon/page.tsx              # Recon inspector
│   │   │   ├── exceptions/page.tsx         # Exception queue + approval modal
│   │   │   ├── compliance/page.tsx         # Compliance status
│   │   │   ├── audit/page.tsx              # Hash chain verifier
│   │   │   └── evaluate/page.tsx           # SSE /evaluate stream viewer
│   │   ├── components/
│   │   │   ├── DataModeBanner.tsx           # DATA_MODE=sim|live banner
│   │   │   ├── ApprovalModal.tsx            # Human sign-off modal
│   │   │   ├── AuditChainVerifier.tsx       # Client-side hash verification
│   │   │   └── SSEStream.tsx                # /evaluate SSE consumer
│   │   └── lib/
│   │       ├── api.ts                       # Typed API client
│   │       └── types.ts                     # Shared TypeScript types
│   └── tests/
│       └── ...
│
├── eval/
│   ├── corpus/
│   │   ├── seed_20261122.json
│   │   ├── strata.yaml
│   │   └── generators/
│   │       ├── happy_path.py
│   │       ├── ambiguous_residue.py
│   │       ├── regulatory_failure.py
│   │       └── adversarial.py
│   ├── judges/
│   │   ├── recon_accuracy_judge.py
│   │   ├── compliance_judge.py
│   │   ├── llm_grounding_judge.py
│   │   └── audit_integrity_judge.py
│   ├── artifacts/
│   │   └── .gitkeep
│   ├── configs/
│   │   ├── model_fleet.yaml
│   │   └── eval_config.yaml
│   ├── Makefile
│   └── README.md
│
└── docs/
    ├── schema.sql                          # ★ Canonical DDL (sibling file)
    ├── razorpay_api_reference.md
    ├── rbi_compliance_reference.md
    └── feters_purpose_codes.csv
```

---

## 2. Non-Negotiable Invariants

> [!CAUTION]
> These invariants are **CI-enforced**. Any agent edit that violates these will fail the pipeline. Do NOT attempt workarounds, monkey-patches, or conditional bypasses.

### G1 — Conservation of Value

- **All monetary values are `int64` (Python `int`, SQL `BIGINT`) in the smallest subunit** (paise for INR, cents for USD/EUR/GBP).
- **ZERO `float`, `Decimal`, `NUMERIC`, or `REAL` for any money field.** Not in variables, not in function signatures, not in SQL casts, not in Pydantic models.
- **Any non-zero residue after recon → `EXCEPTION_OPEN`.** The system NEVER silently rounds, truncates to zero, or force-matches a non-zero residue.
- **Pydantic models for money fields use `StrictInt` with validators rejecting `NaN`, `Infinity`, and negative values.**

### G2 — Boundary Isolation

- **`backend/src/deterministic_core/` MUST NEVER import from `llm_layer/`, `litellm`, `openai`, `anthropic`, `groq`, `google.generativeai`, or `google.genai`.**
- Enforced by `.importlinter` configuration checked in CI.
- The reverse direction (llm_layer importing deterministic_core types for FactSet verification) IS allowed.

### G3 — Zero Ungrounded AI Claims

- **Every factual token** (amounts, UTRs, entity IDs, FETERS codes, dates, FX rates) in an LLM response **MUST pass `FactSetVerifier` verbatim matching** against source-of-truth database records.
- **Any single verification failure → entire response is `ABSTAINED_UNGROUNDED`.**
- LLM outputs are **never** written to financial tables (`orders`, `recon_rows`, `settlements`, `compliance_classifications`, `lrs_tcs_events`).
- LLM outputs are stored **only** in `llm_investigations` with explicit `provenance_status`.

### G4 — Atomic Single-Use Approval

- Exception approval uses a **conditional SQL UPDATE**:
  ```sql
  UPDATE exceptions
  SET    approval_status = :new_status,
         approved_by     = :user_id,
         approved_at     = now(),
         approval_token  = NULL
  WHERE  exception_id    = :eid
    AND  approval_token  = :tok
    AND  approval_status = 'PENDING';
  ```
- Application code **MUST assert `cursor.rowcount == 1`** after this UPDATE.
- If `rowcount != 1` → return **HTTP 409 Conflict**. No retry, no fallback.
- The `approval_token` is a UUID4 generated once when the exception is created and consumed (set to NULL) on approval. It is **never reissued**.

### G5 — Tamper-Evident Audit Chain

- `audit_events` is **append-only**. `REVOKE UPDATE, DELETE ON audit_events FROM app_role`.
- Each event's hash: `h_i = SHA256(h_{i-1} || canonical_json(payload) || timestamp_iso8601)`.
- The genesis event (seq=1) uses `h_0 = SHA256('GENESIS')`.
- **No agent may add `UPDATE` or `DELETE` statements targeting `audit_events`.**

---

## 3. Import Boundary Rules

### `.importlinter` Configuration

```ini
[importlinter]
root_packages =
    backend.src

[importlinter:contract:g2_boundary_isolation]
name = G2: Deterministic core must not import LLM layer
type = forbidden
source_modules =
    backend.src.deterministic_core
forbidden_modules =
    backend.src.llm_layer
    litellm
    openai
    anthropic
    groq
    google.generativeai
    google.genai
```

### Quick Check Command

```bash
# Must pass with zero violations
python -m importlinter --config .importlinter
```

---

## 4. Exact Test & Eval Commands

> [!IMPORTANT]
> Agents MUST run these commands **in this exact order** after any code change. All commands must pass before committing.

### 4.1 Lint & Type Check

```bash
# Static analysis (zero warnings policy)
ruff check backend/src/ backend/tests/ --fix
ruff format backend/src/ backend/tests/

# Type checking (strict mode)
mypy backend/src/ --strict --no-error-summary

# Import boundary (G2)
python -m importlinter --config .importlinter

# Security scans
pip-audit --require-hashes -r requirements.txt
gitleaks detect --source . --verbose
```

### 4.2 Unit & Property Tests

```bash
# Full test suite (SQLite WAL fallback for zero-Docker CI)
DATABASE_URL=sqlite:///test.db pytest backend/tests/ -x --tb=short -q

# Property-based tests only (G1 conservation, TCS monotonicity)
DATABASE_URL=sqlite:///test.db pytest backend/tests/property/ -x --tb=short -q

# With PostgreSQL (Docker required)
docker compose up -d db
DATABASE_URL=postgresql://ctrl:ctrl@localhost:5432/controller \
  pytest backend/tests/ -x --tb=short -q
```

### 4.3 Evaluation Harness

```bash
# Spend-free dry run (no LLM API calls)
DATA_MODE=sim make -C eval eval-dry SEED=20261122

# Full eval with live LLM
DATA_MODE=live make -C eval eval-live SEED=20261122

# Model fleet comparison
DATA_MODE=live make -C eval eval-fleet SEED=20261122 \
  MODELS="gemini-2.0-flash,llama-3.1-70b,mixtral-8x7b"

# Inspect artifacts
cat eval/artifacts/run_20261122.jsonl | python -m json.tool
```

### 4.4 End-to-End Smoke Test

```bash
# Start full stack
docker compose up -d

# Health check
curl -f http://localhost:8000/health

# Seed & run recon
curl -X POST http://localhost:8000/recon/trigger \
  -H "Content-Type: application/json" \
  -d '{"seed": "20261122", "mode": "sim"}'

# Verify audit chain integrity
curl http://localhost:8000/audit/verify | python -m json.tool

# Teardown
docker compose down -v
```

### 4.5 CI Gate Summary

| Gate | Command | Must Pass |
|---|---|---|
| Lint | `ruff check` | Zero warnings |
| Types | `mypy --strict` | Zero errors |
| Boundary | `importlinter` | Zero violations |
| Security | `pip-audit && gitleaks detect` | Zero findings |
| Tests | `pytest -x` | All pass |
| Eval (dry) | `make eval-dry SEED=20261122` | 100% clean match, 0% false match |

---

## 5. Prohibited Edits

> [!CAUTION]
> Any AI agent operating in this repository MUST treat the following as **hard errors**. If a task requires any of these changes, the agent MUST refuse and explain why.

### 5.1 Absolutely Prohibited

| # | Prohibited Edit | Reason |
|---|---|---|
| **P1** | Adding `float`, `Decimal`, `NUMERIC`, or `REAL` for any monetary value | Violates G1 (Conservation of Value) |
| **P2** | Importing `litellm`, `openai`, `anthropic`, `groq`, or `google.genai` inside `deterministic_core/` | Violates G2 (Boundary Isolation) |
| **P3** | Writing LLM output to `orders`, `recon_rows`, `settlements`, `compliance_classifications`, or `lrs_tcs_events` tables | Violates G3 (Zero Ungrounded AI Claims) |
| **P4** | Adding `UPDATE` or `DELETE` SQL targeting `audit_events` | Violates G5 (Tamper-Evident Audit Chain) |
| **P5** | Removing or weakening the `FactSetVerifier` verbatim check | Violates G3 |
| **P6** | Removing `hmac.compare_digest` or replacing with `==` for signature comparison | Introduces timing attack |
| **P7** | Catching exceptions from `bytes.fromhex()` and proceeding to HMAC comparison | Re-introduces Issue #135 |
| **P8** | Removing `rowcount == 1` assertion from approval flow | Violates G4 (Atomic Approval) |
| **P9** | Adding `ON CONFLICT ... DO UPDATE` or `UPSERT` to `audit_events` | Violates G5 append-only property |
| **P10** | Silently rounding a non-zero residue to zero to force a match | Violates G1; masks financial discrepancy |
| **P11** | Inferring FX rate from net credited INR (gross minus fees) | Conflates FX with fee/GST; produces wrong rate |
| **P12** | Allowing inward/outward netting (`netting_links > 0`) | Violates RBI PA-CB regulation |
| **P13** | Bypassing the ₹25L PA-CB cap check | Violates RBI PA-CB regulation |
| **P14** | Removing the `NRI_NRO` / `CORPORATE_FEMA` 0% TCS exemption | Violates Section 394(1) |
| **P15** | Adding `AUTO_APPROVE` or any path that approves exceptions without human sign-off | Violates G4 intent (human-in-the-loop) |

### 5.2 Restricted Edits (Require Justification)

| # | Edit | Required Justification |
|---|---|---|
| **R1** | Changing fee percentages (3%, 1%, 18% GST) | Link to official Razorpay rate card update |
| **R2** | Changing LRS TCS thresholds or slab rates | Link to Finance Act amendment / CBDT notification |
| **R3** | Changing PA-CB cap from ₹25,00,000 | Link to RBI circular |
| **R4** | Adding a new LLM provider | Must route through LiteLLM; must pass FactSet gate |
| **R5** | Modifying `schema.sql` CHECK constraints | Must update both `docs/schema.sql` and migration file |
| **R6** | Adding new columns to financial tables | Must be `NOT NULL` with a default or added via migration |

---

## 6. File Ownership & Review Gates

| Path Pattern | Owner | Review Gate |
|---|---|---|
| `backend/src/deterministic_core/**` | Finance Core Team | 2 approvals; mandatory `importlinter` check |
| `backend/src/llm_layer/**` | AI/ML Team | 1 approval; mandatory FactSet test coverage |
| `backend/src/api/**` | Backend Team | 1 approval |
| `docs/schema.sql` | Finance Core + DBA | 2 approvals; migration must match |
| `eval/**` | Eval Team | 1 approval; seed corpus changes require justification |
| `.importlinter` | Finance Core Team | 2 approvals; boundary changes are architecture decisions |
| `.github/workflows/**` | DevOps | 1 approval |
| `frontend/**` | Frontend Team | 1 approval |

---

## 7. Agent Workflow Protocol

### 7.1 Before Making Any Change

1. **Read this file (`AGENTS.md`) completely.**
2. **Identify which directory the change touches** and verify it's not in the prohibited list (§5).
3. **If the change touches `deterministic_core/`:** verify zero LLM imports will result.
4. **If the change involves money:** verify `int64`/`BIGINT` is used throughout.

### 7.2 After Making Any Change

1. Run the full lint/type/boundary suite (§4.1).
2. Run the full test suite (§4.2).
3. Run `make eval-dry SEED=20261122` (§4.3).
4. If all pass, commit with a message referencing the invariants upheld:
   ```
   feat(recon): add AED currency support [G1-verified, G2-clean]
   ```

### 7.3 When Uncertain

- **If a task seems to require violating an invariant:** STOP. Explain the conflict to the human operator. Do NOT attempt a workaround.
- **If a regulatory rule is ambiguous:** Flag it as `TODO(regulatory): <question>` and proceed with the more conservative interpretation.
- **If a test fails after your change:** Fix the root cause. Do NOT modify the test to pass unless the test itself is wrong (and explain why).

---

## 8. Naming Conventions

| Element | Convention | Example |
|---|---|---|
| Python modules | `snake_case` | `fx_fee_separator.py` |
| Python classes | `PascalCase` | `ReconEngine` |
| Python functions | `snake_case` | `separate_fx_and_fees()` |
| Money variables | `*_paise` or `*_cents` suffix | `gross_txn_paise`, `fee_paise` |
| SQL tables | `snake_case`, plural | `recon_rows`, `audit_events` |
| SQL columns | `snake_case` | `base_amount_paise` |
| Pydantic models | `PascalCase` + `Schema` suffix for API, plain for internal | `ReconRowSchema`, `ReconRow` |
| TypeScript types | `PascalCase` | `ReconRow`, `Exception` |
| React components | `PascalCase` | `ApprovalModal.tsx` |
| Environment variables | `SCREAMING_SNAKE_CASE` | `DATA_MODE`, `DATABASE_URL` |
| Test files | `test_<module>.py` | `test_recon_engine.py` |
| Eval judges | `<domain>_judge.py` | `recon_accuracy_judge.py` |

---

## 9. Dependency Management

### 9.1 Python (Backend)

- **All dependencies pinned with hashes** in `requirements.txt`.
- **Dev dependencies** in `requirements-dev.txt` (pytest, hypothesis, ruff, mypy, import-linter, pip-audit, gitleaks).
- **Adding a dependency:** Add to `requirements.in`, run `pip-compile --generate-hashes`, commit both files.
- **LLM-related packages (`litellm`):** Allowed ONLY in `requirements.txt`, never imported in `deterministic_core/`.

### 9.2 Node.js (Frontend)

- **Lock file:** `package-lock.json` committed.
- **No `--force` or `--legacy-peer-deps`** without justification.

### 9.3 Prohibited Global State

- **No module-level mutable state** in `deterministic_core/`. All functions are pure or accept explicit DB connections.
- **No `functools.lru_cache` on functions that touch money.** Caching stale FX rates or fee schedules is a correctness bug.

---

> **End of AGENTS.md**
