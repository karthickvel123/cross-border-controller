# Cross-Border Controller — AI Finance Controller (Track 04)

## 1. One-Line Pitch & Clearly Demonstrable Goal

> **One-Line Pitch:** A deterministic, tamper-evident finance controller executing the **Cross-Border Settlement-to-Compliance loop for Indian Exporters**—pairing exact integer arithmetic with bounded, FactSet-grounded AI to eliminate hallucinations, silent rounding losses, and regulatory drift.

🌐 **Live Hosted Sandbox:** [https://cross-border-controller.onrender.com/](https://cross-border-controller.onrender.com/)  
*(Note: Render free tier spins down on idle; initial cold start may take ~30–50s to bootstrap).*

**The Finance-Ops Loop:**
- **Exact Payout Recon:** Ingests Razorpay settlements across USD, EUR, GBP, SGD; computes exact FX, fees (3% card / 1% MoneySaver), and 18% GST down to the exact paisa.
- **Residue Investigation:** Detects non-zero residues (e.g. SWIFT intermediary deductions, split settlements) and routes them to a bounded, FactSet-grounded AI investigator with 0 ungrounded claims.
- **Regulatory Routing:** Enforces RBI PA-CB ₹25L caps (routing >₹25L to GIFT City IFSCA PSPs), blocks netting, enforces pre-capture EDF/EDPMS export realization timelines, and calculates Section 394(1) LRS TCS slabs.
- **Tamper-Evident Sign-off:** Requires atomic single-use human approval (G4) and commits all lifecycle actions to an append-only SHA-256 cryptographic audit chain (G5).

---

## 2. Live Benchmark Scorecard Table (`SEED=20261122`)

Evaluated on 200 records (110 Happy, 40 Ambiguous, 30 Policy, 20 Adversarial):

| Benchmark Metric | Protected (Deterministic + FactSet) | Ablation Baseline (Controls Disabled) | Safety Gate Status |
|---|---|---|---|
| **Happy Path Exact Paisa Match** | **`100.0%`** (110/110) | `97.27%` (107/110 exact) | ✅ PASS (100.0%) |
| **False Matches (Non-Zero Residue Masked)** | **`0`** | `0` (zero masked residues) | ✅ PASS (0 allowed) |
| **Spurious Float Mismatches (Happy Path)** | **`0`** | `3` (float rounding drift) | ✅ PASS (0 allowed) |
| **Regulatory & Policy Fail-Closed Rate** | **`100.0%`** (30/30) | `0.0%` (30/30 policy misses) | ✅ PASS (100.0%) |
| **Adversarial & Issue #135 Defense Rate** | **`100.0%`** (20/20) | `5.0%` (19/20 crashes & leaks) | ✅ PASS (100.0%) |
| **Ungrounded AI Claim Leak Rate** | **`0.0%`** (0 ungrounded claims) | `24.0%` (48 ungrounded claims leaked) | ✅ PASS (0.0% leak) |
| **FactSet Gate Stress Test (Injections Blocked)** | **`100.0%`** (48/48 caught & blocked) | `0.0%` (0 blocked without gate) | ✅ 100% BLOCKED |
| **Total Discrepancy ₹ at Risk Flagged** | **`₹597,978.14`** honestly in exception queue | `₹0.00` (silently lost / unrouted) | ✅ HONEST QUEUE |
| **p95 Latency (Per-Record)** | **`0.092 ms`** | `0.007 ms` (naive baseline) | |

> Note: The Ablation Baseline is an internal control ablation (measuring impact when Decimal math, regulatory compliance checks, and FactSet gating are removed), not a third-party benchmark.

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
| **Bounded AI** | `backend/src/llm_layer/` | `litellm` (optional), httpx, Pydantic | **Read-Only:** Zero financial writes; XML isolation; G3 gate. |

---

## 4. 3-Command Zero-Key Quick Start

Python 3.12 / 3.13; zero external keys or Docker:

```bash
pip install -r requirements.txt                  # Install core runtime dependencies
pytest backend/tests/ -v                         # 1. Run all 149 tests
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
- The verifier scans both cited fields and free-text rationale via regex for Razorpay IDs (`pay_...`), UTRs, FETERS purpose codes (`P0802`, `S0002`), rupee figures, and foreign currency amounts (`$`, `USD`, `EUR`, `€`, `GBP`, `£`, `SGD`).
- If ANY cited token is absent from the verified `FactSetSnapshot`, the result is rejected as `ABSTAINED_UNGROUNDED`, and the category is forced to `ABSTAINED`.

### G4 — Atomic Single-Use Approval Lock
- Exception resolution requires human sign-off via an approval token.
- Implemented via atomic conditional SQL:
  ```sql
  UPDATE exception_approvals
     SET status = 'APPROVED', reviewed_by = ?, reviewed_at = ?
   WHERE approval_token = ? AND status = 'PENDING' AND expires_at > ?
  ```
- Asserts `rowcount == 1`. Verified under a 20-worker concurrent thread race: exactly 1 worker obtains HTTP 200, while 19 receive HTTP 409 Conflict. Normalized UTC timestamps prevent timezone offset bypasses.

### G5 — Cryptographic SHA-256 Audit Chain
- Every critical system event is committed to `audit_events`.
- Each block stores `prev_hash` and `event_hash = SHA256(prev_hash | event_type | canonical_json | timestamp_iso)`.
- `verify_chain` recomputes and validates the entire chain from genesis `0 * 64`, detecting payload, hash, or event_type tampering immediately with the exact broken sequence number.

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
├── scripts/
│   └── check_boundary.py               # G2 Boundary Isolation enforcement script
├── .github/workflows/ci.yml            # Automated CI workflow
├── Makefile                            # Top-level make targets (test, eval-dry, etc.)
├── EVALUATION.md                       # Complete benchmark evaluation report
├── AGENTS.md                           # Strict repository invariants and contract
└── README.md                           # This document
```

---

## 7. Regulatory References

The deterministic core models and validates compliance against official Indian regulatory and statutory frameworks:

- **RBI Master Direction on Regulation of Payment Aggregators – Cross Border (PA-CB) ([rbi.org.in](https://www.rbi.org.in)):**
  - Enforces the strict ₹25,00,000 (₹25 lakh) per-transaction cap on export and import transactions through PA-CB channels.
  - Prohibits inward/outward netting (`netting_requested=True` fail-closed with `PACB_NO_NETTING_VIOLATION`).
- **IFSCA (Payment Services) Regulations, 2024 ([ifsca.gov.in](https://ifsca.gov.in)):**
  - Transactions exceeding the ₹25L PA-CB threshold or requiring foreign currency account retention are routed to `GIFT_CITY_IFSCA_PSP`.
- **RBI Master Direction on Export of Goods and Services / EDPMS ([rbi.org.in](https://www.rbi.org.in)):**
  - Tracks Export Declaration Form (EDF) and EDPMS realization timelines (270 days for foreign currency invoices, 365 days for INR invoices).
  - Requires pre-capture EDF on ICICI MoneySaver Export accounts before capture.
- **Income-tax Act, 2025 Section 394(1) (FY 2026-27 LRS TCS):**
  - Models the ₹10,00,000 cumulative annual exemption threshold and the 20% TCS slab on remittances exceeding ₹10L.
  - Honors the statutory 0% TCS exemption for NRI remittances from NRO balances up to $1M under FEMA Remittance of Assets Regulations (0% TCS under Income-tax Act, 2025 Section 394(1)).

> **Configurable Policy Constants:** All regulatory limits (₹25L PA-CB cap, ₹10L TCS threshold, 20% TCS rate, 270d/365d EDPMS realization windows) and fee rate cards (3% card fee, 1% MoneySaver fee, 18% GST) are maintained as configurable deterministic constants in `backend/src/deterministic_core/` rather than hardcoded logic, enabling seamless updates as circulars evolve.

---

## 8. Known V1 Limitations

1. **Ephemeral Hosted SQLite:** The hosted Render sandbox runs with an in-memory / local SQLite WAL file that resets on deployment restarts. Production DDL targeting persistent PostgreSQL 16 is fully mapped in `docs/schema.sql`.
2. **Regex-Scoped FactSet Gate:** The G3 FactSet Gate enforces strict exact-match allowlists on Razorpay IDs, UTRs, FETERS codes, and numbers formatted as numerals, decimals, or currency subunits (`₹`, `$`, `USD`, `EUR`, `paise`). It does not currently parse amounts written out purely as English words (e.g. "five lakh rupees").
3. **Hosted Simulation Default (`DATA_MODE=sim`):** By default, the live hosted sandbox operates in deterministic simulation mode (`DATA_MODE=sim`) to provide 24/7 uptime without requiring external third-party API credentials or network dependencies.
