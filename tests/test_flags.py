"""Every council flag: plant the pattern in clean data and check it fires; check it does not
fire on the clean data alone (SPEC.md section 7)."""

from datetime import date

import pytest

from conftest import CID, add_payee, add_tender, build, clean_records
from screener import settings

COUNCIL_FLAGS = [n for n, s in settings.flag_config()["flags"].items() if s["layer"] == "council"]


def test_all_fourteen_council_flags_have_sql():
    assert len(COUNCIL_FLAGS) == 14
    for name in COUNCIL_FLAGS:
        assert (settings.FLAGS_DIR / f"{name}.sql").exists()


@pytest.mark.parametrize("flag", COUNCIL_FLAGS)
def test_flag_quiet_on_clean_data(clean_run, flag):
    hits = [h for h in clean_run.hits if h["flag"] == flag]
    assert hits == [], f"{flag} fired on clean data: {[h['detail'] for h in hits]}"


def test_clean_data_surfaces_no_cases(clean_run):
    assert clean_run.cases == []


# Each planting function adds one pattern to the clean records and returns the raw name of
# the entity (or None for the body-level context flag) that must carry the flag.

def plant_over_threshold_no_tender(r):
    add_payee(r, "Zenith Civil Pty Ltd", [(date(2024, 8, 14), 90000.0, "Culvert works"),
                                          (date(2024, 12, 5), 95000.0, "Culvert works"),
                                          (date(2025, 3, 20), 80000.0, "Culvert works")])
    return "Zenith Civil Pty Ltd"


def plant_just_under_limit(r):
    add_payee(r, "Avon Valley Plant Hire Pty Ltd", [(date(2024, 9, 2), 19500.0, "Plant hire"),
                                                     (date(2024, 11, 4), 19250.0, "Plant hire"),
                                                     (date(2025, 1, 13), 19900.0, "Plant hire")], abn_since=None)
    return "Avon Valley Plant Hire Pty Ltd"


def plant_repeat_round_amount(r):
    add_payee(r, "Skyline Printing", [(date(2024, 8, 1), 2000.40, "Printing"), (date(2024, 10, 1), 1980.00, "Printing"),
                                      (date(2024, 12, 2), 2020.00, "Printing"), (date(2025, 2, 3), 2049.00, "Printing")],
              abn_since=None)
    return "Skyline Printing"


def plant_new_abn_payee(r):
    add_payee(r, "Fresh Start Services", [(date(2024, 8, 1), 8000.0, "Services"), (date(2024, 9, 2), 8500.0, "Services"),
                                          (date(2024, 10, 1), 7600.0, "Services")], abn_since=date(2024, 6, 1))
    return "Fresh Start Services"


def plant_unmatched_payee(r):
    add_payee(r, "Nowhere Contracting", [(date(2024, 8, 1), 12000.0, "Works"), (date(2025, 1, 6), 12500.0, "Works")],
              abn_since=None)
    return "Nowhere Contracting"


def plant_gift_from_payee(r):
    r["gifts"].append({"recipient_name": "Lisa Corrigan", "recipient_role": "Councillor", "giver_raw": "Bluegum Cleaning Services",
                       "value": 400.0, "received_date": date(2024, 12, 1), "description": "Hamper"})
    return "Bluegum Cleaning Services"


def plant_interest_matches_payee(r):
    r["interests"].append({"person": "Lisa Corrigan", "role": "Councillor", "interest_type": "business",
                           "entity_raw": "Northam Office Supplies", "detail": "Part owner", "return_type": "annual",
                           "return_date": date(2024, 8, 30)})
    return "Northam Office Supplies"


def plant_undeclared_at_meeting(r):
    r["items"].append({"meeting_date": date(2025, 2, 27), "item_ref": "10.4", "item_title": "Freight services arrangement",
                       "entities_mentioned": ["Whitby Transport Pty Ltd"]})
    return "Whitby Transport Pty Ltd"


def plant_cover_bidding(r):
    for i, d in enumerate([date(2022, 11, 24), date(2023, 11, 23), date(2024, 11, 28)]):
        add_tender(r, f"RFT 2{i}/T", d, "Fitzroy Fencing Pty Ltd", 40000.0, ["Alpha Fencing Pty Ltd", "Beta Fencing Co"])
    return "Fitzroy Fencing Pty Ltd"


def plant_post_award_overrun(r):
    add_tender(r, "RFT 99/2024", date(2024, 6, 27), "Avon Valley Plant Hire Pty Ltd", 20000.0)
    return "Avon Valley Plant Hire Pty Ltd"


def plant_repeat_winner(r):
    # Three wins of seven tenders in 2024-25 (43 per cent).
    for i, d in enumerate([date(2024, 10, 24), date(2025, 1, 23), date(2025, 4, 24)]):
        add_tender(r, f"RFT 7{i}/2024", d, "Sunrise Bus Services", 30000.0, ["Other Coaches Pty Ltd"])
    return "Sunrise Bus Services"


def test_repeat_winner_needs_three_wins_as_well_as_the_share(tmp_path):
    records = clean_records()
    # 2023-24 has two clean tenders; add three more, two won by Sunrise: 2 of 5 is 40 per cent.
    add_tender(records, "RFT 80/2023", date(2023, 9, 28), "Sunrise Bus Services", 30000.0, ["Other Coaches Pty Ltd"])
    add_tender(records, "RFT 81/2023", date(2024, 2, 22), "Sunrise Bus Services", 30000.0, ["Other Coaches Pty Ltd"])
    add_tender(records, "RFT 82/2023", date(2024, 4, 26), "Other Coaches Pty Ltd", 30000.0, ["Sunrise Bus Services"])
    run = build(records, tmp_path / "two")
    assert run.flagged("repeat_winner") == set()

    add_tender(records, "RFT 83/2023", date(2024, 6, 20), "Sunrise Bus Services", 30000.0, ["Other Coaches Pty Ltd"])
    run = build(records, tmp_path / "three")  # 3 of 6, 50 per cent
    assert run.flagged("repeat_winner") == {run.entity("Sunrise Bus Services")}


def plant_related_party_not_declared(r):
    r["related_parties"].append({"fy": 2025, "counterparty_raw": "Golden Grain Catering", "amount": 4200.0,
                                 "nature": "Catering by a business of a family member of key management personnel"})
    return "Golden Grain Catering"


def plant_campaign_funder_wins(r):
    r["candidate_gifts"].append({"election_date": date(2023, 10, 21), "candidate": "Ravi Menon",
                                 "giver_raw": "Brightline Solar Pty Ltd", "value": 800.0})
    return "Brightline Solar Pty Ltd"


def plant_oag_control_weakness(r):
    r["audit_findings"].append({"report_year": 2024, "report_title": "Fictional Auditor General finding (fixture, 2024)",
                                "area": "Payroll", "finding": "Payroll changes not independently reviewed"})
    return None


PLANTS = {name[len("plant_"):]: fn for name, fn in globals().items() if name.startswith("plant_")}


def test_every_flag_has_a_planting():
    assert set(PLANTS) == set(COUNCIL_FLAGS)


@pytest.mark.parametrize("flag", COUNCIL_FLAGS)
def test_flag_fires_on_planted_pattern(tmp_path, flag):
    records = clean_records()
    target = PLANTS[flag](records)
    run = build(records, tmp_path)
    hits = [h for h in run.hits if h["flag"] == flag]
    if target is None:
        assert [h["body_id"] for h in hits] == [CID]
        assert hits[0]["entity_id"] is None
    else:
        assert run.entity(target) in run.flagged(flag), f"{flag} did not fire on {target}"
    for h in hits:
        assert h["evidence_json"] and h["evidence_json"] != "[]", "a flag without evidence"


def test_walga_and_state_contract_spend_is_exempt(clean_run):
    for name in ("Harbourline Fuel Pty Ltd", "Southern Cross Machinery Pty Ltd", "Statewide Fleet Solutions Pty Ltd"):
        total = clean_run.con.execute("SELECT sum(amount) FROM payments WHERE entity_id = ?", [clean_run.entity(name)]).fetchone()[0]
        assert total > 250000, f"{name} should be a near miss over the threshold"
    assert clean_run.flagged("over_threshold_no_tender") == set()


def test_rent_insurance_subscription_and_licence_repeats_are_exempt(clean_run):
    repeats = clean_run.con.execute("""
        SELECT payee_raw FROM payments GROUP BY payee_raw, round(amount / 100) HAVING count(*) >= 4
    """).fetchall()
    assert {r[0] for r in repeats} >= {"Pemberton Property Holdings Pty Ltd", "Mutual Shire Insurance Scheme",
                                        "Ledgerline Software Pty Ltd", "Regional Library Cooperative"}
    assert clean_run.flagged("repeat_round_amount") == set()


def test_disclosed_interest_does_not_fire(clean_run):
    # Cr Brennan declared Brennan Grain and disclosed at item 11.2, which names it.
    assert clean_run.con.execute("SELECT count(*) FROM items WHERE item_ref = '11.2'").fetchone()[0] == 1
    assert clean_run.flagged("undeclared_at_meeting") == set()


def test_variation_item_suppresses_overrun(tmp_path):
    records = clean_records()
    plant_post_award_overrun(records)
    records["items"].append({"meeting_date": date(2024, 12, 12), "item_ref": "10.5",
                             "item_title": "RFT 99/2024 plant hire - contract variation",
                             "entities_mentioned": ["Avon Valley Plant Hire Pty Ltd"]})
    run = build(records, tmp_path)
    assert run.flagged("post_award_overrun") == set()


def test_single_weak_flag_does_not_surface(tmp_path):
    records = clean_records()
    plant_repeat_round_amount(records)
    run = build(records, tmp_path)
    assert run.flagged("repeat_round_amount")
    assert run.cases == []


def test_single_strong_flag_surfaces(tmp_path):
    records = clean_records()
    plant_gift_from_payee(records)
    run = build(records, tmp_path)
    assert [c["entity_id"] for c in run.cases] == [run.entity("Bluegum Cleaning Services")]


def test_context_flag_multiplies_scores(tmp_path):
    records = clean_records()
    plant_gift_from_payee(records)
    plant_oag_control_weakness(records)
    run = build(records, tmp_path)
    assert run.cases[0]["score"] == 4 * 1.25
