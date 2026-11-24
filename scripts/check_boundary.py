"""
scripts/check_boundary.py
=========================
G2 Boundary Isolation Verifier:
Asserts that deterministic_core, verifier, and audit_ledger never import LLM packages
or llm_layer.
"""
from __future__ import annotations

import glob
import re
import sys

FORBIDDEN = [
    "litellm",
    "openai",
    "anthropic",
    "groq",
    "google.genai",
    "google.generativeai",
    "llm_layer",
]

TARGET_DIRS = [
    "backend/src/deterministic_core",
    "backend/src/verifier",
    "backend/src/audit_ledger",
]


def check_boundaries() -> int:
    violations: list[str] = []
    for target_dir in TARGET_DIRS:
        for filepath in glob.glob(f"{target_dir}/**/*.py", recursive=True):
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            for pkg in FORBIDDEN:
                pattern = r"\b(import\s+" + re.escape(pkg) + r"|from\s+" + re.escape(pkg) + r")\b"
                if re.search(pattern, content):
                    violations.append(f"{filepath}: forbidden import of '{pkg}'")

    if violations:
        print("❌ G2 Boundary Isolation VIOLATIONS:")
        for v in violations:
            print(f"  - {v}")
        return 1

    print("✅ G2 Boundary Isolation: Zero forbidden LLM imports detected in deterministic layers.")
    return 0


if __name__ == "__main__":
    sys.exit(check_boundaries())
