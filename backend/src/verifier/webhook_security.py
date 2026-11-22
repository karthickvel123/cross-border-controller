"""
backend/src/verifier/webhook_security.py
=========================================
Razorpay webhook HMAC-SHA256 verification — fixes razorpay-mcp-server Issue #135.

Issue #135 root cause: the original code called `bytes.fromhex()` inside the HMAC
comparison block without catching `ValueError`, meaning a hex-decode error of a
malformed (non-hex / odd-length) signature string would propagate as an unhandled
exception rather than a clean 400 rejection.  This module fixes that by validating
the signature *format* (regex guard) **before** any byte-decoding, so
`bytes.fromhex()` is never called on invalid input.

No LLM libraries are imported — this module is part of deterministic_core territory.
"""

from __future__ import annotations

import hashlib
import hmac
import re

# Exactly 64 hex characters — SHA-256 produces 32 bytes = 64 hex chars.
_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def verify_razorpay_webhook(
    raw_body: bytes,
    signature: str | None,
    webhook_secret: str | None,
) -> tuple[bool, int, str]:
    """Verify a Razorpay webhook HMAC-SHA256 signature.

    Parameters
    ----------
    raw_body:
        The raw, unmodified request body bytes exactly as received from Razorpay.
    signature:
        The value of the ``X-Razorpay-Signature`` header (may be ``None`` if the
        header was absent).
    webhook_secret:
        The shared Razorpay webhook secret configured in the dashboard.  Loaded
        from an environment variable; ``None`` / empty string means unconfigured.

    Returns
    -------
    tuple[bool, int, str]
        ``(success, http_status_code, reason_code)`` where ``reason_code`` is one
        of:

        - ``"VERIFIED"``                      — 200, signature matches.
        - ``"WEBHOOK_SECRET_UNCONFIGURED"``   — 503, secret absent.
        - ``"MALFORMED_SIGNATURE_FORMAT"``    — 400, header absent / bad hex.
        - ``"INVALID_SIGNATURE"``             — 401, hex valid but digest mismatch.

    Security notes
    --------------
    * Rule 2 (regex guard) is intentionally evaluated **before** ``bytes.fromhex``
      to prevent Issue #135: an odd-length or non-hex string would raise
      ``ValueError`` inside the HMAC path in the original implementation.
    * ``hmac.compare_digest`` operates on equal-length ``bytes`` objects to prevent
      timing side-channels.  We assert both buffers are exactly 32 bytes wide so
      the comparison is never short-circuited by a length mismatch.
    * The secret is encoded to UTF-8; Razorpay's dashboard stores secrets as
      printable ASCII, but UTF-8 is a safe superset.
    """

    # ── Rule 1: secret must be configured ────────────────────────────────────────
    if not webhook_secret:
        return (False, 503, "WEBHOOK_SECRET_UNCONFIGURED")

    # ── Rule 2: signature must be a 64-char hex string ───────────────────────────
    # Catches: None, empty string, 1-char, odd-length, non-hex characters.
    # This guard MUST run before bytes.fromhex() — see Issue #135.
    if not signature or not _HEX64_RE.match(signature):
        return (False, 400, "MALFORMED_SIGNATURE_FORMAT")

    # ── Rule 3: decode to exact 32-byte buffers ──────────────────────────────────
    # Both fromhex() calls are safe here because the regex above guarantees the
    # signature is a valid 64-char hex string.
    received: bytes = bytes.fromhex(signature)
    expected: bytes = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).digest()

    assert len(expected) == 32 and len(received) == 32, (  # noqa: S101
        f"Unexpected digest lengths: expected={len(expected)}, received={len(received)}"
    )

    # ── Rule 4 / 5: constant-time comparison ─────────────────────────────────────
    if hmac.compare_digest(expected, received):
        return (True, 200, "VERIFIED")

    return (False, 401, "INVALID_SIGNATURE")
