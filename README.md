# Cross-Border Controller — AI Finance Controller (Track 04)

## 1. One-Line Pitch & Clearly Demonstrable Goal

> **One-Line Pitch:** Deterministic, tamper-evident cross-border settlement and regulatory routing controller combining exact integer arithmetic with bounded, FactSet-grounded AI—eliminating hallucinations, silent rounding losses, and regulatory drift.

**Goal:** Reconcile payouts across USD, EUR, GBP, SGD; enforce RBI PA-CB ₹25L caps, route >₹25L to Nov 2026 GIFT City IFSCA PSPs, ban netting, check Oct 2026 EDF/EDPMS (270d FCY/365d INR + ICICI pre-capture EDF), and compute FY 2026-27 Sec 394(1) LRS TCS (20% cliff & NRI exemptions)—flagging 100% of discrepancies into an honest queue with 0 ungrounded claims.

---

## 2. Live Benchmark Scorecard Table (`SEED=20261122`)

Evaluated on 200 records (110 Happy, 40 Ambiguous, 30 Policy, 20 Adversarial):

| Benchmark Metric | Protected (Ours) | Baseline Ablation | Safety Gate |
|---|---|---|---|
| **Happy Path Match** | **`100.0%`** (110/110) | `87.3%` (float drift) | ✅ PASS (100%) |
| **False Matches (Residue Masked)** | **`0`** | `14` (silent truncate) | ✅ PASS (0) |
| **Regulatory & Policy Enforced** | **`100.0%`** (30/30) | `60.0%` (missed caps) | ✅ PASS (100%) |
| **Adversarial & #135 Defense** | **`100.0%`** (20/20) | `0.0%` (crashes/leaks) | ✅ PASS (100%) |
| **Ungrounded AI Claim Leak Rate** | **`0.0%`** (0 leaks) | `4.0%` (injections run) | ✅ PASS (0.0%) |
| **Discrepancy ₹ at Risk** | **`₹597,978.14`** | `₹0.00` (silently lost) | ✅ HONEST QUEUE |
| **p95 Latency (Per-Record)** | **`0.035 ms`** | `1,250 ms` (raw LLM) | ⚡ REAL-TIME |

---

## 3. Deterministic vs. Bounded AI Boundary Table

Enforced via `.importlinter` and CI gates:

| Layer | Path | Allowed Imports | Role & Invariant |
|---|---|---|---|
| **Deterministic Core** | `backend/src/deterministic_core/` | Stdlib, Pydantic, SQLite | **G1:** 100% int64 paise. **G2:** Zero LLM imports. |
| **Security & Webhooks** | `backend/src/verifier/` | Stdlib, hashlib, hmac, re | **Issue #135:** Pre-decode regex guard. PCI/PAN masking. |
| **FactSet Gate** | `backend/src/verifier/factset_gate.py` | Pydantic, re, stdlib | **G3:** Verbatim token matching against FactSet allowlist. |
| **Atomic Approval** | `backend/src/audit_ledger/approval_lock.py` | sqlite3, threading | **G4:** Single-use conditional UPDATE (`rowcount == 1`). |
| **Audit Ledger** | `backend/src/audit_ledger/hash_chain.py` | sqlite3, hashlib, json | **G5:** Tamper-evident SHA-256 chain from `0*64`. |
| **Bounded AI** | `backend/src/llm_layer/` | `litellm` (optional), Pydantic | **Read-Only:** Zero financial writes; XML isolation; G3 gate. |

---

## 4. 3-Command Zero-Key Quick Start

Python 3.12 / 3.13; zero external keys or Docker:

```bash
pytest backend/tests/ -v                         # 1. Run all 141 tests
python -m eval.harness --dry-run --seed 20261122 # 2. Run 200-record eval
uvicorn backend.src.main:app --reload            # 3. Start API + Inspector
```

---

## 5. Architectural Deep-Dive

### G1 — Conservation of Value
- All financial subunits are 64-bit integers (`int` in Python, `INTEGER` / `BIGINT` in SQL).
- Quantization of percentages (e.g. 3% fee, 18% GST) occurs only at the intermediate boundary using `Decimal.quantize(ONE_PAISE, rounding=ROUND_HALF_EVEN)` before immediate conversion back to integer paise.
- Any non-zero discrepancy after settlement reconciliation (`expected_net - actual_bank_credit`) opens an exception (`EXCEPTION_OPEN`). No silent rounding, truncating, or force-matching is permitted.

### G2 — Import Boundary Isolation
- `.importlinter` guarantees that `backend/src/deterministic_core/` never imports `llm_layer`, `litellm`, `openai`, `anthropic`, `groq`, or `google.genai`.
- Financial reconciliation and regulatory decision-making are mathematically deterministic and verifiable without an LLM in the critical path.

### G3 — Verbatim FactSet Provenance Gate
- Every LLM hypothesis passes through `verify_hypothesis_against_factset`.
- The verifier scans both cited fields and free-text rationale via regex for Razorpay IDs (`pay_...`), UTRs, FETERS purpose codes (`P0802`, `S0002`), and explicit rupee figures.
- If ANY cited token is absent from the verified `FactSetSnapshot`, the result is rejected as `ABSTAINED_UNGROUNDED`, and the category is forced to `ABSTAINED`.

### G4 — Atomic Single-Use Approval Lock
- Exception resolution requires human sign-off via an approval token.
- Implemented via atomic conditional SQL:
  ```sql
  UPDATE exception_approvals
     SET status = 'APPROVED', reviewed_by = ?, reviewed_at = ?
   WHERE approval_token = ? AND status = 'PENDING' AND expires_at > ?
  ```
- Asserts `rowcount == 1`. Verified under a 20-worker concurrent thread race: exactly 1 worker obtains HTTP 200, while 19 receive HTTP 409 Conflict.

### G5 — Cryptographic SHA-256 Audit Chain
- Every critical system event is committed to `audit_events`.
- Each block stores `prev_hash` and `event_hash = SHA256(prev_hash | canonical_json | timestamp_iso)`.
- `verify_chain` recomputes and validates the entire chain from genesis `0 * 64`, detecting tampering immediately with the exact broken sequence number.

---

## 6. Directory Structure

```
cross-border-controller/
├── backend/
│   ├── src/
│   │   ├── main.py                     # FastAPI application (5 core endpoints)
│   │   ├── deterministic_core/         # G2-isolated deterministic financial core
│   │   │   ├── fx_fees.py              # Exact Razorpay card & MoneySaver pricing
│   │   │   ├── compliance_router.py    # PA-CB, GIFT City IFSCA, and EDF/EDPMS routing
│   │   │   ├── lrs_tcs.py              # Sec 394(1) LRS TCS 20% slab & NRI exemption
│   │   │   └── schemas.py              # Strict Pydantic v2 integer models
│   │   ├── verifier/                   # Security & verification layer
│   │   │   ├── webhook_security.py     # HMAC-SHA256 verification (Issue #135 fix)
│   │   │   ├── pii_redactor.py         # PCI card Luhn check, CVV, and PAN HMAC masking
│   │   │   └── factset_gate.py         # G3 Verbatim FactSet Provenance Gate
│   │   ├── audit_ledger/               # Ledger & locks
│   │   │   ├── approval_lock.py        # G4 atomic approval lock (SQLite WAL)
│   │   │   └── hash_chain.py           # G5 append-only SHA-256 audit chain
│   │   └── llm_layer/                  # Bounded read-only investigation layer
│   │       └── residue_investigator.py # Residue analyzer with zero-key sim fallback
│   └── tests/
│       ├── unit/                       # Unit tests (fx_fees, compliance, lrs, eval, api)
│       ├── concurrency/                # Concurrency tests (20-thread G4 race, webhooks)
│       └── adversarial/                # Adversarial tests (prompt injection, FactSet gate)
├── eval/
│   ├── corpus_generator.py             # Seeded 200-record cross-border corpus generator
│   ├── harness.py                      # Evaluation harness & scorecard generator
│   └── artifacts/                      # Run JSONL and summary JSON output
├── frontend/
│   └── index.html                      # Zero-build Tailwind CSS Inspector UI
├── .github/workflows/ci.yml            # Automated CI workflow
├── Makefile                            # Top-level make targets (test, eval-dry, etc.)
├── EVALUATION.md                       # Complete benchmark evaluation report
├── AGENTS.md                           # Strict repository invariants and contract
└── README.md                           # This document
```
