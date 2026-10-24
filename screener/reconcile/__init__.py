"""Reconciliation tests: the "does not add up" checks between sources (SPEC.md section 8).

Each test is a function taking the database connection and returning mismatch lines,
each with both sides and both source links. They arrive with their sources (phase 7);
phase 1 registers none, and the report says so.
"""

from __future__ import annotations

TESTS: list = []


def run_all(con) -> list[dict]:
    """Return every mismatch: {test, left, right, left_source, right_source, note}."""
    out = []
    for test in TESTS:
        out.extend(test(con))
    return out
