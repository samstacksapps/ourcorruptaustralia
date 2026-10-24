"""Council accounts-paid layouts, tested on synthetic word positions rather than real files."""

import pytest

from screener.parse import payment_layouts as pl
from screener.parse.payment_layouts import Word, group_lines


def words(page_rows, page=1):
    """[(top, [(x0, text), ...]), ...] -> Lines; each word is 4pt wide per character."""
    ws = [Word(text, x0, x0 + 4 * len(text), top, False) for top, cells in page_rows for x0, text in cells]
    return group_lines(ws, page)


@pytest.mark.parametrize("text, expected", [
    ("1,351.63", 1351.63), ("$ 742.00", 742.0), ("-$ 2 4.00", -24.0), ("6 ,184.54", 6184.54),
    ("9347.47", 9347.47), ("(12.00)", -12.0), ("2026", None), ("1,2,3.00", None), ("12.5", None),
])
def test_amount(text, expected):
    assert pl.amount(text) == expected


def test_trailing_amount_takes_the_longest_run_that_parses():
    ws = [Word(t, x, x + 5, 0, False) for x, t in [(0, "Airport"), (10, "1,203"), (20, "$"), (30, "2,724.68")]]
    value, rest = pl.trailing_amount(ws)
    assert value == 2724.68 and [w.text for w in rest] == ["Airport", "1,203"]


def test_words_within_three_points_share_a_line():
    lines = words([(100, [(10, "01/08/2026"), (60, "Fee")]), (101, [(30, "Commonwealth")]), (110, [(10, "next")])])
    assert [l.text for l in lines] == ["01/08/2026 Commonwealth Fee", "next"]


def test_split_assigns_words_by_column_edges():
    line = words([(100, [(10, "EFT1"), (50, "Acme"), (70, "Pty"), (120, "Repairs"), (200, "10.00")])])[0]
    cols = [("ref", 8), ("payee", 48), ("description", 118), ("amount", 190)]
    assert pl.split(line, cols) == {"ref": "EFT1", "payee": "Acme Pty", "description": "Repairs", "amount": "10.00"}


def test_attach_nearest_puts_a_wrapped_reference_above_its_row():
    # A centred row: half the reference sits 4pt above the anchor, half 5pt below.
    lines = words([(94, [(10, "01/09/2026"), (60, "A")]), (104, [(40, "INV-1-")]),
                   (108, [(10, "02/09/2026"), (60, "B")]), (113, [(40, "47")]), (122, [(10, "03/09/2026"), (60, "C")])])
    rows = pl.attach(lines, lambda l: l.words[0].text.endswith("2026"))
    assert [[l.text for l in r] for r in rows] == [["01/09/2026 A"], ["INV-1-", "02/09/2026 B", "47"], ["03/09/2026 C"]]


def test_attach_previous_keeps_wraps_with_the_row_above():
    lines = words([(100, [(10, "EFT1"), (60, "Plumbing")]), (108, [(60, "Services")]), (117, [(10, "EFT2")])])
    rows = pl.attach(lines, lambda l: l.words[0].text.startswith("EFT"), nearest=False)
    assert [[l.text for l in r] for r in rows] == [["EFT1 Plumbing", "Services"], ["EFT2"]]


def _toodyay_page():
    header = [(140, "Payment"), (161, "Number"), (193, "Date"), (224, "Invoice"), (241, "No"), (281, "Creditor"),
              (299, "Name"), (443, "Invoice"), (460, "Description"), (657, "Inclusive"), (677, "Amount")]
    return words([
        (100, [(140, "Electronic"), (190, "Funds"), (230, "Transfer"), (270, "Payments")]),
        (108, header),
        (116, [(140, "CP.1"), (193, "6/08/2026"), (224, "INV-1"), (281, "Acme"), (300, "Pty"), (443, "Repairs"), (675, "1,351.63")]),
        (123, [(140, "CP.1"), (193, "6/08/2026"), (224, "77"), (281, "Beta"), (443, "Hire"), (679, "248.85")]),
        (130, [(443, "EFT"), (460, "Total"), (675, "1,600.48")]),
        (140, [(140, "Credit"), (165, "Card")]),
        (148, header),
        (156, [(193, "2/08/2026"), (281, "Credit"), (300, "Card"), (320, "-"), (330, "CEO"), (443, "Officeworks-Office"), (679, "470.75")]),
        (163, [(193, "5/08/2026"), (443, "Refund-licence"), (677, "-290.67")]),
        (170, [(443, "Credit"), (465, "Card"), (490, "Total"), (679, "180.08")]),
        (177, [(443, "Grand"), (465, "Total"), (675, "1,780.56")]),
    ])


def test_toodyay_layout_reads_sections_and_totals():
    res = pl.toodyay(_toodyay_page())
    assert [(r["section"], r["payee_raw"], r["amount"]) for r in res.payments] == [
        ("EFT", "Acme Pty", 1351.63), ("EFT", "Beta", 248.85),
        ("Credit Card", "Officeworks", 470.75), ("Credit Card", "Refund", -290.67)]
    assert res.payments[0]["ref"] == "CP.1" and res.payments[0]["invoice_ref"] == "INV-1"
    assert res.payments[3]["statement"] == "Credit Card - CEO", "later card rows keep the statement"
    assert all(c["ok"] for c in pl.check(res)) and len(res.totals) == 3


def test_check_reports_a_missed_total():
    res = pl.toodyay(_toodyay_page())
    res.payments.pop(1)
    failed = [c for c in pl.check(res) if not c["ok"]]
    assert [(c["label"], c["difference"]) for c in failed] == [("EFT Total", -248.85), ("Grand Total", -248.85)]


def test_card_settlement_moves_to_details_and_fuel_details_link_to_their_eft():
    res = pl.Result(
        payments=[{"section": "EFT", "ref": "EFT9", "row_key": "p1l1", "amount": 30.0, "description": "Diesel", "invoice_ref": ""},
                  {"section": "Direct Debit", "ref": "", "row_key": "p2l1", "amount": 15.0,
                   "description": "Cba Cr Card Pmnt", "invoice_ref": "Corporate Credit Card"},
                  {"section": "Credit Card", "ref": "", "row_key": "p3l1", "amount": 15.0, "description": "", "invoice_ref": ""}],
        details=[{"section": "Fuel Card", "statement": "Ampol", "amount": 10.0, "parse_note": ""},
                 {"section": "Fuel Card", "statement": "Ampol", "amount": 20.0, "parse_note": ""}])
    pl._settle_card(res)
    pl._link_details(res)
    assert [p["section"] for p in res.payments] == ["EFT", "Credit Card"]
    assert [d.get("detail_of") for d in res.details] == ["EFT9", "EFT9", "Credit Card"]


def test_page_range():
    assert pl.page_range("29-31,40") == [29, 30, 31, 40]
