"""
backend/src/main.py
===================
FastAPI Application Entrypoint for Cross-Border Controller.

Exposes:
  - GET  /health                                -> Health check & DATA_MODE status
  - POST /webhooks/razorpay                     -> Webhook HMAC verification (Issue #135 fix)
  - GET  /evaluate/stream                       -> SSE stream of 200-record evaluation & scorecard
  - GET  /audit/verify                          -> Verify G5 append-only audit hash chain
  - POST /exceptions/{approval_token}/approve   -> G4 atomic single-use approval lock
  - GET  /exceptions                            -> Open exception queue for frontend inspector
  - GET  /                                      -> Serves frontend inspector UI
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from backend.src.audit_ledger.approval_lock import (
    consume_approval_token,
    create_pending_approval,
    initialise_schema as init_approval_schema,
)
from backend.src.audit_ledger.hash_chain import (
    append_event,
    initialise_schema as init_chain_schema,
    verify_chain,
)
from backend.src.verifier.webhook_security import verify_razorpay_webhook
from eval.corpus_generator import DEFAULT_SEED, generate_corpus
from eval.harness import run_eval_harness

DB_PATH = os.getenv("DATABASE_PATH", "controller_ledger.db")
WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "whsec_test_secret_corp_20261122")
DATA_MODE = os.getenv("DATA_MODE", "sim")

# Demo tokens for the UI
SAMPLE_TOKEN = "token-rec-0042-approval"
SAMPLE_ENTITY = "pay_98765432"


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    init_approval_schema(conn)
    init_chain_schema(conn)
    # Ensure genesis event exists if empty
    count = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    if count == 0:
        append_event(
            conn,
            event_type="SYSTEM_BOOTSTRAP",
            payload={"mode": DATA_MODE, "version": "1.0.0"},
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )
    return conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bootstrap DB schema & seed demo data if empty
    conn = _get_db()
    try:
        # Seed initial pending approval if not present
        row = conn.execute(
            "SELECT COUNT(*) FROM exception_approvals WHERE approval_token = ?",
            (SAMPLE_TOKEN,),
        ).fetchone()
        if row and row[0] == 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
            create_pending_approval(conn, SAMPLE_TOKEN, SAMPLE_ENTITY, expires_at)

        # Seed initial audit chain event if empty
        chain_count = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        if chain_count == 0:
            append_event(
                conn,
                event_type="SYSTEM_BOOTSTRAP",
                payload={"mode": DATA_MODE, "version": "1.0.0"},
                timestamp_iso=datetime.now(timezone.utc).isoformat(),
            )
    finally:
        conn.close()
    yield


app = FastAPI(
    title="Cross-Border Controller API",
    version="1.0.0",
    description="Deterministic Financial Discrepancy & Regulatory Compliance Controller",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 1. Health Check ───────────────────────────────────────────────────────────

@app.get("/health")
def health_check() -> dict[str, str]:
    """Return system health and current operational DATA_MODE."""
    return {"status": "ok", "data_mode": os.getenv("DATA_MODE", "sim")}


# ── 2. Razorpay Webhook Verification ──────────────────────────────────────────

@app.post("/webhooks/razorpay")
async def handle_razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(None, alias="X-Razorpay-Signature"),
) -> JSONResponse:
    """Verify Razorpay webhook HMAC-SHA256 signature (Issue #135 fix).

    Returns exact HTTP status: 503, 400, 401, or 200.
    """
    raw_body = await request.body()
    valid, http_status, reason = verify_razorpay_webhook(
        raw_body=raw_body,
        signature=x_razorpay_signature,
        webhook_secret=WEBHOOK_SECRET,
    )
    return JSONResponse(
        status_code=http_status,
        content={"verified": valid, "reason": reason},
    )


# ── 3. Evaluation SSE Stream ──────────────────────────────────────────────────

@app.get("/evaluate/stream")
async def evaluate_stream() -> StreamingResponse:
    """Stream real-time evaluation progress across the 200-record corpus via SSE."""

    async def event_generator() -> AsyncGenerator[str, None]:
        corpus = generate_corpus(seed=DEFAULT_SEED)
        total = len(corpus)

        # Emit initial event
        yield f"data: {json.dumps({'type': 'start', 'total': total, 'seed': DEFAULT_SEED})}\n\n"

        # Stream progressive batches
        for i, rec in enumerate(corpus, start=1):
            if i % 25 == 0 or i == total:
                yield f"data: {json.dumps({'type': 'progress', 'current': i, 'total': total, 'record_id': rec['record_id'], 'stratum': rec['stratum']})}\n\n"
                await asyncio.sleep(0.01)

        # Run full harness and emit final scorecard
        summary = run_eval_harness(seed=DEFAULT_SEED, dry_run=True)
        yield f"data: {json.dumps({'type': 'summary', 'summary': summary})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 4. Audit Hash-Chain Verification ──────────────────────────────────────────

@app.get("/audit/verify")
def verify_audit_chain() -> dict[str, Any]:
    """Verify cryptographic integrity of the G5 append-only audit hash chain."""
    conn = _get_db()
    try:
        valid, broken_seq, msg = verify_chain(conn)
        count = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        return {
            "valid": valid,
            "broken_seq": broken_seq,
            "message": msg,
            "event_count": count,
        }
    finally:
        conn.close()


# ── 5. G4 Atomic Approval Lock ────────────────────────────────────────────────

@app.post("/exceptions/{approval_token}/approve")
def approve_exception(approval_token: str) -> JSONResponse:
    """Consume a single-use exception approval token atomically.

    Returns 200 on first approval, 409 on replay or expired token.
    """
    conn = _get_db()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        ok, http_status, reason = consume_approval_token(
            conn=conn,
            approval_token=approval_token,
            reviewer="compliance_officer_demo",
            now_iso=now_iso,
        )

        if ok:
            # Append audit event for approval
            append_event(
                conn=conn,
                event_type="EXCEPTION_APPROVED",
                payload={"approval_token": approval_token, "reviewer": "compliance_officer_demo"},
                timestamp_iso=now_iso,
            )

        return JSONResponse(
            status_code=http_status,
            content={"success": ok, "status": "APPROVED" if ok else "CONFLICT", "reason": reason},
        )
    finally:
        conn.close()


# ── Helper for UI: List Open Exceptions ───────────────────────────────────────

@app.get("/exceptions")
def list_exceptions() -> list[dict[str, Any]]:
    """Return active exceptions queue for frontend inspection."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT approval_token, entity_id, status, expires_at, reviewed_by, reviewed_at FROM exception_approvals"
        ).fetchall()
        return [
            {
                "approval_token": r[0],
                "entity_id": r[1],
                "status": r[2],
                "expires_at": r[3],
                "reviewed_by": r[4],
                "reviewed_at": r[5],
                "discrepancy_inr": 18.50,
                "category": "SWIFT_INTERMEDIARY_FEE",
            }
            for r in rows
        ]
    finally:
        conn.close()


# ── Serve Frontend ────────────────────────────────────────────────────────────

@app.get("/")
def serve_index() -> Response:
    """Serve frontend inspector UI if available."""
    index_file = Path("frontend/index.html")
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(
        {"message": "Cross-Border Controller API online. Open frontend/index.html or visit /health."}
    )
