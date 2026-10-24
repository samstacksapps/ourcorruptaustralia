"""Parse step: files already downloaded in, rows with provenance out.

Each source folder data/raw/<source>/<date>/ holds the files and a manifest.json naming
each file's kind and the URL it was downloaded from. parse_folder() writes one CSV per
table to data/parsed/<source>/, every row carrying the provenance columns, so a parser
can be rerun on files already downloaded.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from screener.parse import accounts_paid, registers

PROVENANCE = ["source_url", "source_file", "page", "source_line", "fetched_at", "parser", "parser_version", "model", "prompt_id"]
COUNCIL_TABLES = {"tenders", "gifts", "interests", "disclosures", "items", "delegations", "related_parties", "candidate_gifts", "audit_findings"}


def latest_folder(source_dir: Path) -> Path:
    folders = sorted(p for p in Path(source_dir).iterdir() if (p / "manifest.json").exists())
    if not folders:
        raise FileNotFoundError(f"no downloaded folder with a manifest.json under {source_dir}")
    return folders[-1]


def parse_folder(raw_dir: Path, out_dir: Path, root: Path) -> dict[str, int]:
    """Parse every file in raw_dir's manifest and write <table>.csv files to out_dir."""
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    council_id = manifest.get("council_id")
    tables: dict[str, list[dict]] = {}
    for entry in manifest["files"]:
        path = raw_dir / entry["file"]
        prov = {
            "source_url": entry["url"],
            "source_file": _rel(path, root),
            "fetched_at": entry.get("fetched_at", manifest["fetched_at"]),
            "model": "",
            "prompt_id": "",
        }
        kind = entry["kind"]
        if kind == "accounts_paid":
            rows = accounts_paid.parse(path)
            table, parser, version = "payments", accounts_paid.PARSER, accounts_paid.PARSER_VERSION
        else:
            rows = registers.parse(path, kind)
            table, parser, version = kind, registers.PARSER, registers.PARSER_VERSION
        for r in rows:
            r.update(prov, parser=parser, parser_version=version)
            r.setdefault("page", "")
            r.setdefault("source_line", "")
            if council_id and (table in COUNCIL_TABLES or table == "payments"):
                r["council_id"] = council_id
        tables.setdefault(table, []).extend(rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.csv"):
        old.unlink()
    counts = {}
    for table, rows in tables.items():
        cols = [c for c in rows[0] if c not in PROVENANCE] + PROVENANCE if rows else PROVENANCE
        with open(out_dir / f"{table}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        counts[table] = len(rows)
    return counts


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
