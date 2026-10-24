"""List of accounts paid: PDF in, payment rows out.

Councils publish this as a table attached to the monthly minutes: cheque or EFT number,
date, payee, description, amount. The header names vary, so columns are found by header
text. pdfplumber's ruled-table reader is tried first and its text-alignment reader second.
Phase 2 adds the model fallback for layouts neither can read.
"""

from __future__ import annotations

import re
from datetime import datetime

import pdfplumber

PARSER = "accounts_paid.pdfplumber"
PARSER_VERSION = "1"

_HEADERS = {
    "ref": re.compile(r"chq|cheque|eft|ref|voucher|number|no\.?$", re.I),
    "date": re.compile(r"date", re.I),
    "payee": re.compile(r"name|payee|creditor|supplier", re.I),
    "description": re.compile(r"desc|details|particulars", re.I),
    "amount": re.compile(r"amount|total|\$", re.I),
}
_REF = re.compile(r"^(EFT\s*\d+|\d{3,}|DD\s*\d+)$", re.I)
_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d")
_TEXT_SETTINGS = {"vertical_strategy": "text", "horizontal_strategy": "text"}


def parse(path) -> list[dict]:
    """Return one dict per payment: paid_date, ref, payee_raw, description, amount, page."""
    rows: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables() or page.extract_tables(_TEXT_SETTINGS)
            for table in tables:
                rows += _rows_from_table(table, page_no)
    return rows


def _rows_from_table(table: list[list], page_no: int) -> list[dict]:
    out = []
    cols = None
    for raw in table:
        cells = [_clean(c) for c in raw]
        mapped = _header_map(cells)
        if mapped:
            cols = mapped
            continue
        if cols is None:
            continue
        ref = cells[cols["ref"]] if "ref" in cols else ""
        if not _REF.match(ref.replace(" ", "")):
            continue  # totals, sub-headings, blank lines
        try:
            paid = _date(cells[cols["date"]])
            amount = _amount(cells[cols["amount"]])
        except (ValueError, KeyError, IndexError):
            continue
        out.append({
            "paid_date": paid.isoformat(),
            "ref": ref,
            "payee_raw": cells[cols["payee"]] if "payee" in cols else "",
            "description": cells[cols["description"]] if "description" in cols else "",
            "amount": amount,
            "page": page_no,
        })
    return out


def _header_map(cells: list[str]) -> dict | None:
    lowered = [c.lower() for c in cells]
    if not any("date" in c for c in lowered) or not any("amount" in c or c.startswith("$") for c in lowered):
        return None
    cols = {}
    for key, pattern in _HEADERS.items():
        for i, c in enumerate(cells):
            if i not in cols.values() and c and pattern.search(c):
                cols[key] = i
                break
    return cols if {"date", "amount", "payee"} <= cols.keys() else None


def _clean(cell) -> str:
    return " ".join(str(cell or "").split())


def _date(s: str):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(s)


def _amount(s: str) -> float:
    s = s.replace("$", "").replace(",", "").strip()
    negative = s.startswith("(") and s.endswith(")") or s.endswith("CR")
    s = s.strip("()").removesuffix("CR").strip()
    value = float(s)
    return -value if negative else value
