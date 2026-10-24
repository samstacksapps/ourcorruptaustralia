"""Name cleaning and entity resolution (SPEC.md section 6)."""

from datetime import date

import pytest

from conftest import build, clean_records
from screener.normalise import normalise_name, person_key, resolve


@pytest.mark.parametrize("raw, expected", [
    ("Greenway Landscapes Pty Ltd", "GREENWAY LANDSCAPES"),
    ("GREENWAY LANDSCAPES PTY. LTD.", "GREENWAY LANDSCAPES"),
    ("The Trustee for the Smith Family Trust", "SMITH"),
    ("Smith Holdings Pty Ltd ATF Smith Unit Trust", "SMITH HOLDINGS SMITH"),
    ("J Smith T/A Smith Plumbing", "J SMITH SMITH PLUMBING"),
    ("Dunn's Mechanical Repairs", "DUNNS MECHANICAL REPAIRS"),
    ("Wheatbelt Plumbing & Gas", "WHEATBELT PLUMBING AND GAS"),
    ("  Acme   Proprietary   Limited ", "ACME"),
    (None, ""),
])
def test_normalise_name(raw, expected):
    assert normalise_name(raw) == expected


def test_person_key():
    assert person_key("Cr T Hollis") == "HOLLIS"
    assert person_key("Tom Hollis") == "HOLLIS"
    assert person_key("Shire President Dianne Archer") == "ARCHER"


def _resolve(names, abn_names=(), confirmed=None, same_entity=None):
    return resolve(names, list(abn_names), fuzzy_link_min=92, model_review_min=80, fuzzy_min_tokens=2,
                   confirmed=confirmed, same_entity=same_entity)


def test_abn_match_is_exact_and_joins_names_on_one_abn():
    res = _resolve(["PEARCE PROPERTY MAINTENANCE", "PEARCE DAVID JOHN"],
                   [("111", "PEARCE PROPERTY MAINTENANCE"), ("111", "PEARCE DAVID JOHN")])
    assert res.links["PEARCE PROPERTY MAINTENANCE"].entity_id == res.links["PEARCE DAVID JOHN"].entity_id == "ABN-111"
    assert {l.method for l in res.links.values()} == {"abn"}


def test_fuzzy_link_at_92_or_more():
    res = _resolve(["BLUEGUM CLEANING SERVICES", "BLUEGUM CLEANING SERVICE"], [("222", "BLUEGUM CLEANING SERVICES")])
    link = res.links["BLUEGUM CLEANING SERVICE"]
    assert link.entity_id == "ABN-222" and link.method == "fuzzy" and link.score >= 92


def test_single_token_subset_does_not_fuzzy_link():
    # token_set_ratio scores these 100; the token guard keeps them apart.
    res = _resolve(["EXAMPLE", "SHIRE OF EXAMPLE"])
    assert res.links["EXAMPLE"].entity_id != res.links["SHIRE OF EXAMPLE"].entity_id


def test_review_band_goes_to_model_and_unsure_stays_unmatched():
    res = _resolve(["NORTHAM OFFICE SUPPLIES", "NORTHAM OFFICE SUPPLY CO"])
    a, b = res.links["NORTHAM OFFICE SUPPLIES"], res.links["NORTHAM OFFICE SUPPLY CO"]
    assert res.reviews and res.reviews[0]["answer"] == "unsure"
    assert 80 <= res.reviews[0]["score"] < 92
    assert a.entity_id != b.entity_id


def test_model_same_answer_links_with_method_model():
    res = _resolve(["NORTHAM OFFICE SUPPLIES", "NORTHAM OFFICE SUPPLY CO"],
                   same_entity=lambda a, b: ("same", "test-model", "same-entity-v1"))
    assert res.links["NORTHAM OFFICE SUPPLY CO"].method == "model"
    assert res.links["NORTHAM OFFICE SUPPLY CO"].entity_id == res.links["NORTHAM OFFICE SUPPLIES"].entity_id


def test_fuzzy_match_never_carries_a_strong_flag_alone(tmp_path):
    records = clean_records()
    # The giver's name differs from the payee's by one letter: a fuzzy link, not exact.
    records["gifts"].append({"recipient_name": "Lisa Corrigan", "recipient_role": "Councillor",
                             "giver_raw": "Bluegum Cleaning Service", "value": 400.0,
                             "received_date": date(2024, 12, 1), "description": "Hamper"})
    run = build(records, tmp_path / "fuzzy")
    held = [h for h in run.hits if h["flag"] == "gift_from_payee"]
    assert held and all(h["held"] and h["match_basis"] == "fuzzy" for h in held)
    assert run.cases == []

    confirmed = build(records, tmp_path / "confirmed", decisions={"confirmed_names": ["BLUEGUM CLEANING SERVICE"]})
    hits = [h for h in confirmed.hits if h["flag"] == "gift_from_payee"]
    assert hits and not hits[0]["held"] and hits[0]["match_basis"] == "confirmed"
    assert len(confirmed.cases) == 1
