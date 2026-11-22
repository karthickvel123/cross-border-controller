"""
backend/tests/unit/test_eval_harness.py
=========================================
Unit tests for the evaluation corpus generator and harness.

Verifies:
  1. generate_corpus(20261122) returns exactly 200 records with exact stratum
     counts (110, 40, 30, 20) and is byte-for-byte deterministic across two runs.
  2. Running the harness in --dry-run mode exits 0, writes run_20261122.jsonl
     (200 lines) and summary_20261122.json, and asserts 0 false matches and
     0 ungrounded leaks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from eval.corpus_generator import DEFAULT_SEED, generate_corpus
from eval.harness import run_eval_harness


# ═════════════════════════════════════════════════════════════════════════════
# 1. Corpus Generator Determinism and Strata
# ═════════════════════════════════════════════════════════════════════════════

def test_corpus_generation_determinism_and_strata():
    """generate_corpus(20261122) returns 200 records with exact stratum counts

    (110, 40, 30, 20) and is byte-for-byte deterministic across runs.
    """
    corpus_run1 = generate_corpus(DEFAULT_SEED)
    corpus_run2 = generate_corpus(DEFAULT_SEED)

    # 1. Total length must be exactly 200
    assert len(corpus_run1) == 200
    assert len(corpus_run2) == 200

    # 2. Byte-for-byte deterministic (identical JSON serializations)
    serialized1 = json.dumps(corpus_run1, sort_keys=True)
    serialized2 = json.dumps(corpus_run2, sort_keys=True)
    assert serialized1 == serialized2

    # 3. Exact stratum counts: (110, 40, 30, 20)
    strata_counts = {
        "HAPPY_PATH": sum(1 for r in corpus_run1 if r["stratum"] == "HAPPY_PATH"),
        "AMBIGUOUS_RESIDUE": sum(1 for r in corpus_run1 if r["stratum"] == "AMBIGUOUS_RESIDUE"),
        "REGULATORY_POLICY": sum(1 for r in corpus_run1 if r["stratum"] == "REGULATORY_POLICY"),
        "ADVERSARIAL": sum(1 for r in corpus_run1 if r["stratum"] == "ADVERSARIAL"),
    }

    assert strata_counts["HAPPY_PATH"] == 110
    assert strata_counts["AMBIGUOUS_RESIDUE"] == 40
    assert strata_counts["REGULATORY_POLICY"] == 30
    assert strata_counts["ADVERSARIAL"] == 20


# ═════════════════════════════════════════════════════════════════════════════
# 2. Evaluation Harness Dry-Run Execution & Safety Invariants
# ═════════════════════════════════════════════════════════════════════════════

def test_harness_dry_run_programmatic(tmp_path: Path):
    """Programmatic execution of harness writes artifacts and verifies invariants."""
    output_dir = str(tmp_path / "artifacts")
    summary = run_eval_harness(seed=DEFAULT_SEED, dry_run=True, output_dir=output_dir)

    # Invariants verification
    assert summary["total_records"] == 200
    assert summary["happy_path_exact_match_rate"] == 100.0
    assert summary["false_matches"] == 0
    assert summary["policy_enforcement_rate"] == 100.0
    assert summary["adversarial_defense_rate"] == 100.0
    assert summary["ungrounded_token_leak_rate_protected"] == 0.0
    assert summary["ungrounded_token_leak_rate_baseline"] > 0.0
    assert summary["total_at_risk_flagged_inr"] > 0.0

    # File artifacts
    run_file = Path(output_dir) / f"run_{DEFAULT_SEED}.jsonl"
    summary_file = Path(output_dir) / f"summary_{DEFAULT_SEED}.json"

    assert run_file.exists()
    assert summary_file.exists()

    with open(run_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 200

    with open(summary_file, "r", encoding="utf-8") as f:
        saved_summary = json.load(f)
    assert saved_summary["false_matches"] == 0
    assert saved_summary["ungrounded_token_leak_rate_protected"] == 0.0


def test_harness_cli_dry_run_subprocess(tmp_path: Path):
    """CLI execution of python -m eval.harness exits with code 0."""
    output_dir = str(tmp_path / "cli_artifacts")
    cmd = [
        sys.executable,
        "-m",
        "eval.harness",
        "--dry-run",
        "--seed",
        str(DEFAULT_SEED),
        "--output-dir",
        output_dir,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"Harness CLI failed with stderr: {result.stderr}"
    assert "All hard safety gates passed with 100.0% compliance" in result.stdout

    run_file = Path(output_dir) / f"run_{DEFAULT_SEED}.jsonl"
    assert run_file.exists()
    with open(run_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 200
