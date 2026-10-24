"""Shared helpers: build a database from fixture records the same way `run --fixture` does."""

from __future__ import annotations

import copy
import sys
import zlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fixtures"))

import make_fixture  # noqa: E402

from screener import db, parse, score, settings  # noqa: E402

CID = make_fixture.CID


@dataclass
class Run:
    con: object
    hits: list
    cases: list
    path: Path

    def entity(self, raw_name: str) -> str:
        from screener.normalise import normalise_name
        row = self.con.execute("SELECT entity_id FROM entity_links WHERE name_norm = ?", [normalise_name(raw_name)]).fetchone()
        assert row, f"{raw_name} is not in the database"
        return row[0]

    def flagged(self, flag: str) -> set:
        return {h["entity_id"] for h in self.hits if h["flag"] == flag and not h["held"]}


def build(records: dict, tmp: Path, *, pdf: bool = False, decisions: dict | None = None) -> Run:
    raw = tmp / "raw" / "2025-07-15"
    make_fixture.write(records, raw, pdf=pdf)
    parse.parse_folder(raw, tmp / "parsed", ROOT)
    con = db.connect(tmp / "screener.duckdb")
    db.create_schema(con)
    db.load_parsed(con, tmp / "parsed")
    th, fc = settings.thresholds(), settings.flag_config()
    db.resolve_entities(con, th, confirmed=(decisions or {}).get("confirmed_names", []))
    hits = score.run_flags(con, th, fc)
    cases = score.build_cases(con, th, fc, decisions)
    return Run(con, hits, cases, tmp)


def clean_records() -> dict:
    return copy.deepcopy(make_fixture.build_records(planted=False))


def add_payee(records, name, payments, *, abn_since: date | None = date(2010, 1, 1)):
    """Add a payee with (date, amount, description) payments and, unless abn_since is None, an ABN."""
    if abn_since is not None:
        records["abn"].append({"abn": f"9{zlib.crc32(name.encode()) % 10**10:010d}", "name_raw": name, "entity_type": "PRV",
                               "status": "ACT", "status_from": abn_since, "state": "WA", "postcode": "6000"})
    for i, (d, amount, desc) in enumerate(payments):
        records["payments"].append({"paid_date": d, "payee_raw": name, "description": desc, "amount": amount,
                                    "ref": f"EFT9{len(records['payments']):04d}"})


def add_tender(records, ref, awarded, winner, amount, losers=()):
    base = {"tender_ref": ref, "description": f"Test tender {ref}", "closed_date": awarded, "awarded_date": awarded}
    records["tenders"].append({**base, "tenderer_raw": winner, "is_winner": True, "contract_amount": amount})
    for loser in losers:
        records["tenders"].append({**base, "tenderer_raw": loser, "is_winner": False, "contract_amount": None})


@pytest.fixture(scope="session")
def clean_run(tmp_path_factory) -> Run:
    return build(clean_records(), tmp_path_factory.mktemp("clean"))


@pytest.fixture(scope="session")
def planted_run(tmp_path_factory) -> Run:
    """The full fixture, accounts paid read from the PDF end to end."""
    return build(make_fixture.build_records(planted=True), tmp_path_factory.mktemp("planted"), pdf=True)
