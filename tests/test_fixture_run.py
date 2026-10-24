"""The full fixture end to end: the PDF parsed, the six planted cases at the top, nothing else."""

from collections import Counter

import make_fixture
from conftest import build
from screener import cli, report, score, settings

CAL = settings.calibration()["fixture"]


def test_pdf_parser_reads_every_payment(planted_run):
    expected = make_fixture.build_records(planted=True)["payments"]
    got = planted_run.con.execute("SELECT ref, paid_date, payee_raw, description, amount, page FROM payments ORDER BY row_id").fetchall()
    assert len(got) == len(expected)
    assert Counter((r[0], r[1], r[2], r[3], r[4]) for r in got) == Counter(
        (p["ref"], p["paid_date"], p["payee_raw"], p["description"], p["amount"]) for p in expected)
    assert all(r[5] and r[5] >= 1 for r in got), "every payment cites its PDF page"


def test_rows_carry_provenance(planted_run):
    for table in ("payments", "tenders", "gifts", "interests", "items", "related_parties"):
        missing = planted_run.con.execute(f"""
            SELECT count(*) FROM {table}
            WHERE source_url IS NULL OR source_file IS NULL OR fetched_at IS NULL OR parser IS NULL OR parser_version IS NULL
               OR (page IS NULL AND source_line IS NULL)
        """).fetchone()[0]
        assert missing == 0, f"{table} has rows without provenance"


def test_six_planted_cases_rank_at_the_top(planted_run):
    planted = [planted_run.entity(c["entity"]) for c in CAL["cases"]]
    top = [c["entity_id"] for c in planted_run.cases[:6]]
    assert sorted(top) == sorted(planted)


def test_planted_cases_carry_their_expected_flags(planted_run):
    by_entity = {c["entity_id"]: set(c["flags"]) for c in planted_run.cases}
    for case in CAL["cases"]:
        assert set(case["expect_flags"]) <= by_entity[planted_run.entity(case["entity"])], case["id"]


def test_no_clean_supplier_flagged(planted_run):
    planted = {planted_run.entity(c["entity"]) for c in CAL["cases"]}
    stray = [(h["flag"], h["entity_id"], h["detail"]) for h in planted_run.hits if h["entity_id"] and h["entity_id"] not in planted]
    assert stray == []
    assert len(planted_run.cases) == 6
    for name, *_ in make_fixture.CLEAN_SUPPLIERS:
        assert planted_run.entity(name) not in {h["entity_id"] for h in planted_run.hits}


def test_strong_flags_rest_on_exact_or_abn_matches(planted_run):
    strong = [h for h in planted_run.hits if h["strength"] == "strong"]
    assert strong and all(h["match_basis"] in ("exact", "abn") for h in strong)


def test_calibration_passes(planted_run):
    cal = score.check_calibration(planted_run.con, settings.calibration(), fixture_mode=True)
    assert cal["ok"], cal
    assert all(r["status"] == "found" for r in cal["results"] if "expect_flags" in r)


def test_report_lists_the_six_cases(planted_run, tmp_path):
    cal = score.check_calibration(planted_run.con, settings.calibration(), fixture_mode=True)
    out = report.write(planted_run.con, tmp_path / "index.html", calibration=cal, decisions={}, git_commit="test",
                       root=settings.ROOT)
    html = out.read_text(encoding="utf-8")
    assert html.count('<details class="case"') == 6
    positions = [html.index(f"<strong>{c['entity'].replace('&', '&amp;')}</strong>") for c in CAL["cases"]]
    assert all(p > 0 for p in positions)
    queue = html.split("<h2>Case queue</h2>")[1].split("<h2>Reconciliation</h2>")[0]
    for name, *_ in make_fixture.CLEAN_SUPPLIERS:
        assert f"<strong>{name.replace('&', '&amp;')}</strong>" not in queue
    for section in ("Reconciliation", "FOI request draft", "Export decisions.json", "Commit test"):
        assert section in html
    known = len(settings.calibration()["known_cases"])
    assert f"<h2>Calibration: 6 of {6 + known} checked</h2>" in html
    context = html.split('<section class="context">')[1].split("</section>")[0]
    assert '<span class="chip context">fixture</span>' in context
    assert "Fictional Auditor General finding" in context


def test_noise_decision_keeps_case_dismissed(tmp_path):
    records = make_fixture.build_records(planted=True)
    first = build(records, tmp_path / "a")
    key = first.cases[-1]["case_key"]
    second = build(records, tmp_path / "b", decisions={"cases": {key: {"status": "noise"}}})
    assert {c["case_key"]: c["status"] for c in second.cases}[key] == "noise"


def test_cli_run_fixture(tmp_path):
    raw = tmp_path / "raw" / "fixture" / "2025-07-15"
    make_fixture.write(make_fixture.build_records(planted=True), raw)
    code = cli.main(["run", "--fixture", "--data", str(tmp_path), "--raw", str(tmp_path / "raw" / "fixture")])
    assert code == 0
    assert (tmp_path / "report" / "index.html").exists()
    assert (tmp_path / "screener.duckdb").exists()
