"""
backend/tests/concurrency/test_security_and_ledger.py
=======================================================
Pytest test suite for:

  1. Webhook HMAC verification (Issue #135 regression + success path).
  2. PII / PAN redactor (Luhn, non-Luhn, Indian PAN, zero-width, HTML comment).
  3. G4 Atomic approval lock — 20-thread concurrency race (exactly 1 winner).
  4. G5 Audit hash-chain — append + verify + tamper detection.

Run with:
    DATABASE_URL=sqlite:///test.db pytest backend/tests/concurrency/test_security_and_ledger.py -x -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import pytest

# ── Modules under test ────────────────────────────────────────────────────────
from backend.src.verifier.webhook_security import verify_razorpay_webhook
from backend.src.verifier.pii_redactor import luhn_check, redact_pci_and_pii
from backend.src.audit_ledger.approval_lock import (
    initialise_schema as init_approval_schema,
    create_pending_approval,
    consume_approval_token,
)
from backend.src.audit_ledger.hash_chain import (
    initialise_schema as init_chain_schema,
    append_event,
    verify_chain,
)


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _make_signature(body: bytes, secret: str) -> str:
    """Compute the correct HMAC-SHA256 hex signature for a webhook body."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _in_memory_conn() -> sqlite3.Connection:
    """Return an in-memory SQLite connection safe for multi-threaded use."""
    return sqlite3.connect(":memory:", check_same_thread=False)


def _now_iso(offset_seconds: int = 0) -> str:
    """Return current UTC time ± *offset_seconds* as ISO-8601."""
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return dt.isoformat()


# ═════════════════════════════════════════════════════════════════════════════
# 1. Webhook HMAC-SHA256 Verification (Issue #135 regression)
# ═════════════════════════════════════════════════════════════════════════════

class TestWebhookSecurity:
    """HMAC verification — covers every code path in webhook_security.py."""

    SECRET = "razorpay_test_secret_xK9mP2"
    BODY   = b'{"event":"payment.captured","payload":{"amount":150000}}'

    # ── Rule 1: unconfigured secret → 503 ─────────────────────────────────────

    def test_none_secret_returns_503(self):
        ok, status, reason = verify_razorpay_webhook(self.BODY, "a" * 64, None)
        assert ok is False
        assert status == 503
        assert reason == "WEBHOOK_SECRET_UNCONFIGURED"

    def test_empty_secret_returns_503(self):
        ok, status, reason = verify_razorpay_webhook(self.BODY, "a" * 64, "")
        assert ok is False
        assert status == 503
        assert reason == "WEBHOOK_SECRET_UNCONFIGURED"

    # ── Rule 2: malformed signature → 400, zero exceptions ───────────────────

    def test_none_signature_returns_400(self):
        ok, status, reason = verify_razorpay_webhook(self.BODY, None, self.SECRET)
        assert ok is False
        assert status == 400
        assert reason == "MALFORMED_SIGNATURE_FORMAT"

    def test_empty_signature_returns_400(self):
        ok, status, reason = verify_razorpay_webhook(self.BODY, "", self.SECRET)
        assert ok is False
        assert status == 400
        assert reason == "MALFORMED_SIGNATURE_FORMAT"

    def test_one_char_signature_returns_400(self):
        """Single char — not 64 chars, Issue #135: bytes.fromhex('a') is valid but
        too short; the regex guard must reject it before hex-decoding."""
        ok, status, reason = verify_razorpay_webhook(self.BODY, "a", self.SECRET)
        assert ok is False
        assert status == 400
        assert reason == "MALFORMED_SIGNATURE_FORMAT"

    def test_odd_length_hex_signature_returns_400(self):
        """Odd-length hex string — bytes.fromhex() would raise ValueError (Issue #135)."""
        odd_hex = "a" * 63   # 63 chars — odd length, would crash bytes.fromhex
        ok, status, reason = verify_razorpay_webhook(self.BODY, odd_hex, self.SECRET)
        assert ok is False
        assert status == 400
        assert reason == "MALFORMED_SIGNATURE_FORMAT"

    def test_non_hex_signature_returns_400(self):
        """64 chars but containing non-hex character 'g'."""
        non_hex = "g" + "a" * 63   # 64 chars, starts with 'g' (not hex)
        ok, status, reason = verify_razorpay_webhook(self.BODY, non_hex, self.SECRET)
        assert ok is False
        assert status == 400
        assert reason == "MALFORMED_SIGNATURE_FORMAT"

    def test_63_char_valid_hex_returns_400(self):
        """63 valid hex chars — wrong length."""
        ok, status, reason = verify_razorpay_webhook(self.BODY, "ab" * 31 + "a", self.SECRET)
        assert ok is False
        assert status == 400
        assert reason == "MALFORMED_SIGNATURE_FORMAT"

    def test_65_char_valid_hex_returns_400(self):
        """65 valid hex chars — wrong length."""
        ok, status, reason = verify_razorpay_webhook(self.BODY, "a" * 65, self.SECRET)
        assert ok is False
        assert status == 400
        assert reason == "MALFORMED_SIGNATURE_FORMAT"

    # ── Rule 4: 64-char valid hex but wrong secret → 401 ─────────────────────

    def test_wrong_signature_returns_401(self):
        """64-char valid hex but not the correct HMAC → 401."""
        wrong = "b" * 64   # valid hex, wrong value
        ok, status, reason = verify_razorpay_webhook(self.BODY, wrong, self.SECRET)
        assert ok is False
        assert status == 401
        assert reason == "INVALID_SIGNATURE"

    def test_correct_signature_for_different_body_returns_401(self):
        """HMAC computed over different body — still 64-char hex, still wrong."""
        other_body = b'{"event":"payment.failed"}'
        sig = _make_signature(other_body, self.SECRET)   # valid for wrong body
        ok, status, reason = verify_razorpay_webhook(self.BODY, sig, self.SECRET)
        assert ok is False
        assert status == 401
        assert reason == "INVALID_SIGNATURE"

    # ── Rule 5: correct signature → 200 ──────────────────────────────────────

    def test_valid_signature_returns_200(self):
        sig = _make_signature(self.BODY, self.SECRET)
        ok, status, reason = verify_razorpay_webhook(self.BODY, sig, self.SECRET)
        assert ok is True
        assert status == 200
        assert reason == "VERIFIED"

    def test_valid_signature_uppercase_hex_returns_200(self):
        """HMAC signatures are sometimes returned uppercase by proxies."""
        sig = _make_signature(self.BODY, self.SECRET).upper()
        ok, status, reason = verify_razorpay_webhook(self.BODY, sig, self.SECRET)
        assert ok is True
        assert status == 200
        assert reason == "VERIFIED"

    def test_empty_body_valid_signature_returns_200(self):
        """Empty body is a legitimate edge-case (ping events)."""
        body = b""
        sig = _make_signature(body, self.SECRET)
        ok, status, reason = verify_razorpay_webhook(body, sig, self.SECRET)
        assert ok is True
        assert status == 200
        assert reason == "VERIFIED"


# ═════════════════════════════════════════════════════════════════════════════
# 2. PII / PAN Redactor
# ═════════════════════════════════════════════════════════════════════════════

class TestPiiRedactor:
    """PCI + PII redaction correctness."""

    HMAC_KEY = "test_pan_hmac_key_secret_1234"

    # ── luhn_check unit tests ─────────────────────────────────────────────────

    def test_luhn_valid_visa(self):
        assert luhn_check("4111111111111111") is True

    def test_luhn_valid_mastercard(self):
        assert luhn_check("5500005555555559") is True

    def test_luhn_valid_amex(self):
        assert luhn_check("378282246310005") is True  # 15-digit

    def test_luhn_invalid_sequential(self):
        # 1234567890123456 fails Luhn (also checked explicitly in the card section)
        assert luhn_check("1234567890123456") is False

    def test_luhn_invalid_non_digit(self):
        assert luhn_check("411111111111111x") is False

    def test_luhn_empty_string(self):
        assert luhn_check("") is False

    # ── Card PAN redaction ────────────────────────────────────────────────────

    def test_valid_luhn_card_redacted(self):
        text = "Card number: 4111111111111111 was charged."
        result = redact_pci_and_pii(text, self.HMAC_KEY)
        assert "[REDACTED_CARD_PAN]" in result
        assert "4111111111111111" not in result

    def test_spaced_card_redacted(self):
        text = "Card: 4111 1111 1111 1111"
        result = redact_pci_and_pii(text, self.HMAC_KEY)
        assert "[REDACTED_CARD_PAN]" in result

    def test_hyphenated_card_redacted(self):
        text = "Card: 4111-1111-1111-1111"
        result = redact_pci_and_pii(text, self.HMAC_KEY)
        assert "[REDACTED_CARD_PAN]" in result

    def test_non_luhn_16_digit_order_number_preserved(self):
        """16-digit number that fails Luhn must NOT be redacted (e.g. order ID)."""
        # 1234567890123456 fails Luhn
        assert luhn_check("1234567890123456") is False
        text = "Order ID: 1234567890123456 placed."
        result = redact_pci_and_pii(text, self.HMAC_KEY)
        assert "1234567890123456" in result
        assert "[REDACTED_CARD_PAN]" not in result

    # ── CVV redaction ─────────────────────────────────────────────────────────

    def test_cvv_3_digit_redacted(self):
        text = "cvv: 123"
        result = redact_pci_and_pii(text, self.HMAC_KEY)
        assert "[REDACTED_CVV]" in result
        assert "123" not in result

    def test_cvc_4_digit_redacted(self):
        text = "CVC = 9876"
        result = redact_pci_and_pii(text, self.HMAC_KEY)
        assert "[REDACTED_CVV]" in result

    # ── Indian PAN replacement ────────────────────────────────────────────────

    def test_indian_pan_replaced_with_hmac_token(self):
        pan = "ABCDE1234F"
        text = f"Customer PAN: {pan}"
        result = redact_pci_and_pii(text, self.HMAC_KEY)
        # Must not contain the raw PAN.
        assert pan not in result
        # Must contain the PAN_HMAC: prefix.
        assert "PAN_HMAC:" in result
        # Verify the token is exactly 64 hex chars.
        token = result.split("PAN_HMAC:")[1].split()[0]
        assert len(token) == 64
        assert all(c in "0123456789abcdef" for c in token)

    def test_indian_pan_deterministic(self):
        """Same PAN + same key → same token every time."""
        pan = "ABCDE1234F"
        text = f"PAN {pan}"
        r1 = redact_pci_and_pii(text, self.HMAC_KEY)
        r2 = redact_pci_and_pii(text, self.HMAC_KEY)
        assert r1 == r2

    def test_indian_pan_different_key_different_token(self):
        pan = "ABCDE1234F"
        text = f"PAN {pan}"
        r1 = redact_pci_and_pii(text, "key_alpha")
        r2 = redact_pci_and_pii(text, "key_beta")
        # Tokens must differ when keys differ.
        token1 = r1.split("PAN_HMAC:")[1].split()[0]
        token2 = r2.split("PAN_HMAC:")[1].split()[0]
        assert token1 != token2

    # ── Zero-width Unicode stripping ──────────────────────────────────────────

    def test_zero_width_chars_stripped(self):
        # Zero-width space, ZWNJ, ZWJ, BOM
        text = "hello\u200bworld\u200c\u200d\ufeffend"
        result = redact_pci_and_pii(text, self.HMAC_KEY)
        assert "\u200b" not in result
        assert "\u200c" not in result
        assert "\u200d" not in result
        assert "\ufeff" not in result
        assert "helloworld" in result

    # ── HTML comment stripping ────────────────────────────────────────────────

    def test_html_comment_stripped(self):
        text = "visible <!-- hidden payload --> text"
        result = redact_pci_and_pii(text, self.HMAC_KEY)
        assert "<!-- hidden payload -->" not in result
        assert "hidden payload" not in result
        assert "visible" in result
        assert "text" in result

    def test_multiline_html_comment_stripped(self):
        text = "before <!--\nmulti\nline\n--> after"
        result = redact_pci_and_pii(text, self.HMAC_KEY)
        assert "multi" not in result
        assert "before" in result
        assert "after" in result


# ═════════════════════════════════════════════════════════════════════════════
# 3. G4 Atomic Approval Lock — 20-Thread Concurrency Race
# ═════════════════════════════════════════════════════════════════════════════

class TestApprovalLockConcurrency:
    """
    20 threads race to consume the same approval token simultaneously.
    Exactly 1 must succeed (200 APPROVED) and 19 must fail (409 CONFLICT).
    """

    NUM_WORKERS = 20

    def _make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        init_approval_schema(conn)
        return conn

    def test_exactly_one_winner(self):
        conn = self._make_conn()
        token = str(uuid.uuid4())
        entity_id = "EXC-001"
        expires_at = _now_iso(offset_seconds=3600)   # 1 hour in the future
        now = _now_iso()

        create_pending_approval(conn, token, entity_id, expires_at)

        results: list[tuple[bool, int, str]] = []

        def _worker(worker_id: int) -> tuple[bool, int, str]:
            reviewer = f"reviewer_{worker_id}"
            return consume_approval_token(conn, token, reviewer, now)

        with ThreadPoolExecutor(max_workers=self.NUM_WORKERS) as pool:
            futures = [pool.submit(_worker, i) for i in range(self.NUM_WORKERS)]
            for future in as_completed(futures):
                results.append(future.result())

        status_counts = Counter(r[1] for r in results)

        assert status_counts[200] == 1,  (
            f"Expected exactly 1 APPROVED (200), got {status_counts[200]}. "
            f"Full results: {results}"
        )
        assert status_counts[409] == self.NUM_WORKERS - 1, (
            f"Expected {self.NUM_WORKERS - 1} CONFLICT (409), "
            f"got {status_counts[409]}. Full results: {results}"
        )

    def test_expired_token_returns_409(self):
        conn = self._make_conn()
        token = str(uuid.uuid4())
        # Token that expired 1 second ago.
        expires_at = _now_iso(offset_seconds=-1)
        create_pending_approval(conn, token, "EXC-002", expires_at)
        ok, status, reason = consume_approval_token(conn, token, "reviewer", _now_iso())
        assert ok is False
        assert status == 409
        assert reason == "TOKEN_ALREADY_USED_OR_EXPIRED"

    def test_nonexistent_token_returns_409(self):
        conn = self._make_conn()
        init_approval_schema(conn)
        ok, status, reason = consume_approval_token(
            conn, "ghost-token", "reviewer", _now_iso()
        )
        assert ok is False
        assert status == 409

    def test_already_approved_token_returns_409(self):
        conn = self._make_conn()
        token = str(uuid.uuid4())
        expires_at = _now_iso(offset_seconds=3600)
        create_pending_approval(conn, token, "EXC-003", expires_at)

        # First consume — must succeed.
        ok1, s1, _ = consume_approval_token(conn, token, "reviewer_a", _now_iso())
        assert ok1 is True
        assert s1 == 200

        # Second consume — must fail.
        ok2, s2, r2 = consume_approval_token(conn, token, "reviewer_b", _now_iso())
        assert ok2 is False
        assert s2 == 409
        assert r2 == "TOKEN_ALREADY_USED_OR_EXPIRED"


# ═════════════════════════════════════════════════════════════════════════════
# 4. G5 Tamper-Evident Audit Hash Chain
# ═════════════════════════════════════════════════════════════════════════════

class TestAuditHashChain:
    """Append events, verify chain integrity, and detect tampering."""

    NUM_EVENTS = 5

    def _make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        init_chain_schema(conn)
        return conn

    def test_empty_chain_is_valid(self):
        conn = self._make_conn()
        ok, broken, reason = verify_chain(conn)
        assert ok is True
        assert broken is None
        assert reason == "OK"

    def test_append_and_verify_five_events(self):
        conn = self._make_conn()
        base_time = datetime(2026, 11, 22, 15, 0, 0, tzinfo=timezone.utc)

        for i in range(self.NUM_EVENTS):
            ts = (base_time + timedelta(seconds=i)).isoformat()
            append_event(
                conn,
                event_type=f"TEST_EVENT_{i}",
                payload={"seq": i, "amount_paise": (i + 1) * 10000},
                timestamp_iso=ts,
            )

        ok, broken, reason = verify_chain(conn)
        assert ok is True, f"Chain should be intact, got broken={broken}, reason={reason}"
        assert broken is None
        assert reason == "OK"

    def test_append_returns_64_char_hex(self):
        conn = self._make_conn()
        h = append_event(conn, "EVT", {"k": "v"}, "2026-11-22T15:00:00+00:00")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_tamper_payload_on_row_3_detected(self):
        """Directly mutate payload_json on row 3 → verify_chain returns (False, 3, ...)."""
        conn = self._make_conn()
        base_time = datetime(2026, 11, 22, 15, 0, 0, tzinfo=timezone.utc)

        for i in range(self.NUM_EVENTS):
            ts = (base_time + timedelta(seconds=i)).isoformat()
            append_event(
                conn,
                event_type=f"EVT_{i}",
                payload={"index": i},
                timestamp_iso=ts,
            )

        # Verify baseline — should be intact.
        ok_before, _, _ = verify_chain(conn)
        assert ok_before is True

        # Directly mutate the payload_json of row with seq = 3.
        # (This is what G5 is designed to detect.)
        tampered_payload = json.dumps(
            {"index": 2, "tampered": True}, sort_keys=True, separators=(",", ":")
        )
        conn.execute(
            "UPDATE audit_events SET payload_json = ? WHERE seq = 3",
            (tampered_payload,),
        )
        conn.commit()

        ok_after, broken_seq, reason = verify_chain(conn)
        assert ok_after is False, "Tamper should be detected"
        assert broken_seq == 3, (
            f"Broken seq should be 3 (the tampered row), got {broken_seq}"
        )
        assert reason  # non-empty reason string

    def test_tamper_event_hash_on_row_2_detected(self):
        """Mutating stored event_hash of row 2 also breaks the chain at row 2."""
        conn = self._make_conn()
        base_time = datetime(2026, 11, 22, 15, 0, 0, tzinfo=timezone.utc)

        for i in range(self.NUM_EVENTS):
            ts = (base_time + timedelta(seconds=i)).isoformat()
            append_event(conn, "EVT", {"i": i}, ts)

        conn.execute(
            "UPDATE audit_events SET event_hash = ? WHERE seq = 2",
            ("f" * 64,),   # bogus hash
        )
        conn.commit()

        ok, broken_seq, reason = verify_chain(conn)
        assert ok is False
        # Row 2's hash no longer matches the recomputation → first break at seq=2.
        assert broken_seq == 2

    def test_tamper_prev_hash_on_row_4_detected(self):
        """Mutating prev_hash of row 4 breaks the linkage at row 4."""
        conn = self._make_conn()
        base_time = datetime(2026, 11, 22, 15, 0, 0, tzinfo=timezone.utc)

        for i in range(self.NUM_EVENTS):
            ts = (base_time + timedelta(seconds=i)).isoformat()
            append_event(conn, "EVT", {"i": i}, ts)

        conn.execute(
            "UPDATE audit_events SET prev_hash = ? WHERE seq = 4",
            ("0" * 64,),   # forge a wrong prev_hash
        )
        conn.commit()

        ok, broken_seq, reason = verify_chain(conn)
        assert ok is False
        assert broken_seq == 4

    def test_genesis_prev_hash_is_all_zeros(self):
        """The first event must store the genesis sentinel '0' * 64 as prev_hash."""
        conn = self._make_conn()
        append_event(conn, "GENESIS_TEST", {"k": 1}, "2026-11-22T00:00:00+00:00")
        row = conn.execute(
            "SELECT prev_hash FROM audit_events WHERE seq = 1"
        ).fetchone()
        assert row is not None
        assert row[0] == "0" * 64
