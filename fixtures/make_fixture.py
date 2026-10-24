"""Build the synthetic council used to test phase 1 (SPEC.md section 10).

"Shire of Example", population 4,000, twelve months of records for 2024-25, with six
planted cases and forty clean suppliers that must not fire. Every name, ABN and person
here is invented.

    python fixtures/make_fixture.py              writes data/raw/fixture/<today>/
    python fixtures/make_fixture.py --clean      the same council with no planted cases
    python fixtures/make_fixture.py --out DIR

Registers are written as CSV. The accounts-paid list is written as one PDF in a typical
council layout (a table per month, header repeated on each page) so the parser is
tested end to end.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import math
import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

COUNCIL = {
    "council_id": "shire-of-example",
    "name": "Shire of Example",
    "region": "Wheatbelt",
    "website": "fixture://shire-of-example",
    "minutes_url": "fixture://shire-of-example/minutes",
    "population": 4000,
    "notes": "Synthetic council for tests. Not a real local government.",
}
CID = COUNCIL["council_id"]
FY_START = date(2024, 7, 1)
MONTHS = [(2024, m) for m in range(7, 13)] + [(2025, m) for m in range(1, 7)]
SEED = 20250630

# Approval limits in the delegations register. Clean amounts avoid 90-100% of each.
DELEGATIONS = [
    ("Chief Executive Officer", 50000.0),
    ("Executive Manager Corporate Services", 20000.0),
    ("Manager Works", 10000.0),
]
TENDER_THRESHOLD = 250000.0
_FORBIDDEN = [(0.9 * lim, lim) for _, lim in DELEGATIONS] + [(0.9 * TENDER_THRESHOLD, TENDER_THRESHOLD)]

# Forty ordinary suppliers: (name, ABN status-from year, pattern, description).
# pattern: ("monthly", lo, hi) | ("monthly_fixed", amount) | ("quarterly", lo, hi)
#          ("quarterly_fixed", amount) | ("n", count, lo, hi) | ("list", [amounts], [months])
CLEAN_SUPPLIERS = [
    ("Harbourline Fuel Pty Ltd", 2009, ("monthly", 21000, 27500), "Fuel and oils - WALGA Preferred Supplier"),
    ("Southern Cross Machinery Pty Ltd", 2004, ("list", [385000.00], [(2024, 10)]), "Motor grader purchase - WALGA Preferred Supplier"),
    ("Statewide Fleet Solutions Pty Ltd", 2012, ("list", [62450.00, 71980.00, 58320.00, 77250.00], [(2024, 8), (2024, 11), (2025, 2), (2025, 5)]), "Vehicle purchase - State Contract CUA"),
    ("Pemberton Property Holdings Pty Ltd", 2007, ("monthly_fixed", 2400.00), "Rent - Community Resource Centre lease"),
    ("Mutual Shire Insurance Scheme", 1998, ("quarterly_fixed", 38200.00), "Insurance premium instalment"),
    ("State Treasury Lending Authority", 1999, ("list", [24315.60, 24315.60], [(2024, 9), (2025, 3)]), "Loan 112 repayment"),
    ("Ledgerline Software Pty Ltd", 2013, ("monthly_fixed", 1850.00), "Software subscription - records system"),
    ("Regional Library Cooperative", 2001, ("quarterly_fixed", 1240.00), "Library licence fee"),
    ("Southern Energy Retail Pty Ltd", 2006, ("monthly", 3800, 8200), "Electricity - shire facilities"),
    ("Coastal Water Services", 2003, ("quarterly", 2200, 6400), "Water usage - parks and facilities"),
    ("Regional Telecom Pty Ltd", 2010, ("monthly", 640, 1150), "Telephone and internet"),
    ("Westcoast Super Fund", 2002, ("monthly", 14000, 17200), "Superannuation contributions"),
    ("Example Rural Supplies", 2005, ("n", 20, 120, 3400), "Hardware and materials"),
    ("Wheatbelt Plumbing & Gas", 2011, ("n", 6, 380, 6200), "Plumbing repairs"),
    ("Avon Electrical Contractors", 2008, ("n", 8, 290, 7400), "Electrical repairs"),
    ("Dunn's Mechanical Repairs", 2014, ("n", 10, 450, 8300), "Plant repairs and servicing"),
    ("Northam Office Supplies", 2009, ("n", 10, 85, 1450), "Stationery and office supplies"),
    ("Golden Grain Catering", 2016, ("n", 4, 380, 2600), "Catering - council meetings"),
    ("Wheatbelt Waste Services Pty Ltd", 2010, ("monthly", 15600, 17400), "Waste collection - RFT 01/2024"),
    ("Roadline Contracting Pty Ltd", 2006, ("list", [118400.00, 164250.00, 152900.00, 124450.00], [(2024, 11), (2025, 1), (2025, 3), (2025, 5)]), "Road reseal program RFT 03/2024 - progress claim"),
    ("Kestrel Building Services Pty Ltd", 2012, ("list", [72400.00, 64850.00, 42600.00], [(2024, 7), (2024, 9), (2024, 12)]), "Medical centre refurbishment RFT 09/2023 - progress claim"),
    ("Brightwater Pool Management", 2015, ("monthly", 11500, 13600), "Pool management - RFT 11/2023"),
    ("Peak Engineering Consultants Pty Ltd", 2011, ("list", [12800.00, 21400.00, 16350.00], [(2024, 12), (2025, 2), (2025, 4)]), "Townsite drainage design RFT 05/2024"),
    ("Swan Valley Traffic Management", 2013, ("n", 6, 1200, 7800), "Traffic management"),
    ("Bluegum Cleaning Services", 2012, ("monthly", 2850, 3900), "Cleaning - shire offices and hall"),
    ("Example Veterinary Clinic", 2004, ("n", 3, 180, 950), "Ranger services - impounded animals"),
    ("Northern Signs & Lines", 2010, ("n", 3, 640, 4800), "Signage"),
    ("Fitzroy Fencing Pty Ltd", 2017, ("n", 3, 2100, 8600), "Fencing - sportsground"),
    ("Albany Tyre Centre", 2003, ("n", 5, 420, 5400), "Tyres - plant"),
    ("Pinnacle Audit Partners", 2008, ("list", [24600.00, 12900.00], [(2024, 10), (2025, 4)]), "Audit fees - interim and final"),
    ("Greenfield Weed Control", 2014, ("n", 5, 900, 6800), "Weed spraying - road reserves"),
    ("Southern Pest Management", 2016, ("n", 4, 260, 1800), "Pest control"),
    ("Avon Valley Plant Hire Pty Ltd", 2009, ("n", 7, 2600, 14800), "Plant hire"),
    ("Example Community Resource Centre Inc", 2000, ("list", [7500.00, 7500.00], [(2024, 8), (2025, 2)]), "Community grant - annual operating"),
    ("Coastal Freight Lines", 2011, ("n", 4, 140, 980), "Freight"),
    ("Maddock & Reid Lawyers", 1995, ("n", 3, 1800, 8900), "Legal advice"),
    ("Example Medical Practice", 2006, ("monthly", 4200, 5600), "GP services agreement - practice support"),
    ("Sunrise Bus Services", 2013, ("n", 10, 1100, 3900), "Community bus hire"),
    ("Skyline Printing", 2015, ("n", 4, 350, 2200), "Printing - community newsletter"),
    ("Brightline Solar Pty Ltd", 2018, ("list", [52800.00, 35200.00], [(2025, 3), (2025, 5)]), "Pavilion solar installation RFT 06/2024"),
]
assert len(CLEAN_SUPPLIERS) == 40

PLANTED_NAMES = {
    1: "Pearce Property Maintenance",
    2: "Dunmore Civil & Drainage",
    3: "Greenway Landscapes Pty Ltd",
    4: "Cardell Strategic Consulting Pty Ltd",
    5: "Westridge Construction Pty Ltd",
    6: "Tarrant Holdings Pty Ltd",
}

# Tenders: (ref, description, closed, awarded, winner, contract amount, losers, planted case or None)
TENDERS = [
    ("RFT 02/2022", "Parks and gardens maintenance 2022-23", date(2022, 7, 28), date(2022, 8, 25), "Greenway Landscapes Pty Ltd", 84000.0, ["Verdant Grounds Pty Ltd", "Parkline Gardens"], 3),
    ("RFT 08/2022", "Waste transfer station management", date(2022, 8, 26), date(2022, 9, 22), "Wheatbelt Waste Services Pty Ltd", 180000.0, ["Prospect Waste Pty Ltd"], None),
    ("RFT 09/2022", "Townsite footpath program", date(2022, 9, 30), date(2022, 10, 27), "Roadline Contracting Pty Ltd", 140000.0, ["Goldfields Asphalt Pty Ltd"], None),
    ("RFT 02/2023", "Parks and gardens maintenance 2023-24", date(2023, 7, 27), date(2023, 8, 24), "Greenway Landscapes Pty Ltd", 88000.0, ["Verdant Grounds Pty Ltd", "Parkline Gardens"], 3),
    ("RFT 04/2023", "Depot ablution block", date(2023, 9, 28), date(2023, 10, 26), "Westridge Construction Pty Ltd", 38000.0, ["Hallmark Builders Pty Ltd", "Coreline Constructions"], 5),
    ("RFT 06/2023", "Town hall roof repairs", date(2023, 10, 27), date(2023, 11, 23), "Westridge Construction Pty Ltd", 46000.0, ["Coreline Constructions", "Summit Roofing Pty Ltd"], 5),
    ("RFT 07/2023", "Recreation centre upgrade", date(2023, 12, 15), date(2024, 1, 25), "Westridge Construction Pty Ltd", 220000.0, ["Kestrel Building Services Pty Ltd", "Hallmark Builders Pty Ltd"], 5),
    ("RFT 09/2023", "Medical centre refurbishment", date(2024, 2, 29), date(2024, 3, 28), "Kestrel Building Services Pty Ltd", 310000.0, ["Hallmark Builders Pty Ltd", "Coreline Constructions"], None),
    ("RFT 11/2023", "Swimming pool management 2024-2027", date(2024, 4, 26), date(2024, 5, 23), "Brightwater Pool Management", 450000.0, ["Aquatic Leisure Management Pty Ltd"], None),
    ("RFT 01/2024", "Waste collection services 2024-2027", date(2024, 6, 28), date(2024, 7, 25), "Wheatbelt Waste Services Pty Ltd", 210000.0, ["Prospect Waste Pty Ltd", "Eastern Waste Logistics"], None),
    ("RFT 02/2024", "Parks and gardens maintenance 2024-25", date(2024, 7, 26), date(2024, 8, 22), "Greenway Landscapes Pty Ltd", 92000.0, ["Verdant Grounds Pty Ltd", "Parkline Gardens"], 3),
    ("RFT 03/2024", "Road reseal program 2024-25", date(2024, 8, 30), date(2024, 9, 26), "Roadline Contracting Pty Ltd", 620000.0, ["Goldfields Asphalt Pty Ltd", "Bitumen West Pty Ltd"], None),
    ("RFT 05/2024", "Townsite drainage design", date(2024, 10, 31), date(2024, 11, 28), "Peak Engineering Consultants Pty Ltd", 65000.0, ["Meridian Civil Consultants", "Hydra Engineering Pty Ltd"], None),
    ("RFT 06/2024", "Pavilion solar installation", date(2025, 1, 31), date(2025, 2, 27), "Brightline Solar Pty Ltd", 88000.0, ["Sunvolt Energy Pty Ltd"], None),
]

COUNCILLORS = [
    ("Dianne Archer", "Shire President"),
    ("Paul Brennan", "Councillor"),
    ("Lisa Corrigan", "Councillor"),
    ("Tom Hollis", "Councillor"),
    ("Ravi Menon", "Councillor"),
    ("Sarah Quinlan", "Councillor"),
    ("Greg Whitby", "Councillor"),
]
OFFICERS = [
    ("Andrew Kerr", "Chief Executive Officer"),
    ("Neil Prowse", "Manager Works"),
    ("Jo Tate", "Executive Manager Corporate Services"),
]

# Declared interests: (person, interest type, entity or None, detail)
CLEAN_INTERESTS = [
    ("Dianne Archer", "business", "Archer Pastoral Co", "Farming partnership"),
    ("Dianne Archer", "trust", "Archer Family Trust", "Beneficiary"),
    ("Dianne Archer", "land", None, "Lot 4 Wongan Road, Example"),
    ("Paul Brennan", "directorship", "Brennan Grain Pty Ltd", "Director and shareholder"),
    ("Lisa Corrigan", "employer", "Department of Education", "Teacher, Example District High School"),
    ("Tom Hollis", "business", "Hollis & Co Bookkeeping", "Sole trader"),
    ("Ravi Menon", "employer", "Wheatbelt Health Service", "Registered nurse"),
    ("Sarah Quinlan", "business", "Quinlan Florist", "Proprietor"),
    ("Greg Whitby", "directorship", "Whitby Transport Pty Ltd", "Director"),
    ("Andrew Kerr", "land", None, "Residence, Example townsite"),
    ("Neil Prowse", "shareholding", "Prowse Earthmoving Pty Ltd", "Minority shareholder (family company)"),
    ("Jo Tate", "land", None, "Residence, Example townsite"),
]


def _round100(x: float) -> int:
    return math.floor(x / 100 + 0.5)


def _draw(rng: random.Random, lo: float, hi: float) -> float:
    while True:
        x = round(rng.uniform(lo, hi), 2)
        if not any(a <= x <= b for a, b in _FORBIDDEN):
            return x


def _varied(rng: random.Random, lo: float, hi: float, n: int) -> list[float]:
    """n amounts in [lo, hi] that never repeat to the nearest $100 four times."""
    while True:
        xs = [_draw(rng, lo, hi) for _ in range(n)]
        if max(Counter(_round100(x) for x in xs).values()) < 4:
            return xs


def _day(rng: random.Random, year: int, month: int, lo: int = 3, hi: int = 27) -> date:
    return date(year, month, rng.randint(lo, min(hi, calendar.monthrange(year, month)[1])))


def _fourth_thursday(year: int, month: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(3 - d.weekday()) % 7)
    return d + timedelta(weeks=3)


def _fake_abn(rng: random.Random) -> str:
    return str(rng.randint(10, 99)) + "".join(str(rng.randint(0, 9)) for _ in range(9))


def build_records(planted: bool = True, seed: int = SEED) -> dict[str, list[dict]]:
    """Every record of the fixture council, as lists of dicts keyed by table."""
    rng = random.Random(seed)
    payments: list[dict] = []

    def pay(d: date, payee: str, desc: str, amount: float):
        payments.append({"paid_date": d, "payee_raw": payee, "description": desc, "amount": round(amount, 2)})

    abn_rows: list[dict] = []

    def register(name: str, status_from: date, entity_type: str = "PRV", extra_names=()):
        abn = _fake_abn(rng)
        for n in (name, *extra_names):
            abn_rows.append({"abn": abn, "name_raw": n, "entity_type": entity_type, "status": "ACT",
                             "status_from": status_from, "state": "WA", "postcode": "6" + str(rng.randint(100, 999))})
        return abn

    # Clean suppliers.
    for name, since, pattern, desc in CLEAN_SUPPLIERS:
        register(name, date(since, rng.randint(1, 12), rng.randint(1, 28)))
        kind = pattern[0]
        if kind == "monthly":
            for (y, m), amt in zip(MONTHS, _varied(rng, pattern[1], pattern[2], 12)):
                pay(_day(rng, y, m), name, desc, amt)
        elif kind == "monthly_fixed":
            for y, m in MONTHS:
                pay(_day(rng, y, m, 1, 5), name, desc, pattern[1])
        elif kind == "quarterly":
            for (y, m), amt in zip(MONTHS[::3], _varied(rng, pattern[1], pattern[2], 4)):
                pay(_day(rng, y, m), name, desc, amt)
        elif kind == "quarterly_fixed":
            for y, m in MONTHS[::3]:
                pay(_day(rng, y, m, 8, 14), name, desc, pattern[1])
        elif kind == "n":
            count, lo, hi = pattern[1:]
            for amt in _varied(rng, lo, hi, count):
                y, m = rng.choice(MONTHS)
                pay(_day(rng, y, m), name, desc, amt)
        elif kind == "list":
            for amt, (y, m) in zip(pattern[1], pattern[2]):
                pay(_day(rng, y, m, 5, 20), name, desc, amt)

    # Tender losers that are on the ABN register, and decoys with similar words.
    for name in ["Verdant Grounds Pty Ltd", "Parkline Gardens", "Prospect Waste Pty Ltd", "Goldfields Asphalt Pty Ltd",
                 "Hallmark Builders Pty Ltd", "Coreline Constructions", "Aquatic Leisure Management Pty Ltd",
                 "Eastern Waste Logistics", "Bitumen West Pty Ltd", "Meridian Civil Consultants",
                 "Hydra Engineering Pty Ltd", "Sunvolt Energy Pty Ltd", "Dunmore Pastoral Co",
                 "Pearce Family Investments Pty Ltd", "Cardinal Strategy Group Pty Ltd"]:
        register(name, date(rng.randint(1998, 2019), rng.randint(1, 12), rng.randint(1, 28)))

    if planted:
        # 1. Sole trader, ABN two months before the first payment, $9,800 a month, no tender.
        register(PLANTED_NAMES[1], date(2024, 5, 3), entity_type="IND", extra_names=["PEARCE, DAVID JOHN"])
        for y, m in MONTHS:
            pay(date(y, m, 5) if date(y, m, 5).weekday() < 5 else date(y, m, 7), PLANTED_NAMES[1], "Maintenance services", 9800.00)
        # 2. Civil contractor, $410,000 across nine invoices, no tender, no ABN match.
        for (y, m), amt in zip(MONTHS[1:10], [45100, 45900, 45300, 46200, 45450, 45800, 45050, 45650, 45550]):
            pay(_day(rng, y, m, 8, 22), PLANTED_NAMES[2], "Drainage and civil works", amt)
        # 3. Landscaper: gift to a councillor, wins against the same two losers a third time.
        register(PLANTED_NAMES[3], date(2012, 3, 14))
        for (y, m), amt in zip(MONTHS, _varied(rng, 6800, 8400, 12)):
            ref = "RFT 02/2023" if (y, m) < (2024, 9) else "RFT 02/2024"
            pay(_day(rng, y, m), PLANTED_NAMES[3], f"Parks and gardens maintenance {ref}", amt)
        # 4. Consultancy a councillor directs, $45,000, engaged at a meeting with no disclosure.
        register(PLANTED_NAMES[4], date(2016, 6, 2))
        pay(date(2024, 9, 19), PLANTED_NAMES[4], "Strategic community plan review - consultancy", 27500.00)
        pay(date(2024, 12, 12), PLANTED_NAMES[4], "Strategic community plan review - consultancy", 17500.00)
        # 5. Tender at $220,000, $340,000 paid in the eighteen months after award.
        register(PLANTED_NAMES[5], date(2009, 11, 20))
        for i, ((y, m), amt) in enumerate(zip([(2024, 7), (2024, 9), (2024, 11), (2025, 1), (2025, 4), (2025, 6)],
                                              [52400, 61300, 38900, 57600, 71200, 58600]), start=1):
            pay(_day(rng, y, m, 6, 20), PLANTED_NAMES[5], f"Recreation centre upgrade RFT 07/2023 - progress claim {i}", amt)
        # 6. Company in the related party note and in no interest return.
        register(PLANTED_NAMES[6], date(2011, 2, 8))
        for y, m in MONTHS:
            pay(_day(rng, y, m, 10, 16), PLANTED_NAMES[6], "Advisory services", 3500.00)

    payments.sort(key=lambda p: (p["paid_date"], p["payee_raw"]))
    eft = 21450
    for p in payments:
        if p["payee_raw"] in ("State Treasury Lending Authority", "Regional Library Cooperative"):
            p["ref"] = f"{rng.randint(4500, 4599):06d}"
        else:
            eft += 1
            p["ref"] = f"EFT{eft}"

    tenders = []
    for ref, desc, closed, awarded, winner, amount, losers, case in TENDERS:
        if case and not planted:
            continue
        tenders.append({"tender_ref": ref, "description": desc, "closed_date": closed, "awarded_date": awarded,
                        "tenderer_raw": winner, "is_winner": True, "contract_amount": amount})
        for loser in losers:
            tenders.append({"tender_ref": ref, "description": desc, "closed_date": closed, "awarded_date": awarded,
                            "tenderer_raw": loser, "is_winner": False, "contract_amount": None})

    gifts = [
        {"recipient_name": "Dianne Archer", "recipient_role": "Shire President", "giver_raw": "Wheatbelt Grain Growers Association",
         "value": 320.0, "received_date": date(2024, 9, 12), "description": "Annual dinner ticket"},
        {"recipient_name": "Andrew Kerr", "recipient_role": "Chief Executive Officer", "giver_raw": "Regional Development Forum Inc",
         "value": 650.0, "received_date": date(2025, 3, 5), "description": "Conference registration"},
        {"recipient_name": "Ravi Menon", "recipient_role": "Councillor", "giver_raw": "Lakeside Winery",
         "value": 310.0, "received_date": date(2025, 5, 15), "description": "Hospitality at regional tourism launch"},
    ]
    if planted:
        gifts.append({"recipient_name": "Greg Whitby", "recipient_role": "Councillor", "giver_raw": PLANTED_NAMES[3],
                      "value": 450.0, "received_date": date(2024, 10, 3), "description": "Corporate hospitality - football final"})

    roles = dict(COUNCILLORS + OFFICERS)
    interests = []
    for person, itype, entity, detail in CLEAN_INTERESTS:
        returns = [("annual", date(2023, 8, 31)), ("annual", date(2024, 8, 30))]
        if person == "Tom Hollis":
            returns = [("primary", date(2023, 11, 10)), ("annual", date(2024, 8, 30))]
        for rtype, rdate in returns:
            interests.append({"person": person, "role": roles[person], "interest_type": itype, "entity_raw": entity,
                              "detail": detail, "return_type": rtype, "return_date": rdate})
    if planted:
        for rtype, rdate in [("primary", date(2023, 11, 10)), ("annual", date(2024, 8, 30))]:
            interests.append({"person": "Tom Hollis", "role": "Councillor", "interest_type": "directorship",
                              "entity_raw": PLANTED_NAMES[4], "detail": "Director", "return_type": rtype, "return_date": rdate})

    def tenderers(ref):
        return [t["tenderer_raw"] for t in tenders if t["tender_ref"] == ref]

    items = []
    for y, m in MONTHS:
        meeting = _fourth_thursday(y, m)
        items.append({"meeting_date": meeting, "item_ref": "10.1.1", "item_title": "Monthly financial statements", "entities_mentioned": []})
        items.append({"meeting_date": meeting, "item_ref": "10.1.2", "item_title": "List of accounts paid", "entities_mentioned": []})
    awards = [("RFT 01/2024", "10.2.1", "Waste collection services - award of tender"),
              ("RFT 02/2024", "10.2.1", "Parks and gardens maintenance - award of tender"),
              ("RFT 03/2024", "10.2.1", "Road reseal program - award of tender"),
              ("RFT 05/2024", "10.2.1", "Townsite drainage design - award of tender"),
              ("RFT 06/2024", "10.2.1", "Pavilion solar installation - award of tender")]
    for ref, item_ref, title in awards:
        rows = [t for t in tenders if t["tender_ref"] == ref]
        if rows:
            items.append({"meeting_date": rows[0]["awarded_date"], "item_ref": item_ref, "item_title": f"{ref} {title}",
                          "entities_mentioned": tenderers(ref)})
    items.append({"meeting_date": date(2025, 4, 24), "item_ref": "10.2.2", "item_title": "RFT 03/2024 Road reseal program - contract variation",
                  "entities_mentioned": ["Roadline Contracting Pty Ltd"]})
    items.append({"meeting_date": date(2025, 3, 27), "item_ref": "11.2", "item_title": "Development application - machinery shed, Lot 12 Great Eastern Highway",
                  "entities_mentioned": ["Brennan Grain Pty Ltd"]})
    items.append({"meeting_date": date(2024, 10, 24), "item_ref": "9.1", "item_title": "Community grants round 2024-25", "entities_mentioned": []})
    if planted:
        items.append({"meeting_date": date(2024, 8, 22), "item_ref": "10.3", "item_title": "Strategic community plan review - engagement of consultant",
                      "entities_mentioned": [PLANTED_NAMES[4]]})
    items.sort(key=lambda i: (i["meeting_date"], i["item_ref"]))

    disclosures = [
        {"meeting_date": date(2025, 3, 27), "item_ref": "11.2", "item_title": "Development application - machinery shed, Lot 12 Great Eastern Highway",
         "person": "Cr Brennan", "interest_type": "financial"},
        {"meeting_date": date(2024, 10, 24), "item_ref": "9.1", "item_title": "Community grants round 2024-25",
         "person": "Cr Quinlan", "interest_type": "impartiality"},
    ]
    if planted:
        # Another councillor discloses at the meeting that engaged the consultancy; Cr Hollis does not.
        disclosures.append({"meeting_date": date(2024, 8, 22), "item_ref": "10.2.1", "item_title": "RFT 02/2024 Parks and gardens maintenance - award of tender",
                            "person": "Cr Menon", "interest_type": "impartiality"})

    related = [{"fy": 2025, "counterparty_raw": None, "amount": 142000.0, "nature": "Elected members - meeting fees and allowances"}]
    if planted:
        related += [
            {"fy": 2025, "counterparty_raw": PLANTED_NAMES[4], "amount": 45000.0, "nature": "Consultancy services from an entity controlled by a councillor"},
            {"fy": 2025, "counterparty_raw": PLANTED_NAMES[6], "amount": 42000.0, "nature": "Advisory services from an entity related to key management personnel"},
        ]

    candidate_gifts = [
        {"election_date": date(2023, 10, 21), "candidate": "Tom Hollis", "giver_raw": "Avon Transport Group", "value": 500.0},
        {"election_date": date(2023, 10, 21), "candidate": "Sarah Quinlan", "giver_raw": "Lakeview Farms", "value": 1000.0},
    ]

    # Invented findings: not taken from any real Auditor General report.
    audit = [
        {"report_year": 2023, "report_title": "Fictional Auditor General finding (fixture, 2023)", "area": "Financial reporting",
         "finding": "Fixed asset register not reconciled to the general ledger"},
        {"report_year": 2024, "report_title": "Fictional Auditor General finding (fixture, 2024)", "area": "Financial reporting",
         "finding": "Asset revaluation not performed in the required cycle"},
    ]
    if planted:
        audit.append({"report_year": 2024, "report_title": "Fictional Auditor General finding (fixture, 2024)", "area": "Purchasing",
                      "finding": "Purchase orders raised after invoices; quotes not retained"})

    delegations = [{"role": role, "limit_amount": amount, "source_date": date(2024, 6, 27)} for role, amount in DELEGATIONS]

    return {
        "councils": [dict(COUNCIL)],
        "payments": payments,
        "tenders": tenders,
        "gifts": gifts,
        "interests": interests,
        "disclosures": disclosures,
        "items": items,
        "delegations": delegations,
        "related_parties": related,
        "candidate_gifts": candidate_gifts,
        "audit_findings": audit,
        "abn": abn_rows,
    }


# File each table is written to; payments go to the PDF.
FILES = {
    "councils": "council.csv",
    "tenders": "tenders_register.csv",
    "gifts": "gifts_register.csv",
    "interests": "financial_interest_returns.csv",
    "disclosures": "disclosures_of_interest.csv",
    "items": "agenda_items.csv",
    "delegations": "delegations_register.csv",
    "related_parties": "related_party_note.csv",
    "candidate_gifts": "candidate_gift_disclosures.csv",
    "audit_findings": "oag_audit_results.csv",
    "abn": "abn_extract_sample.csv",
}
PDF_NAME = "accounts_paid_2024-25.pdf"


def write(records: dict[str, list[dict]], out_dir: Path, pdf: bool = True) -> Path:
    """Write the records as source files. pdf=False writes payments as CSV (fast tests)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = dict(FILES) if pdf else {**FILES, "payments": "accounts_paid.csv"}
    for table, fname in files.items():
        rows = records[table]
        cols = list(rows[0].keys()) if rows else []
        with open(out_dir / fname, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({k: _cell(v) for k, v in r.items()})
    entries = [{"file": fname, "kind": table, "url": f"fixture://{CID}/{fname}"} for table, fname in files.items()]
    if pdf:
        write_accounts_pdf(records["payments"], out_dir / PDF_NAME)
        entries.insert(0, {"file": PDF_NAME, "kind": "accounts_paid", "url": f"fixture://{CID}/{PDF_NAME}"})
    manifest = {
        "source": "fixture",
        "council_id": CID,
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "files": entries,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out_dir


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return " | ".join(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def write_accounts_pdf(payments: list[dict], path: Path) -> None:
    """One table per month under the council letterhead, header row repeated on every page."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm, title="Shire of Example - List of Accounts Paid 2024-25")
    story = []
    by_month: dict[tuple[int, int], list[dict]] = {}
    for p in payments:
        by_month.setdefault((p["paid_date"].year, p["paid_date"].month), []).append(p)
    for i, (y, m) in enumerate(MONTHS):
        rows = by_month.get((y, m), [])
        meeting = _fourth_thursday(*((y, m + 1) if m < 12 else (y + 1, 1)))
        if i:
            story.append(PageBreak())
        story.append(Paragraph("SHIRE OF EXAMPLE", styles["Title"]))
        story.append(Paragraph(f"LIST OF ACCOUNTS PAID - {calendar.month_name[m].upper()} {y}", styles["Heading2"]))
        story.append(Paragraph(f"Attachment 10.1.2 to the Ordinary Council Meeting of {meeting.day} {calendar.month_name[meeting.month]} {meeting.year}. Municipal Fund.", styles["Normal"]))
        story.append(Spacer(1, 4 * mm))
        data = [["Chq/EFT", "Date", "Name", "Description", "Amount $"]]
        for p in rows:
            data.append([p["ref"], p["paid_date"].strftime("%d/%m/%Y"), p["payee_raw"], p["description"], f"{p['amount']:,.2f}"])
        data.append(["", "", "", f"Total {calendar.month_name[m]} {y}", f"{sum(p['amount'] for p in rows):,.2f}"])
        table = Table(data, colWidths=[24 * mm, 24 * mm, 75 * mm, 112 * mm, 30 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dde3ea")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#8a96a3")),
            ("ALIGN", (4, 0), (4, -1), "RIGHT"),
        ]))
        story.append(table)

    def footer(canvas, doc_):
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(landscape(A4)[0] - 15 * mm, 8 * mm, f"Page {doc_.page}")

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, help="output folder (default data/raw/fixture/<today>)")
    ap.add_argument("--clean", action="store_true", help="omit the planted cases")
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    out = args.out or root / "data" / "raw" / "fixture" / date.today().isoformat()
    records = build_records(planted=not args.clean)
    write(records, out)
    print(f"Fixture written to {out}: {len(records['payments'])} payments, {len(records['tenders'])} tender rows, "
          f"{'no planted cases' if args.clean else 'six planted cases'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
