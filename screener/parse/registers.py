"""Registers already published as tables (CSV): file in, rows out.

Phase 1 reads the fixture's CSV registers. The PDF and HTML register parsers arrive with
the council fetchers in phases 2 and 4 and return the same columns.
"""

from __future__ import annotations

import csv

PARSER = "registers.csv"
PARSER_VERSION = "1"

# Columns each table keeps from its register file.
COLUMNS = {
    "payments": ["paid_date", "ref", "payee_raw", "description", "amount"],
    "councils": ["council_id", "name", "region", "website", "minutes_url", "population", "notes"],
    "tenders": ["tender_ref", "description", "closed_date", "awarded_date", "tenderer_raw", "is_winner", "contract_amount"],
    "gifts": ["recipient_name", "recipient_role", "giver_raw", "value", "received_date", "description"],
    "interests": ["person", "role", "interest_type", "entity_raw", "detail", "return_type", "return_date"],
    "disclosures": ["meeting_date", "item_ref", "item_title", "person", "interest_type"],
    "items": ["meeting_date", "item_ref", "item_title", "entities_mentioned"],
    "delegations": ["role", "limit_amount", "source_date"],
    "related_parties": ["fy", "counterparty_raw", "amount", "nature"],
    "candidate_gifts": ["election_date", "candidate", "giver_raw", "value"],
    "audit_findings": ["report_year", "report_title", "area", "finding"],
    "abn": ["abn", "name_raw", "entity_type", "status", "status_from", "state", "postcode"],
}


def parse(path, table: str) -> list[dict]:
    """Return the register's rows with the line number each came from."""
    keep = COLUMNS[table]
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            rec = {c: (row.get(c) or "").strip() for c in keep}
            rec["source_line"] = line
            out.append(rec)
    return out
