"""FOI request drafts (SPEC.md section 11)."""

from datetime import date

from screener import foi


def test_range_starts_twelve_months_before_first_payment():
    assert foi.date_range(date(2024, 9, 19), date(2024, 12, 12), None) == (date(2023, 9, 19), date(2024, 12, 12))


def test_range_starts_at_an_earlier_agenda_item():
    assert foi.date_range(date(2024, 9, 19), date(2024, 12, 12), date(2023, 5, 25)) == (date(2023, 5, 25), date(2024, 12, 12))


def test_later_agenda_item_does_not_move_the_start():
    assert foi.date_range(date(2024, 9, 19), date(2024, 12, 12), date(2024, 8, 22))[0] == date(2023, 9, 19)


def test_range_with_no_payments_uses_the_item():
    assert foi.date_range(None, None, date(2025, 3, 27)) == (date(2025, 3, 27), date(2025, 3, 27))


def test_leap_day_first_payment():
    assert foi.date_range(date(2024, 2, 29), date(2024, 6, 1), None)[0] == date(2023, 2, 28)


def test_draft_names_the_act_period_and_clock():
    text = foi.draft("Shire of Example", "council", "Cardell Strategic Consulting Pty Ltd",
                     date(2024, 9, 19), date(2024, 12, 12), date(2024, 8, 22))
    assert "Freedom of Information Act 1992 (WA)" in text
    assert "between 19 September 2023 and 12 December 2024" in text
    assert "within 45 days" in text
    assert foi.due_date(date(2025, 1, 1), "agency") == date(2025, 1, 31)
