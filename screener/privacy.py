"""Keep private individuals' names out of the committed parsed files (SPEC.md rule 6).

Parsed rows from real council files are written to data/parsed/, which is committed. A
payee who is a private individual (a ratepayer refund, a consignment seller, a scholarship
recipient, a committee member, a staff reimbursement, anyone paid under their own name
with no business name) is written as a stable code, PRIVATE-0001, and the same name always
gets the same code. Businesses, government bodies and councillors keep their names.

The name-to-code lookup is data/private/payee_codes.csv, which git ignores. It is read
before each run and new names are appended with the next number, so codes never change.
Every name in it is private, so adding a name there by hand forces it to be coded.
config/payee_not_private.txt lists business names that look like a person's name
("Super Choice", "Red dot"); it holds no individuals and is committed.

A name is private when it is in the lookup, or when it looks like a person's name: two to
five words of letters or initials, with no business, government or trading word, not a
councillor or mayor, and not in the not-private list. That rule leans towards coding a
business by mistake rather than publishing a person; new names it codes are printed so a
person can move a business to the not-private list.

Every name in the lookup is also replaced wherever it appears in the description or
statement of any row, and credit card statement headers have the cardholder's name coded.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

CODE_PREFIX = "PRIVATE-"
LOOKUP_COLUMNS = ["code", "name_key", "name", "first_council", "reason"]
TEXT_COLUMNS = ("description", "statement")

_COUNCILLOR = re.compile(r"^\s*(Cr|Clr|Councillor|Mayor|Deputy Mayor|President|Shire President)\b\.?\s", re.I)
_TITLES = {"MR", "MRS", "MS", "MISS", "DR"}
_PERSON_TOKEN = re.compile(r"^[A-Z](?:[A-Z'\-]*[A-Z])?\.?$")
_CARDHOLDER = re.compile(r"^(Credit [Cc]ard\s*-\s*[A-Z]+\s*-\s*)(.+?)(\s*-\s*[A-Za-z]+ \d{4})$")

# Words that mark a business, government body, trading name or service, never a person.
BUSINESS_WORDS = {
    "PTY", "PTD", "LTD", "LIMITED", "P/L", "INC", "INCORPORATED", "CO", "COMPANY", "CORPORATION", "CORP",
    "GROUP", "HOLDINGS", "ENTERPRISES", "INVESTMENTS", "TRUST", "TRUSTEE", "T/A", "T/AS", "ATF", "PARTNERSHIP",
    "SERVICES", "SERVICE", "SOLUTIONS", "SUPPLIES", "SUPPLY", "CONSULTING", "CONSULTANCY", "CONSULTANTS",
    "CONTRACTING", "CONSTRUCTIONS", "ENGINEERING", "ELECTRICAL", "PLUMBING", "MECHANICAL", "LANDSCAPING",
    "TRANSPORT", "HIRE", "RENTALS", "MOTORS", "TYRES", "TYREPOWER", "HARDWARE", "TRADERS", "TRADING",
    "ASSOCIATION", "ASSOC", "SOCIETY", "CLUB", "COUNCIL", "SHIRE", "CITY", "TOWN", "DEPARTMENT", "DEPT",
    "OFFICE", "AUTHORITY", "COMMISSION", "BANK", "AUSTRALIA", "AUSTRALIAN", "WA", "INTERNATIONAL",
    "MUSEUM", "HOTEL", "HOTELS", "MOTEL", "MOTELS", "CAFE", "BAKERY", "BUTCHERS", "NEWSAGENCY", "PRESS",
    "PUBLISHING", "PUBLICATIONS", "STUDIO", "LEATHER", "QUILTS", "SCHOOL", "CENTRE", "CENTER", "COLLEGE",
    "TAFE", "UNIVERSITY", "PAYROLL", "SUPER", "CASH", "FEE", "FEES", "REFUND", "CARD", "POST", "POWER",
    "WATER", "ENERGY", "GAS", "INSURANCE", "LAWYERS", "SOLICITORS", "BARRISTERS", "SURVEYORS", "VETS",
    "NURSERIES", "TURF", "EQUIPMENT", "SECURITY", "SHOP", "STORE", "WAREHOUSE", "MARKET", "RECRUITMENT",
    "APPOINTMENTS", "REAL", "ESTATE", "PROPERTY", "HOMES", "BUILDING", "BUILDERS", "PAINTING", "CLEANING",
    "REMOVAL", "WASTE", "RECYCLING", "TECHNOLOGIES", "TECHNOLOGY", "TECH", "SOFTWARE", "SYSTEMS",
    "COMMUNICATIONS", "MEDIA", "NEWSPAPER", "NEWSPAPERS", "HERALD", "BULLETIN", "PROMOTIONS", "PHOTOGRAPHY",
    "SHADE", "FENCING", "DOORS", "GLASS", "AUTOGLASS", "WINDSCREENS", "MARQUEES", "BINS", "TOOLS", "PARTS",
    "INDUSTRIES", "INDUSTRIAL", "MANAGEMENT", "PLANNING", "MAPPING", "ADVANTAGE", "CAPITAL", "FINANCIAL",
    "LEASING", "UNION", "REGISTRAR", "OFFICEWORKS", "BUNNINGS", "WOOLWORTHS", "COLES", "KMART", "IGA",
    "COMMERCE", "INDUSTRY", "GARAGE", "RESORT", "AGENCY", "AGENCIES", "BEARINGS", "WELDING", "MUNICIPAL",
    "PETROLEUM", "BUSINESS", "COLLECTIVE", "ICT", "STORAGE", "RECORDS", "AMBULANCE", "VILLAGE", "HOUSE",
    "IMAGING", "APPAREL", "OUTDOORS", "CANVAS", "ENGRAVING", "LINEMARKING", "COPIERS", "EXHAUST",
    "LOCKSERVICE", "PARK", "CLEARING", "SPRINGS", "ALARM", "TECHNICIANS", "SIGNMAKERS", "MOWERS",
    "TRAINING", "FITNESS", "TRANSFORMERS", "PRESENTS", "CEMETERIES", "CREMATORIA", "CULTURE",
}


def is_councillor(name: str) -> bool:
    return bool(_COUNCILLOR.match(name or ""))


def name_key(name: str) -> str:
    """Upper case, punctuation dropped except name hyphens and apostrophes, whitespace collapsed."""
    s = re.sub(r"[^A-Z0-9'\- ]+", " ", (name or "").upper().replace("’", "'"))
    return " ".join(s.split())


def looks_like_person(name: str) -> bool:
    """Two to five words that are all letters or initials, none a business word."""
    raw = (name or "").strip()
    if not raw or is_councillor(raw) or re.search(r"\d", raw):
        return False
    if "," in raw:  # "Surname, Given Names"
        surname, _, given = raw.partition(",")
        raw = f"{given} {surname}"
    tokens = [t for t in raw.upper().replace("&", " ").split() if t not in _TITLES]
    if not 2 <= len(tokens) <= 5:
        return False
    if any(t.strip(".") in BUSINESS_WORDS for t in tokens):
        return False
    return all(_PERSON_TOKEN.match(t) for t in tokens)


class PrivateNames:
    """The name-to-code lookup, the not-private list, and the coding of rows."""

    def __init__(self, lookup_path: Path, not_private_path: Path | None = None):
        self.lookup_path = Path(lookup_path)
        self.codes: dict[str, dict] = {}
        if self.lookup_path.exists():
            with open(self.lookup_path, encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    self.codes[r["name_key"]] = r
        self.not_private: set[str] = set()
        if not_private_path and Path(not_private_path).exists():
            for line in Path(not_private_path).read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    self.not_private.add(name_key(line))
        self.new: list[dict] = []

    def is_private(self, name: str) -> bool:
        key = name_key(name)
        if not key or key.startswith(CODE_PREFIX):
            return False
        if key in self.codes:
            return True
        return key not in self.not_private and looks_like_person(name)

    def code(self, name: str, council_id: str = "", reason: str = "payee") -> str:
        key = name_key(name)
        if key not in self.codes:
            n = max((int(r["code"][len(CODE_PREFIX):]) for r in self.codes.values()), default=0) + 1
            entry = {"code": f"{CODE_PREFIX}{n:04d}", "name_key": key, "name": name.strip(),
                     "first_council": council_id, "reason": reason}
            self.codes[key] = entry
            self.new.append(entry)
        return self.codes[key]["code"]

    def redact_rows(self, rows: list[dict]) -> int:
        """Code private payees and cardholders in place, then replace every private name
        in the description and statement of every row. Return how many rows changed."""
        before = [tuple(r.get(c) for c in ("payee_raw", *TEXT_COLUMNS)) for r in rows]
        for r in rows:
            council = r.get("council_id", "")
            payee = r.get("payee_raw") or ""
            if self.is_private(payee):
                r["payee_raw"] = self.code(payee, council)
            m = _CARDHOLDER.match(r.get("statement") or "")
            if m and not m.group(2).startswith(CODE_PREFIX):
                r["statement"] = m.group(1) + self.code(m.group(2), council, "cardholder") + m.group(3)
        patterns = [(re.compile(rf"(?<!\w){re.escape(e['name'])}(?!\w)", re.I), e["code"])
                    for e in sorted(self.codes.values(), key=lambda e: -len(e["name"]))]
        for r in rows:
            for col in TEXT_COLUMNS:
                text = r.get(col) or ""
                for pattern, code in patterns:
                    text = pattern.sub(code, text)
                r[col] = text
        return sum(b != tuple(r.get(c) for c in ("payee_raw", *TEXT_COLUMNS)) for b, r in zip(before, rows))

    def save(self) -> None:
        self.lookup_path.parent.mkdir(parents=True, exist_ok=True)
        rows = sorted(self.codes.values(), key=lambda r: r["code"])
        with open(self.lookup_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LOOKUP_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
