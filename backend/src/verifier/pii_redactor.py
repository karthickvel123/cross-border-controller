"""
backend/src/verifier/pii_redactor.py
======================================
PCI-DSS / PII redaction utilities for text handling in the Cross-Border Controller.

Covers:
  - Credit/debit card Primary Account Numbers (PAN) via Luhn check
  - CVV / CVC values adjacent to their label keywords
  - Indian Income-Tax PANs (Permanent Account Numbers) via keyed HMAC-SHA256
  - Zero-width Unicode homoglyphs and HTML comments (exfiltration vectors)

No LLM libraries are imported — deterministic-core safe.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re

# ── Card-number pattern ───────────────────────────────────────────────────────
# Matches 13–19 contiguous digit groups that may be separated by spaces or
# hyphens (common card formatting: "4111 1111 1111 1111").
_CARD_CANDIDATE_RE = re.compile(
    r"\b(?:\d[ -]?){12,18}\d\b"
)

# ── CVV / CVC pattern ─────────────────────────────────────────────────────────
# Matches 3 or 4 digits that immediately follow a "cvv" or "cvc" keyword
# (optionally separated by whitespace, colon, or equals).
_CVV_RE = re.compile(
    r"(?i)\b(?:cvv|cvc)\s*[=:\s]\s*(\d{3,4})\b"
)

# ── Indian Income-Tax PAN ─────────────────────────────────────────────────────
# Format: 5 uppercase letters, 4 digits, 1 uppercase letter  (e.g. ABCDE1234F).
_INDIA_PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")

# ── Zero-width / invisible Unicode characters ─────────────────────────────────
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")

# ── HTML comments (including multi-line) ─────────────────────────────────────
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# ─────────────────────────────────────────────────────────────────────────────
# Luhn algorithm
# ─────────────────────────────────────────────────────────────────────────────

def luhn_check(digits: str) -> bool:
    """Return ``True`` if *digits* (a string of ASCII digit characters) passes
    the Luhn (mod-10) checksum, ``False`` otherwise.

    The function is intentionally strict: it only accepts strings whose every
    character is ``0``–``9``.  Spaces, hyphens, or any other separator must be
    stripped by the caller before passing.

    Algorithm (ISO/IEC 7812-1):
        1. Starting from the rightmost digit (the check digit) and moving left,
           double the value of every second digit.
        2. If doubling results in a number > 9, subtract 9.
        3. Sum all digits (original + adjusted).
        4. If the total modulo 10 is 0 → valid.
    """
    if not digits or not digits.isdigit():
        return False

    total = 0
    # Iterate right-to-left; ``i`` is the 0-based position from the right.
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:          # every second digit from the right (1-indexed)
            n *= 2
            if n > 9:
                n -= 9
        total += n

    return total % 10 == 0


# ─────────────────────────────────────────────────────────────────────────────
# Main redaction entry point
# ─────────────────────────────────────────────────────────────────────────────

def redact_pci_and_pii(text: str, pan_hmac_key: str) -> str:
    """Redact PCI / PII data from *text* and return the sanitised string.

    Parameters
    ----------
    text:
        Arbitrary text that may contain sensitive data (log lines, error
        messages, JSON dumps, etc.).
    pan_hmac_key:
        Secret key used for keyed HMAC-SHA256 replacement of Indian PANs.
        The same key produces the same 64-hex-char token for the same PAN,
        enabling cross-reference without storing the raw value.

    Redaction order
    ---------------
    1. Strip zero-width Unicode characters (steganographic exfiltration risk).
    2. Strip HTML comments (hidden payload vector).
    3. Redact card PANs (Luhn-validated 13–19 digit sequences).
    4. Redact CVV / CVC values adjacent to their keyword.
    5. Replace Indian Income-Tax PANs with ``PAN_HMAC:<64-hex>``.

    Notes
    -----
    * Card-number extraction normalises separators (spaces/hyphens) before the
      Luhn check so ``4111-1111-1111-1111`` and ``4111 1111 1111 1111`` are both
      caught.
    * Order numbers, transaction IDs, and other 16-digit non-Luhn numbers are
      **preserved** intentionally.
    * The Indian PAN regex requires word boundaries (``\\b``) to avoid matching
      substrings inside longer tokens.
    """

    # ── Step 1: strip zero-width Unicode ─────────────────────────────────────
    text = _ZERO_WIDTH_RE.sub("", text)

    # ── Step 2: strip HTML comments ──────────────────────────────────────────
    text = _HTML_COMMENT_RE.sub("", text)

    # ── Step 3: redact card PANs ─────────────────────────────────────────────
    def _replace_card(m: re.Match) -> str:  # type: ignore[type-arg]
        raw = m.group(0)
        # Normalise: remove separators to get pure digit string.
        digits = re.sub(r"[ -]", "", raw)
        if luhn_check(digits):
            return "[REDACTED_CARD_PAN]"
        return raw  # not a valid card; leave intact (e.g. order number)

    text = _CARD_CANDIDATE_RE.sub(_replace_card, text)

    # ── Step 4: redact CVV / CVC ─────────────────────────────────────────────
    def _replace_cvv(m: re.Match) -> str:  # type: ignore[type-arg]
        # Preserve the keyword + separator structure; replace only the digits.
        full = m.group(0)
        cvv_digits = m.group(1)
        return full.replace(cvv_digits, "[REDACTED_CVV]")

    text = _CVV_RE.sub(_replace_cvv, text)

    # ── Step 5: replace Indian Income-Tax PANs with keyed HMAC token ─────────
    def _replace_pan(m: re.Match) -> str:  # type: ignore[type-arg]
        pan = m.group(1)
        mac = hmac.new(
            pan_hmac_key.encode("utf-8"),
            pan.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()          # 64 lowercase hex chars
        return f"PAN_HMAC:{mac}"

    text = _INDIA_PAN_RE.sub(_replace_pan, text)

    return text
