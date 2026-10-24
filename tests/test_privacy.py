"""Private individuals' names are coded before parsed rows are written (screener/privacy.py).
All names here are made up."""

from __future__ import annotations

from screener import privacy


def _names(tmp_path, keep=()):
    keep_file = tmp_path / "not_private.txt"
    keep_file.write_text("# businesses\n" + "\n".join(keep) + "\n", encoding="utf-8")
    return privacy.PrivateNames(tmp_path / "private" / "payee_codes.csv", keep_file)


def test_looks_like_person():
    for name in ["Harriet Quill", "QUENTIN ALBERT MOSS", "Mr P Lumm", "Lumm, Petra Jane", "D R Zabel", "G & K Ostrow"]:
        assert privacy.looks_like_person(name), name
    for name in ["Harriet Quill Pty Ltd", "Cr P. Lumm", "Mayor Q. Moss", "Synergy", "Quill Plumbing", "Moss Family Trust",
                 "Landgate", "Water Corporation", "Shire of Example", "150 Square", "Ostrow Electrical Services"]:
        assert not privacy.looks_like_person(name), name


def test_private_payees_coded_and_businesses_kept(tmp_path):
    names = _names(tmp_path, keep=["Rusty Anchor"])
    rows = [
        {"council_id": "shire-a", "payee_raw": "Harriet Quill", "description": "Rates refund - Harriet Quill"},
        {"council_id": "shire-a", "payee_raw": "Rusty Anchor", "description": "Catering"},
        {"council_id": "shire-a", "payee_raw": "Cr P. Lumm", "description": "Councillor allowance"},
        {"council_id": "shire-a", "payee_raw": "Quill Plumbing Pty Ltd", "description": "Repairs for HARRIET QUILL"},
    ]
    names.redact_rows(rows)
    assert rows[0]["payee_raw"] == "PRIVATE-0001"
    assert rows[0]["description"] == "Rates refund - PRIVATE-0001"
    assert rows[1]["payee_raw"] == "Rusty Anchor"
    assert rows[2]["payee_raw"] == "Cr P. Lumm"
    assert rows[3]["payee_raw"] == "Quill Plumbing Pty Ltd"
    assert rows[3]["description"] == "Repairs for PRIVATE-0001"


def test_codes_are_stable_across_runs_and_councils(tmp_path):
    first = _names(tmp_path)
    first.redact_rows([{"council_id": "shire-a", "payee_raw": "Harriet Quill"},
                       {"council_id": "shire-a", "payee_raw": "Petra Lumm"}])
    first.save()
    second = _names(tmp_path)
    rows = [{"council_id": "shire-b", "payee_raw": "Petra Lumm"}, {"council_id": "shire-b", "payee_raw": "HARRIET  QUILL"},
            {"council_id": "shire-b", "payee_raw": "Quentin Moss"}]
    second.redact_rows(rows)
    assert [r["payee_raw"] for r in rows] == ["PRIVATE-0002", "PRIVATE-0001", "PRIVATE-0003"]
    assert [e["code"] for e in second.new] == ["PRIVATE-0003"]


def test_lookup_entry_forces_coding(tmp_path):
    names = _names(tmp_path)
    names.code("Quill Family Trust", "shire-a", "added by hand")
    rows = [{"council_id": "shire-a", "payee_raw": "Quill Family Trust", "description": "Consignment"}]
    names.redact_rows(rows)
    assert rows[0]["payee_raw"] == "PRIVATE-0001"


def test_cardholder_in_statement_coded(tmp_path):
    names = _names(tmp_path)
    rows = [{"council_id": "shire-a", "payee_raw": "Officeworks", "description": "Officeworks-Office equipment",
             "statement": "Credit Card - CEO - P Lumm - August 2026"}]
    names.redact_rows(rows)
    assert rows[0]["statement"] == "Credit Card - CEO - PRIVATE-0001 - August 2026"
    assert rows[0]["payee_raw"] == "Officeworks"
