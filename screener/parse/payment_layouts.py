"""Council accounts-paid layouts: word positions in, payment rows and printed totals out.

Real lists of accounts paid are text PDFs with no ruled cells, so pdfplumber's table
reader cannot split them. This module reads each word with its position, groups words
into lines, assigns them to columns by x-position taken from the header words on each
page, and starts a row at a line with a date or reference in the key column. Every other
line continues the row it sits closest to (cells are vertically centred, so a wrapped
reference can sit just above its row).

Each council has a layout function below. The layout to use and the pages it covers are
named per file in the raw folder's manifest.json (the per-council override). A layout
returns three lists:

- payments: one row per payment made.
- details: lines that break down a payment already counted (invoice sub-lines, card and
  fuel statements paid by one EFT or direct debit, reimbursements inside a pay run). Each
  carries detail_of, the ref or row key of the payment it belongs to, so it is never
  counted twice.
- totals: every total printed on the PDF, with the section it closes, so the parsed rows
  can be checked against it to the cent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

import pdfplumber

DATE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})(.*)$")
_AMOUNT = re.compile(r"^-?(\d{1,3}(,\d{3})*|\d+)\.\d{2}$")


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bold: bool


@dataclass
class Line:
    page: int
    top: float
    words: list[Word]
    no: int = 0  # 1-based line number on its page

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def find(self, text: str, start: int = 0) -> Word | None:
        hits = [w for w in self.words if w.text == text]
        return hits[start] if len(hits) > start else None

    def between(self, lo: float, hi: float) -> list[Word]:
        return [w for w in self.words if lo <= w.x0 < hi]


@dataclass
class Result:
    payments: list[dict] = field(default_factory=list)
    details: list[dict] = field(default_factory=list)
    totals: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- reading words

def read_lines(path, pages: list[int], *, text_flow: bool = False, x_tolerance: float = 3, y_tolerance: float = 3) -> list[Line]:
    """Return the words on the given 1-based pages grouped into lines, top to bottom.

    text_flow keeps the PDF's own drawing order inside a line, which separates a payee
    name from an amount it overprints (Nedlands). Words whose tops are within
    y_tolerance of the line's first word join that line.
    """
    out: list[Line] = []
    with pdfplumber.open(path) as pdf:
        for n in pages:
            page = pdf.pages[n - 1]
            page = page.filter(_not_stray_space(page.chars))
            words = page.extract_words(x_tolerance=x_tolerance, use_text_flow=text_flow, extra_attrs=["fontname"])
            words = [Word(w["text"], w["x0"], w["x1"], w["top"], "Bold" in w["fontname"]) for w in words]
            out += group_lines(words, n, y_tolerance)
    return out


def _not_stray_space(chars: list[dict]):
    """Some PDFs draw spaces on top of letters ('Corpo r ation'). Drop any space that
    starts inside a letter on the same line."""
    letters = [c for c in chars if c["text"].strip()]
    stray = set()
    for c in chars:
        if c["text"] == " " and any(abs(l["top"] - c["top"]) < 1.5 and l["x0"] < c["x0"] < l["x1"] - 0.3 for l in letters):
            stray.add((round(c["x0"], 2), round(c["top"], 2)))
    return lambda o: o.get("object_type") != "char" or (round(o["x0"], 2), round(o["top"], 2)) not in stray


def group_lines(words: list[Word], page: int, y_tolerance: float = 3) -> list[Line]:
    lines: list[Line] = []
    for w in sorted(words, key=lambda w: (w.top, w.x0)):
        if lines and w.top - lines[-1].top <= y_tolerance:
            lines[-1].words.append(w)
        else:
            lines.append(Line(page, w.top, [w]))
    for i, line in enumerate(lines, start=1):
        line.words.sort(key=lambda w: w.x0)
        line.no = i
    return lines


def page_range(spec) -> list[int]:
    """'29-35' or '122-128,129-130' or [29, 30] -> list of page numbers."""
    if isinstance(spec, (list, tuple)):
        return [int(p) for p in spec]
    pages = []
    for part in str(spec).split(","):
        lo, _, hi = part.strip().partition("-")
        pages += range(int(lo), int(hi or lo) + 1)
    return pages


# ---------------------------------------------------------------- shared helpers

def amount(text: str) -> float | None:
    """'1,351.63', '$ 742.00', '-$ 2 4.00', '(12.00)', '6 ,184.54' -> float, else None."""
    s = text.replace(" ", "").replace("$", "")
    negative = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("()").lstrip("-").lstrip("$")
    if not _AMOUNT.match(s):
        return None
    value = float(s.replace(",", ""))
    return -value if negative else value


def trailing_amount(words: list[Word]) -> tuple[float | None, list[Word]]:
    """The amount made by the longest run of words at the end of the list, and the rest."""
    for i in range(len(words)):
        value = amount("".join(w.text for w in words[i:]))
        if value is not None:
            return value, words[:i]
    return None, words


def to_date(text: str) -> str:
    return datetime.strptime(text, "%d/%m/%Y").date().isoformat()


def columns(header: Line, spec: list[tuple[str, str, int, float]]) -> list[tuple[str, float]]:
    """Column left edges from a header line: (name, header word, occurrence, offset)."""
    cols = []
    for name, word, occurrence, offset in spec:
        w = header.find(word, occurrence)
        if w is None:
            raise ValueError(f"page {header.page}: header word {word!r} missing from {header.text!r}")
        cols.append((name, w.x0 + offset))
    return sorted(cols, key=lambda c: c[1])


def split(line: Line, cols: list[tuple[str, float]], words: list[Word] | None = None) -> dict[str, str]:
    """Assign each word to the last column whose left edge it starts at or after."""
    cells: dict[str, list[str]] = {name: [] for name, _ in cols}
    for w in line.words if words is None else words:
        name = cols[0][0]
        for col, x in cols:
            if w.x0 >= x:
                name = col
        cells[name].append(w.text)
    return {k: " ".join(v) for k, v in cells.items()}


def attach(lines: list[Line], is_anchor, nearest: bool = True) -> list[list[Line]]:
    """Group lines into rows. A row starts at an anchor line. With nearest, any other line
    joins the row of whichever neighbouring line on its page is closer vertically (ties go
    up), for layouts that centre cells on a row; otherwise it joins the row above."""
    rows: list[list[Line]] = []
    pending: list[Line] = []
    for i, line in enumerate(lines):
        if is_anchor(line):
            rows.append(pending + [line])
            pending = []
            continue
        prev = rows[-1][-1] if rows and rows[-1][-1].page == line.page else None
        nxt = next((l for l in lines[i + 1:] if is_anchor(l)), None)
        nxt = nxt if nxt and nxt.page == line.page else None
        gap_prev = line.top - prev.top if prev else float("inf")
        gap_next = nxt.top - line.top if nxt else float("inf")
        if (nearest and gap_next < gap_prev) or not rows:
            pending.append(line)
        else:
            rows[-1].append(line)
    return rows


def row_key(line: Line) -> str:
    return f"p{line.page}l{line.no}"


def _row(line: Line, section: str, **kw) -> dict:
    base = {"section": section, "paid_date": None, "ref": "", "invoice_ref": "", "payee_raw": "",
            "description": "", "amount": None, "page": line.page, "source_line": line.no,
            "row_key": row_key(line), "parse_note": ""}
    base.update(kw)
    return base


def _clean(text: str) -> str:
    return " ".join(text.split())


def check(result: Result) -> list[dict]:
    """Compare every printed total with the sum of the rows it closes.

    A total's scope is (kind, section, statement): kind 'payments' sums payment rows, kind
    'details' sums detail rows, kind 'schedule' sums the payments of one schedule (an
    appendix with several sections), kind 'grand' sums every payment. statement narrows a
    section to one card statement or fuel supplier when the PDF prints a total for each.
    """
    out = []
    for t in result.totals:
        rows = result.details if t["kind"] == "details" else result.payments
        if t["kind"] == "schedule":
            rows = [r for r in rows if r.get("schedule") == t["section"]]
        elif t["kind"] != "grand":
            rows = [r for r in rows if r["section"] == t["section"]]
        if t.get("statement"):
            rows = [r for r in rows if r.get("statement") == t["statement"]]
        parsed = round(sum(r["amount"] for r in rows), 2)
        out.append({**t, "rows": len(rows), "parsed": parsed, "difference": round(parsed - t["printed"], 2),
                    "ok": abs(parsed - t["printed"]) < 0.005})
    return out


def _total(section: str, printed: float, line: Line, kind: str = "payments", label: str = "", statement: str = "") -> dict:
    return {"section": section, "kind": kind, "statement": statement, "label": label or section,
            "printed": printed, "page": line.page, "source_line": line.no}


# ---------------------------------------------------------------- Shire of Toodyay

def toodyay(lines: list[Line]) -> Result:
    """Creditor Payment Report - Warrant Listing.

    Sections Cheque, EFT, Payroll, Direct Debit and Credit Card, each repeating the header
    'Payment Number Date Inv/Invoice No Creditor Name Invoice Description Inclusive Amount'
    and closing with '<section> Total'; then 'Grand Total'. Payroll and direct debit rows
    have no payment number. A credit card statement starts with a row whose creditor cell
    names the card ('Credit Card - <position> - <holder> - <month>'); its later rows leave
    that cell blank. The merchant is the description up to the first hyphen.
    """
    sections = {"Cheque Payments": "Cheque", "Electronic Funds Transfer Payments": "EFT", "Payroll": "Payroll",
                "Direct Debit": "Direct Debit", "Credit Card": "Credit Card"}
    spec = [("payment_no", "Payment", 0, -2), ("date", "Date", 0, -2), ("ref", "Date", 0, 29),
            ("payee", "Creditor", 0, -2), ("description", "Invoice", -1, -2), ("amount", "Inclusive", 0, -20)]
    res = Result()
    section, cols, card = None, None, ""
    body: list[Line] = []

    def flush():
        nonlocal card
        if not body:
            return
        for group in attach(body, lambda l: bool(DATE.match(split(l, cols)["date"]))):
            anchor = next(l for l in group if DATE.match(split(l, cols)["date"]))
            cells = {k: " ".join(split(l, cols)[k] for l in group).strip() for k, _ in cols}
            value, _ = trailing_amount([w for l in group for w in l.words if w.x0 >= dict(cols)["amount"]])
            row = _row(anchor, section, paid_date=to_date(cells["date"]), ref=cells["payment_no"],
                       invoice_ref=cells["ref"], payee_raw=_clean(cells["payee"]),
                       description=_clean(cells["description"]), amount=value)
            if section == "Credit Card":
                if row["payee_raw"].lower().startswith("credit card"):
                    card = row["payee_raw"]
                row["statement"] = card
                row["payee_raw"], row["invoice_ref"] = row["description"].partition("-")[0].strip(), ""
                row["parse_note"] = "card transaction; payee is the merchant named in the description"
            if value is None:
                row["parse_note"] = (row["parse_note"] + "; no amount").strip("; ")
            res.payments.append(row)
        body.clear()

    for line in lines:
        text = line.text
        if text in sections:
            flush()
            section = sections[text]
            continue
        if line.find("Inclusive") and line.find("Payment") and line.find("Description"):
            flush()
            cols = columns(line, spec)
            continue
        m = re.match(r"^(Cheque|EFT|Payroll|Direct Debit|Credit Card|Grand) Total (.+)$", text)
        if m:
            flush()
            if m.group(1) == "Grand":
                res.totals.append(_total("", amount(m.group(2)), line, "grand", "Grand Total"))
            else:
                res.totals.append(_total(section, amount(m.group(2)), line, label=f"{m.group(1)} Total"))
            continue
        if cols is None or section is None or text.startswith(("ORDINARY COUNCIL MEETING", "Item ", "Creditor Payment Report")) \
                or re.match(r"^\d{2} \w+ \d{4} to ", text):
            continue
        body.append(line)
    flush()
    return res


# ---------------------------------------------------------------- Shire of Coolgardie

def coolgardie(lines: list[Line]) -> Result:
    """Payments by Delegated Authority.

    Each page repeats the title and names its section (EFT's, Direct Debits, Credit Cards,
    Fuel Cards, Woolworths Cards). A section's total is a line holding only '$ amount'.
    EFT's, Direct Debits and Credit Cards are the payments. The council's own totals treat
    each card transaction as a payment and leave the direct debit that settles the card
    out of the Direct Debits total, so that direct debit is kept as a detail of the card
    statement. Fuel Cards break down one EFT per supplier (Ampol, BP, Eagle) and
    Woolworths Cards break down the Woolworths EFT, so those are details, linked to the
    EFT of the same amount.
    """
    titles = {"EFT's": "EFT", "Direct Debits": "Direct Debit", "Credit Cards": "Credit Card",
              "Fuel Cards": "Fuel Card", "Woolworths Cards": "Woolworths Card"}
    detail_sections = {"Fuel Card", "Woolworths Card"}
    res = Result()
    section, cols, kind, supplier = None, None, None, ""
    body: list[Line] = []
    paid_date = None

    def money_only(line: Line) -> float | None:
        if any(re.search(r"[A-Za-z]", w.text) for w in line.words) or not line.find("$") and not line.words[0].text.startswith("$"):
            return None
        return amount("".join(w.text for w in line.words))

    def flush():
        if not body:
            return
        target = res.details if section in detail_sections else res.payments
        if kind in ("EFT", "Direct Debit", "Credit Card", "Woolworths Card"):
            is_anchor = (lambda l: re.match(r"^EFT\d+$", split(l, cols)["eft"])) if kind == "EFT" \
                else (lambda l: bool(DATE.match(split(l, cols)["date"])))
            for group in attach(body, is_anchor, nearest=False):
                anchor = next(l for l in group if is_anchor(l))
                amt_x = dict(cols)["amount"]
                cells = {k: " ".join(split(l, cols, [w for w in l.words if w.x0 < amt_x])[k] for l in group).strip()
                         for k, _ in cols if k != "amount"}
                value, _ = trailing_amount([w for w in anchor.words if w.x0 >= amt_x and w.text != cells.get("card")]
                                           if kind != "Credit Card" else
                                           [w for l in group for w in l.words if amt_x <= w.x0 < dict(cols)["card"]])
                if value is None:
                    value, _ = trailing_amount([w for l in group for w in l.words if w.x0 >= amt_x and
                                                (kind != "Credit Card" or w.x0 < dict(cols)["card"])])
                row = _row(anchor, section, amount=value, description=_clean(cells.get("description", "")))
                if kind == "EFT":
                    row.update(ref=cells["eft"], paid_date=to_date(cells["date"]), payee_raw=_clean(cells["name"]))
                elif kind == "Direct Debit":
                    row.update(paid_date=to_date(cells["date"]), payee_raw=_clean(cells["payee"]),
                               invoice_ref=_clean(cells["reference"]))
                elif kind == "Credit Card":
                    merchant = row["description"].partition(" - ")[0]
                    card = " ".join(w.text for l in group for w in l.words if w.x0 >= dict(cols)["card"])
                    row.update(paid_date=to_date(cells["date"]), payee_raw=merchant.strip(), statement=card,
                               parse_note=f"card ending {card}; transaction date")
                else:  # Woolworths
                    row.update(paid_date=to_date(cells["date"]), payee_raw="Woolworths", invoice_ref=cells["invoice"],
                               statement="Woolworths", parse_note="invoice date")
                if value is None:
                    row["parse_note"] = (row["parse_note"] + "; no amount").strip("; ")
                target.append(row)
        elif kind == "Fuel":
            for line in body:
                cells = split(line, cols)
                if not re.match(r"^[PT]\d+$", cells["plant"]):
                    continue
                value, _ = trailing_amount([w for w in line.words if w.x0 >= dict(cols)["amount"]])
                res.details.append(_row(line, section, paid_date=paid_date, payee_raw=supplier, statement=supplier,
                                        invoice_ref=cells["plant"], amount=value,
                                        description=_clean(f"{cells['registration']} {cells['vehicle']}"),
                                        parse_note="fuel card; date is invoice paid"))
        elif kind == "Fuel invoice":
            for line in body:
                dates = [w.text for w in line.words if DATE.match(w.text)]
                if len(dates) < 2:
                    continue
                value, _ = trailing_amount([w for w in line.words if w.x0 >= dict(cols)["amount"]])
                res.details.append(_row(line, section, paid_date=to_date(dates[1]), payee_raw=supplier, statement=supplier,
                                        invoice_ref=line.words[0].text, amount=value,
                                        description=_clean(" ".join(w.text for w in line.words[3:] if w.x0 < dict(cols)["litres"])),
                                        parse_note=f"fuel invoice dated {dates[0]}"))
        body.clear()

    for line in lines:
        text = line.text
        if text in titles:
            if titles[text] != section:
                flush()
                section, cols, kind = titles[text], None, None
            continue
        if text.startswith(("ORDINARY COUNCIL MEETING", "Shire of Coolgardie", "Payments by Delegated Authority", "Item 12.2.1")) \
                or re.match(r"^\d{2} \w+ To \d{2} \w+ \d{4}$", text):
            continue
        if section is None:
            continue
        if (line.find("Amount") or line.find("Value")) and (line.find("Description") or line.find("Litres")):
            flush()
            if line.words[0].text == "EFT":
                kind, cols = "EFT", columns(line, [("eft", "EFT", 0, -3), ("date", "Date", 0, -3), ("name", "Name", 0, -3),
                                                   ("description", "Description", 0, -4), ("amount", "Amount", 0, -22)])
            elif line.find("Payee"):
                kind, cols = "Direct Debit", columns(line, [("date", "Date", 0, -3), ("payee", "Payee", 0, -3),
                                                            ("description", "Description", 0, -3), ("reference", "Reference", 0, -3),
                                                            ("amount", "Amount", 0, -26)])
            elif line.find("Supplier") and line.find("Plant"):
                supplier = line.words[1].text
                kind, cols = "Fuel", columns(line, [("left", "Supplier", 0, -3), ("plant", "Plant", 0, -3),
                                                    ("card_no", "Fuel", 0, -3), ("registration", "Registration", 0, -3),
                                                    ("vehicle", "Registration", 0, 50), ("litres", "Usage", 0, -3),
                                                    ("amount", "Amount", 0, -20)])
            elif line.find("Supplier"):
                supplier = line.words[1].text
                kind, cols = "Fuel invoice", columns(line, [("invoice", "Supplier", 0, -3), ("litres", "Litres", 0, -40),
                                                            ("amount", "Amount", 0, -20)])
            elif line.find("Card"):
                if line.find("Invoice"):
                    kind, cols = "Woolworths Card", columns(line, [("date", "Invoice", 0, -3), ("invoice", "No", 0, -40),
                                                                   ("description", "Description", 0, -3),
                                                                   ("card", "Card", 0, -3), ("amount", "Amount", 0, -20)])
                else:
                    kind, cols = "Credit Card", columns(line, [("date", "Date", 0, -3), ("description", "Description", 0, -3),
                                                               ("amount", "Value", 0, -22), ("card", "Card", 0, -3)])
            continue
        total = money_only(line)
        if total is not None and cols is not None:
            flush()
            if section == "Fuel Card":
                res.totals.append(_total(section, total, line, "details", f"Fuel Cards - {supplier}", statement=supplier))
            else:
                res.totals.append(_total(section, total, line, "details" if section in detail_sections else "payments",
                                         f"{section} total"))
            continue
        if kind in ("Fuel", "Fuel invoice") and line.words[0].text == "Invoice":
            m = re.search(r"Paid (\d{1,2}/\d{1,2}/\d{4})", text)
            if m:
                paid_date = to_date(m.group(1))
        if cols is not None:
            body.append(line)
    flush()
    _settle_card(res)
    _link_details(res)
    return res


def _settle_card(res: Result) -> None:
    """Move the direct debit that pays the credit card statement to the details."""
    card = round(sum(r["amount"] or 0 for r in res.payments if r["section"] == "Credit Card"), 2)
    hits = [r for r in res.payments if r["section"] == "Direct Debit" and r["amount"] is not None
            and abs(r["amount"] - card) < 0.005 and "card" in (r["description"] + r["invoice_ref"]).lower()]
    if len(hits) == 1:
        hit = hits[0]
        res.payments.remove(hit)
        hit.update(detail_of="Credit Card", parse_note="settles the credit card statement; the card transactions are the payments")
        res.details.append(hit)


def _link_details(res: Result) -> None:
    """Point each detail group at the one payment whose amount equals the group's sum."""
    groups: dict[tuple, list[dict]] = {}
    for d in res.details:
        if d.get("detail_of"):
            continue
        groups.setdefault((d["section"], d.get("statement") if d["section"] == "Fuel Card" else ""), []).append(d)
    for (section, group), rows in groups.items():
        total = round(sum(r["amount"] or 0 for r in rows), 2)
        parents = [p for p in res.payments if p["amount"] is not None and abs(p["amount"] - total) < 0.005]
        if len(parents) == 1:
            parent = parents[0]["ref"] or parents[0]["row_key"]
            note = ""
        else:
            parent, note = "", f"no single payment of {total:,.2f} found to link"
        for r in rows:
            r["detail_of"] = parent
            if note:
                r["parse_note"] = (r["parse_note"] + "; " + note).strip("; ")


# ---------------------------------------------------------------- Shire of Collie

def collie(lines: list[Line]) -> Result:
    """Schedule of Paid Accounts (14.3.A) and Purchasing Card Payments (14.3.B).

    Header 'Date Method [EFT] Batch Invoice Reference Vendor Description Fund Amount'. The
    header words sit centred over their columns, so column edges are offsets from them.
    Sub-headings name the payment type (Electronic Funds Transfer, Payment by Petty Cash,
    Payroll, Payments made by direct debit / purchasing card / credit card). Amounts have
    no thousands separators. Each schedule ends with its total on a line by itself. A pay
    run's 'STAFF REIMBURSEMENT' and 'STAFF DEDUCTION' lines are already inside the pay run
    amount, so they are details of it.
    """
    headings = {"Electronic Funds Transfer": "EFT", "Payment by Petty Cash": "Petty Cash", "Payroll": "Payroll",
                "Payments made by direct debit": "Direct Debit", "Payments made by purchasing card": "Purchasing Card",
                "Payment made by credit card": "Credit Card"}
    schedules = {"Schedule of Paid Accounts - 16 August 2026 - 15 September 2026": "14.3.A",
                 "Schedule of Paid Accounts - Purchasing Card Payments": "14.3.B"}
    res = Result()
    schedule, section, cols = None, None, None
    body: list[Line] = []

    def date_of(line: Line) -> str | None:
        first = line.words[0]
        m = DATE.match(first.text)
        return m.group(1) if m and first.x0 < dict(cols)["method"] else None

    def flush():
        if not body:
            return
        amt_x = dict(cols)["amount"]
        for group in attach(body, lambda l: date_of(l) is not None):
            anchor = next(l for l in group if date_of(l))
            parent = None
            main: list[Line] = []
            for l in group:
                # A line under a pay run with its own amount is a reimbursement or deduction.
                value, _ = trailing_amount([w for w in l.words if w.x0 >= amt_x])
                if l is not anchor and value is not None and section == "Payroll":
                    res.details.append(_row(l, section, paid_date=parent["paid_date"], ref=parent["ref"],
                                            payee_raw=parent["payee_raw"], amount=value,
                                            detail_of=parent["ref"] or parent["row_key"], schedule=schedule,
                                            description=_clean(" ".join(w.text for w in l.words if w.x0 < amt_x)),
                                            parse_note="included in the pay run amount"))
                    continue
                main.append(l)
                if l is anchor:
                    parent = _collie_row(main, anchor, cols, section, schedule)
            _collie_row(main, anchor, cols, section, schedule, into=parent)
            res.payments.append(parent)
        body.clear()

    for line in lines:
        text = line.text
        if text in schedules:
            flush()
            schedule = schedules[text]
            continue
        if line.find("Method") and line.find("Vendor"):
            flush()
            cols = columns(line, [("date", "Date", 0, -10), ("method", "Method", 0, -2), ("batch", "Invoice", 0, -50),
                                  ("invoice", "Invoice", 0, -5), ("vendor", "Vendor", 0, -90),
                                  ("description", "Description", 0, -112), ("fund", "Fund", 0, -10),
                                  ("amount", "Fund", 0, 45)])
            continue
        if text in headings:
            flush()
            section = headings[text]
            continue
        if cols is None or text in ("SHIRE OF COLLIE", "EFT", "Batch", "16 August 2026 - 15 September 2026") \
                or text.startswith("SHIRE OF COLLIE 14.3"):
            continue
        if all(w.x0 >= dict(cols)["fund"] for w in line.words) and amount("".join(w.text for w in line.words)) is not None:
            flush()
            res.totals.append(_total(schedule, amount("".join(w.text for w in line.words)), line, "schedule",
                                     f"Schedule {schedule} total"))
            continue
        if re.fullmatch(r"\d{1,2}", text) and line.words[0].x0 > 380:
            continue  # page number
        body.append(line)
    flush()
    return res


def _collie_row(group: list[Line], anchor: Line, cols, section: str, schedule: str, into: dict | None = None) -> dict:
    amt_x = dict(cols)["amount"]
    cells: dict[str, list[str]] = {k: [] for k, _ in cols}
    for l in group:
        words = []
        for w in l.words:
            if w.x0 >= amt_x:
                continue
            m = DATE.match(w.text)
            if l is anchor and m and m.group(2) and w.x0 < dict(cols)["method"]:
                # '14/09/2026DD': the date and the method run together.
                words += [Word(m.group(1), w.x0, w.x0 + 1, w.top, w.bold), Word(m.group(2), dict(cols)["method"], w.x1, w.top, w.bold)]
            elif w.text.endswith("BENMUNI") and w.text != "BENMUNI":
                words += [Word(w.text[:-7], w.x0, w.x1, w.top, w.bold)]
            elif w.text == "BENMUNI":
                continue
            else:
                words.append(w)
        for k, v in split(l, cols, words).items():
            if v:
                cells[k].append(v)
    get = {k: _clean(" ".join(v)) for k, v in cells.items()}
    value, _ = trailing_amount([w for w in anchor.words if w.x0 >= amt_x])
    row = _row(anchor, section, paid_date=to_date(get["date"]), ref=get["batch"], invoice_ref=get["invoice"].replace(" ", "")
               if section == "Credit Card" else get["invoice"], payee_raw=get["vendor"], description=get["description"],
               amount=value, schedule=schedule, method=get["method"])
    if value is None:
        row["parse_note"] = "no amount"
    if into is not None:
        into.update({k: row[k] for k in ("invoice_ref", "payee_raw", "description")})
        return into
    return row


# ---------------------------------------------------------------- City of Nedlands

def nedlands(lines: list[Line]) -> Result:
    """List of Accounts Paid: a bold payment line (ref, date, creditor number, creditor
    name, amount) followed by plain invoice lines (invoice number, description, amount).

    Section lines carry their own totals ('Creditor EFT 1,982,841.17', 'Sundry EFT',
    'Direct Debit', 'Cancelled Payments', 'Payroll'), and the list ends with 'TOTAL
    PAYMENTS'. Payroll lines have no payment ref: the pay period is the ref. Read with
    text_flow so a long creditor name that runs into the amount column stays apart from
    the amount.
    """
    sections = ("Creditor EFT", "Sundry EFT", "Direct Debit", "Cancelled Payments", "Payroll")
    res = Result()
    section, cols, current = None, None, None
    for line in lines:
        text = line.text
        if line.find("Payment") and line.find("Reference") and line.find("Amount"):
            cols = columns(line, [("ref", "Payment", 0, -2), ("date", "Payment", 1, -2), ("creditor_no", "Creditor", 0, -2),
                                  ("name", "Creditor", 1, -3), ("sub_amount", "Payment", 2, -60), ("amount", "Amount", 0, -15)])
            continue
        if cols is None or text.startswith(("Ordinary Council Meeting", "City of Nedlands - List", "For the Period",
                                            "Attachment16.5.1")) or re.match(r"^Page \d+ of \d+$", text):
            continue
        m = re.match(rf"^({'|'.join(sections)}|TOTAL PAYMENTS) ([\d,]+\.\d\d)$", text)
        if m:
            current = None
            if m.group(1) == "TOTAL PAYMENTS":
                res.totals.append(_total("", amount(m.group(2)), line, "grand", "TOTAL PAYMENTS"))
            else:
                section = m.group(1)
                res.totals.append(_total(section, amount(m.group(2)), line, label=section))
            continue
        cells = split(line, cols)
        d = DATE.match(cells["date"].split(" ")[0]) if cells["date"] else None
        if line.words[0].bold and d:
            amt_words = [w for w in line.words if w.x1 > dict(cols)["amount"] + 10 and amount(w.text) is not None]
            name_words = [w for w in line.words if dict(cols)["name"] <= w.x0 and w not in amt_words]
            value = amount(amt_words[-1].text) if amt_words else None
            last, note = (name_words[-1] if name_words else None), ""
            fused = re.match(r"^(.*?\D)(\d{1,3}(?:,\d{3})*\.\d{2})$", last.text) if last and value is None else None
            if fused and last.x1 > dict(cols)["amount"] + 10:
                # 'Watering2,112.00': the name ran up against the amount with no space.
                value = amount(fused.group(2))
                name_words[-1] = Word(fused.group(1), last.x0, last.x1, last.top, last.bold)
                note = "amount printed against the name; split off and checked against the invoice lines"
            ref = _clean(cells["ref"])
            current = _row(line, section, paid_date=to_date(d.group(1)), ref=ref,
                           payee_raw=_clean(" ".join(w.text for w in name_words)),
                           amount=value, creditor_no=_clean(cells["creditor_no"]), parse_note=note)
            if value is None:
                current["parse_note"] = "payment amount unreadable"
            res.payments.append(current)
            continue
        if current is None:
            continue
        value, _ = trailing_amount([w for w in line.words if w.x0 >= dict(cols)["sub_amount"]])
        inv = _clean(" ".join(w.text for w in line.words if w.x0 < dict(cols)["date"]))
        desc = _clean(" ".join(w.text for w in line.words if dict(cols)["date"] <= w.x0 < dict(cols)["sub_amount"]))
        if value is None:
            # A description that wraps onto a second line.
            target = res.details[-1] if res.details and res.details[-1]["detail_of"] == current["ref"] else current
            target["description"] = _clean(f"{target['description']} {line.text}")
            continue
        res.details.append(_row(line, section, paid_date=current["paid_date"], ref=current["ref"], invoice_ref=inv,
                                payee_raw=current["payee_raw"], description=desc, amount=value, detail_of=current["ref"]))
    # Describe each payment by its invoice lines, and check each payment against them.
    by_ref: dict[str, list[dict]] = {}
    for d in res.details:
        by_ref.setdefault(d["detail_of"], []).append(d)
    for p in res.payments:
        subs = by_ref.get(p["ref"], [])
        p["description"] = "; ".join(s["description"] for s in subs)[:500]
        sub_total = round(sum(s["amount"] for s in subs), 2)
        if p["amount"] is None and subs:
            p["amount"] = sub_total
            p["parse_note"] = "payment amount unreadable; taken from the sum of its invoice lines"
        elif subs and abs(sub_total - p["amount"]) >= 0.005:
            p["parse_note"] = (p["parse_note"] + f"; invoice lines sum to {sub_total:,.2f}").strip("; ")
    return res


LAYOUTS = {
    "toodyay_warrant_listing": (toodyay, {}),
    "coolgardie_delegated_authority": (coolgardie, {"x_tolerance": 3}),
    "collie_schedule_paid_accounts": (collie, {}),
    "nedlands_accounts_paid": (nedlands, {"text_flow": True}),
}


def parse(path, layout: str, pages) -> Result:
    fn, read_opts = LAYOUTS[layout]
    return fn(read_lines(path, page_range(pages), **read_opts))
