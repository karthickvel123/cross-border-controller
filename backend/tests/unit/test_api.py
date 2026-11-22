"""
backend/tests/unit/test_api.py
==============================
Unit tests for FastAPI endpoints in backend/src/main.py.

Verifies:
  1. GET  /health -> 200, status "ok" and data_mode
  2. POST /webhooks/razorpay -> 400 on 1-char signature, 200 on valid signature
  3. GET  /audit/verify -> 200, valid is True
  4. POST /exceptions/{token}/approve -> 200 on first call, 409 on replay
  5. GET  /evaluate/stream -> 200, text/event-stream
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.src.main import app, _get_db, WEBHOOK_SECRET
from backend.src.audit_ledger.approval_lock import create_pending_approval


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_endpoint(client: TestClient):
    """GET /health must return 200 and data_mode."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "data_mode" in data


def test_webhook_malformed_signature(client: TestClient):
    """POST /webhooks/razorpay must return 400 on 1-char signature (Issue #135)."""
    response = client.post(
        "/webhooks/razorpay",
        content=b'{"event":"payment.captured"}',
        headers={"X-Razorpay-Signature": "a"},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["verified"] is False
    assert data["reason"] == "MALFORMED_SIGNATURE_FORMAT"


def test_webhook_valid_signature(client: TestClient):
    """POST /webhooks/razorpay must return 200 on valid HMAC-SHA256 signature."""
    raw_body = b'{"event":"payment.captured","entity":"pay_test_12345"}'
    valid_sig = hmac.new(WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhooks/razorpay",
        content=raw_body,
        headers={"X-Razorpay-Signature": valid_sig},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["verified"] is True
    assert data["reason"] == "VERIFIED"


def test_audit_verify_endpoint(client: TestClient):
    """GET /audit/verify must return 200 with valid hash chain status."""
    response = client.get("/audit/verify")
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["broken_seq"] is None
    assert data["event_count"] >= 1


def test_exception_approval_lock_atomic(client: TestClient):
    """POST /exceptions/{token}/approve returns 200 on first call, 409 on replay."""
    test_token = f"test-token-{uuid.uuid4()}"
    test_entity = "pay_race_test_01"
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    conn = _get_db()
    create_pending_approval(conn, test_token, test_entity, expires_at)
    conn.close()

    # First approval -> 200 APPROVED
    resp1 = client.post(f"/exceptions/{test_token}/approve")
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "APPROVED"

    # Second approval -> 409 CONFLICT (token already consumed)
    resp2 = client.post(f"/exceptions/{test_token}/approve")
    assert resp2.status_code == 409
    assert resp2.json()["status"] == "CONFLICT"


def test_evaluate_stream_endpoint(client: TestClient):
    """GET /evaluate/stream returns 200 with text/event-stream content type."""
    response = client.get("/evaluate/stream")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "data: " in response.text
