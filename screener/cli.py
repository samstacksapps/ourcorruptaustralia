"""python -m screener fetch|parse|build|score|report|run [--fixture]

Phase 1 runs on the synthetic fixture only and fetches nothing from the internet:

    python fixtures/make_fixture.py
    python -m screener run --fixture

run = parse + build + score + report, then the calibration check. It exits non-zero if a
planted case is missed or any other entity carries a flag.

Phase 2 parses real council files already downloaded, one folder per council, and checks
the rows against the totals printed on each PDF. Nothing is built or scored yet:

    python -m screener parse --source council_check
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path

from screener import db, parse, privacy, report, score, settings


def _paths(args) -> dict:
    data = Path(args.data) if args.data else settings.DATA
    source = "fixture"
    return {
        "data": data,
        "raw": Path(args.raw) if args.raw else data / "raw" / source,
        "parsed": data / "parsed" / source,
        "db": Path(args.db) if args.db else data / "screener.duckdb",
        "report": Path(args.report) if args.report else data / "report" / "index.html",
        "decisions": Path(args.decisions) if args.decisions else data / "review" / "decisions.json",
    }


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=settings.ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=settings.ROOT,
                               capture_output=True, text=True).stdout.strip()
        return out + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "uncommitted"


def cmd_fetch(args, p) -> int:
    print("Phase 1 fetches nothing: the fixture stands in for downloaded files. Run fixtures/make_fixture.py.")
    return 0


def cmd_parse_councils(args, p) -> int:
    """Parse each council's newest folder under data/raw/<source>/ and print the check
    of parsed rows against every printed total. Exits non-zero if any total is missed."""
    raw_root = Path(args.raw) if args.raw else p["data"] / "raw" / args.source
    private = privacy.PrivateNames(p["data"] / "private" / "payee_codes.csv", settings.CONFIG / "payee_not_private.txt")
    ok = True
    for council_dir in sorted(d for d in raw_root.iterdir() if d.is_dir()):
        try:
            folder = parse.latest_folder(council_dir)
        except FileNotFoundError:
            continue
        out = p["data"] / "parsed" / args.source / council_dir.name
        counts = parse.parse_folder(folder, out, settings.ROOT, private=private)
        print(f"\n{council_dir.name}: " + ", ".join(f"{t} {n}" for t, n in sorted(counts.items())))
        totals = out / "payment_totals.csv"
        if not totals.exists():
            continue
        with open(totals, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for t in rows:
            status = "PASS" if t["ok"] == "True" else "FAIL"
            ok &= status == "PASS"
            print(f"  {status}  {t['label']:<28} rows {t['rows']:>4}  parsed {float(t['parsed']):>14,.2f}"
                  f"  printed {float(t['printed']):>14,.2f}  diff {float(t['difference']):>9,.2f}  p{t['page']}")
    private.save()
    print(f"\nPrivate names coded: {len(private.codes)} in {private.lookup_path} (git-ignored), {len(private.new)} new this run.")
    for e in private.new:
        print(f"  {e['code']}  {e['name']}  ({e['first_council']}, {e['reason']})")
    if private.new:
        print("Check the new ones: a business coded by mistake goes in config/payee_not_private.txt.")
    return 0 if ok else 1


def cmd_parse(args, p) -> int:
    if args.source:
        return cmd_parse_councils(args, p)
    folder = parse.latest_folder(p["raw"])
    counts = parse.parse_folder(folder, p["parsed"], settings.ROOT)
    print(f"Parsed {folder}: " + ", ".join(f"{t} {n}" for t, n in sorted(counts.items())))
    return 0


def cmd_build(args, p) -> int:
    if p["db"].exists():
        p["db"].unlink()
    con = db.connect(p["db"])
    db.create_schema(con)
    counts = db.load_parsed(con, p["parsed"])
    decisions = score.load_decisions(p["decisions"])
    res = db.resolve_entities(con, settings.thresholds(), confirmed=decisions.get("confirmed_names", []))
    methods = {}
    for link in res.links.values():
        methods[link.method] = methods.get(link.method, 0) + 1
    con.close()
    print(f"Built {p['db']}: " + ", ".join(f"{t} {n}" for t, n in counts.items())
          + f"; {len(res.entities)} entities from {len(res.links)} names ({methods}); {len(res.reviews)} pairs for review")
    return 0


def cmd_score(args, p) -> int:
    con = db.connect(p["db"])
    th, fc = settings.thresholds(), settings.flag_config()
    hits = score.run_flags(con, th, fc)
    cases = score.build_cases(con, th, fc, score.load_decisions(p["decisions"]))
    db.write_run(con, "fixture" if args.fixture else "live", git_commit(), th)
    con.close()
    print(f"{len(hits)} flag hits, {len(cases)} cases surfaced.")
    return 0


def cmd_report(args, p) -> int:
    con = db.connect(p["db"])
    cal = score.check_calibration(con, settings.calibration(), fixture_mode=args.fixture)
    report.write(con, p["report"], calibration=cal, decisions=score.load_decisions(p["decisions"]),
                 git_commit=git_commit(), root=settings.ROOT)
    con.close()
    print(f"Report written to {p['report']}")
    return _calibration_status(cal)


def _calibration_status(cal: dict) -> int:
    for r in cal["results"]:
        if r.get("expect_flags") is not None:
            print(f"  calibration {r['id']}: {r['status']}" + (f" (rank {r['rank']})" if r.get("rank") else ""))
    for u in cal["unexpected"]:
        print(f"  unexpected flag {u['flag']} on {u['entity_id']}: {u['detail']}")
    if not cal["ok"]:
        print("Calibration check FAILED.")
        return 1
    print("Calibration check passed.")
    return 0


def cmd_run(args, p) -> int:
    for step in (cmd_parse, cmd_build, cmd_score):
        if step(args, p):
            return 1
    return cmd_report(args, p)


COMMANDS = {"fetch": cmd_fetch, "parse": cmd_parse, "build": cmd_build, "score": cmd_score,
            "report": cmd_report, "run": cmd_run}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m screener", description="Red-flag screener for public money records.")
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("--fixture", action="store_true", help="run on the synthetic fixture (phase 1)")
    ap.add_argument("--source", help="parse only: real downloads under data/raw/<source>/<council>/<date>/ (phase 2)")
    ap.add_argument("--data", help="data folder (default data/)")
    ap.add_argument("--raw", help="folder of downloaded source folders (default data/raw/fixture)")
    ap.add_argument("--db", help="database file (default data/screener.duckdb)")
    ap.add_argument("--report", help="report file (default data/report/index.html)")
    ap.add_argument("--decisions", help="reviewer decisions JSON exported from the report (default data/review/decisions.json)")
    args = ap.parse_args(argv)
    if args.command != "fetch" and not args.fixture and not (args.command == "parse" and args.source):
        print("Only parse --source runs on real files so far; everything else runs on the fixture. Add --fixture.", file=sys.stderr)
        return 2
    return COMMANDS[args.command](args, _paths(args))
