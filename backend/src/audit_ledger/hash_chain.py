"""
backend/src/audit_ledger/hash_chain.py
========================================
G5 — Tamper-Evident Append-Only Audit Chain (SQLite WAL implementation).

Design
------
Each row in ``audit_events`` contains:

  * ``seq``           — auto-incrementing primary key; determines chain order.
  * ``event_type``    — short label for the event category.
  * ``payload_json``  — canonical JSON (sort_keys=True, no spaces) of the event.
  * ``timestamp_iso`` — ISO-8601 string supplied by the caller.
  * ``prev_hash``     — the ``event_hash`` of the preceding row (or genesis
                        sentinel ``"0" * 64`` for the first row).
  * ``event_hash``    — SHA-256 of ``f"{prev_hash}|{event_type}|{payload_json}|{timestamp_iso}"``.

Tamper detection
----------------
``verify_chain`` re-reads every row, recomputes its expected ``event_hash`` from
``prev_hash + event_type + payload_json + timestamp_iso``, and checks:
  1. The recomputed hash matches the stored ``event_hash``.
  2. The stored ``prev_hash`` matches the ``event_hash`` of the previous row.

Any discrepancy immediately returns ``(False, broken_seq, reason)``.

Invariant G5 (from AGENTS.md)
------------------------------
* ``audit_events`` is **append-only** — no UPDATE/DELETE statements are present
  in this module.
* The database-level ``REVOKE UPDATE, DELETE ON audit_events FROM app_role``
  constraint is enforced at the PostgreSQL layer in production; SQLite tests rely
  on this module's self-discipline (no mutation SQL).

No LLM libraries are imported — deterministic-core safe.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import weakref

# Genesis sentinel: the ``prev_hash`` used for the very first event.
_GENESIS_PREV_HASH: str = "0" * 64

# Per-connection write lock registry to serialise concurrent appends
_conn_locks: weakref.WeakValueDictionary[int, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_registry_mu = threading.Lock()


def _lock_for(conn: sqlite3.Connection) -> threading.Lock:
    """Return (or create) the write-serialisation lock for *conn*."""
    cid = id(conn)
    with _registry_mu:
        lock = _conn_locks.get(cid)
        if lock is None:
            lock = threading.Lock()
            _conn_locks[cid] = lock
    return lock


# DDL ──────────────────────────────────────────────────────────────────────────
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_events (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type    TEXT    NOT NULL,
    payload_json  TEXT    NOT NULL,
    timestamp_iso TEXT    NOT NULL,
    prev_hash     TEXT    NOT NULL,
    event_hash    TEXT    NOT NULL
);
"""

_WAL_PRAGMAS = [
    "PRAGMA journal_mode = WAL;",
    "PRAGMA synchronous   = NORMAL;",
    "PRAGMA busy_timeout  = 5000;",
]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _canonical_json(payload: dict) -> str:  # type: ignore[type-arg]
    """Serialise *payload* to canonical JSON (sorted keys, no extra whitespace)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _compute_hash(prev_hash: str, event_type: str, canonical_json_str: str, timestamp_iso: str) -> str:
    """Return the SHA-256 hex digest for a single audit-chain link including event_type."""
    data = f"{prev_hash}|{event_type}|{canonical_json_str}|{timestamp_iso}".encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Schema bootstrap
# ─────────────────────────────────────────────────────────────────────────────

def initialise_schema(conn: sqlite3.Connection) -> None:
    """Create the ``audit_events`` table if it does not already exist.

    Call once per connection / at application startup.
    """
    for pragma in _WAL_PRAGMAS:
        conn.execute(pragma)
    conn.execute(_CREATE_TABLE_SQL)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def append_event(
    conn: sqlite3.Connection,
    event_type: str,
    payload: dict,  # type: ignore[type-arg]
    timestamp_iso: str,
) -> str:
    """Append a new event to the audit chain and return its ``event_hash``.

    Parameters
    ----------
    conn:
        An open ``sqlite3.Connection`` (``check_same_thread=False`` for
        multi-threaded use).
    event_type:
        Short label, e.g. ``"RECON_MATCH"``, ``"EXCEPTION_OPENED"``.
    payload:
        Arbitrary dict; will be serialised to canonical JSON.
    timestamp_iso:
        Caller-supplied ISO-8601 timestamp.

    Returns
    -------
    str
        The 64-character hex ``event_hash`` of the newly appended row.
    """
    canonical = _canonical_json(payload)
    write_lock = _lock_for(conn)

    with write_lock:
        in_tx_already = conn.in_transaction
        if not in_tx_already:
            conn.execute("BEGIN IMMEDIATE")

        try:
            # Fetch the hash of the most recently inserted row (if any).
            row = conn.execute(
                "SELECT event_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash: str = row[0] if row else _GENESIS_PREV_HASH

            event_hash = _compute_hash(prev_hash, event_type, canonical, timestamp_iso)

            conn.execute(
                """
                INSERT INTO audit_events
                    (event_type, payload_json, timestamp_iso, prev_hash, event_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_type, canonical, timestamp_iso, prev_hash, event_hash),
            )
            if not in_tx_already:
                conn.execute("COMMIT")
            else:
                conn.commit()
        except Exception:
            if not in_tx_already:
                conn.execute("ROLLBACK")
            else:
                conn.rollback()
            raise

    return event_hash


def verify_chain(conn: sqlite3.Connection) -> tuple[bool, int | None, str]:
    """Verify the integrity of every row in the audit chain.

    Parameters
    ----------
    conn:
        An open ``sqlite3.Connection``.

    Returns
    -------
    tuple[bool, int | None, str]
        ``(True,  None, "OK")``                     — chain intact.
        ``(False, broken_seq, reason_string)``       — first broken link found.
    """
    rows = conn.execute(
        "SELECT seq, event_type, payload_json, timestamp_iso, prev_hash, event_hash "
        "FROM audit_events ORDER BY seq ASC"
    ).fetchall()

    if not rows:
        return (True, None, "OK")

    expected_prev_hash: str = _GENESIS_PREV_HASH

    for row in rows:
        seq, event_type, payload_json, timestamp_iso, stored_prev, stored_hash = row

        # Check 1: prev_hash linkage.
        if stored_prev != expected_prev_hash:
            return (
                False,
                seq,
                f"prev_hash mismatch at seq={seq}: "
                f"stored={stored_prev!r}, expected={expected_prev_hash!r}",
            )

        # Check 2: event_hash correctness (incorporating event_type).
        recomputed = _compute_hash(stored_prev, event_type, payload_json, timestamp_iso)
        if recomputed != stored_hash:
            return (
                False,
                seq,
                f"event_hash mismatch at seq={seq}: "
                f"stored={stored_hash!r}, recomputed={recomputed!r}",
            )

        # Advance: next row's expected prev_hash is this row's recomputed hash.
        expected_prev_hash = recomputed

    return (True, None, "OK")
