"""Entity-level findings from the Auditor General's local government financial audit results.

Written for Report 13: 2025-26 (Local Government 2025 - Financial Audit Results). Reads the
newest data/raw/oag/<date>/ folder (downloaded file plus manifest.json) and
    python -m screener.parse.oag_report

writes to data/parsed/oag/:
  entities.csv        one row per entity named in Appendix 1 or 9, with the report's facts about it
  audit_findings.csv  one row per entity-level finding (the schema's audit_findings table), with page

The report attributes no expenditure or purchasing weakness to any entity (PDF page 25: "109
expenditure weaknesses to 61 entities", sector totals only), so none is recorded here.
Page numbers are PDF pages; the printed page number is the PDF page minus 4.
"""

import csv
import json
import re
from collections import Counter
from pathlib import Path

import pdfplumber

from screener.parse import latest_folder
from screener.settings import ROOT

RAW = latest_folder(ROOT / "data/raw/oag")
OUT = ROOT / "data/parsed/oag"
MANIFEST = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
ENTRY = MANIFEST["files"][0]
PDF = RAW / ENTRY["file"]
PROV = {"source_url": ENTRY["url"], "source_file": PDF.relative_to(ROOT).as_posix(),
        "fetched_at": MANIFEST["fetched_at"], "parser": "parse.oag_report", "parser_version": "1"}
TITLE = "Report 13: 2025-26 Local Government 2025 - Financial Audit Results"
START = re.compile(r"^(Shire of|City of|Town of|Catalina|Eastern|Mindarie|Murchison|Resource|Rivers|Western|Bunbury)\b")
DELAYED = re.compile(r"^(Delayed 2024|2024 delayed)")
SKIP = re.compile(r"^(Delayed 2024|2024 delayed|The below list|which had their|those entities|Entity (Details|Description)|"
                  r"Source:|Appendix|disclaimers removed|included in auditor|related to going|2025 (qualifications|emphasis))")
NAME_FIX = {"Shire of Wyndam-East Kimberley": "Shire of Wyndham-East Kimberley",
            "City of Kalgoorlie - Boulder": "City of Kalgoorlie-Boulder",
            "Shire of Donnybrook Balingup": "Shire of Donnybrook-Balingup",
            "Shire of Bridgetown Greenbushes": "Shire of Bridgetown-Greenbushes",
            "Murchison regional vermin council": "Murchison Regional Vermin Council"}


def clean(name):
    name = re.sub(r"\s*-\s*", "-", " ".join(name.split()))
    return NAME_FIX.get(name, name)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def page_lines(pdf, n):
    return [l.strip() for l in (pdf.pages[n - 1].extract_text() or "").splitlines() if l.strip()]


def appendix1(pdf):
    rows, last = [], None
    row = re.compile(r"^(\d+) (.+?) (\d|Other) (\d{1,2}/\d{1,2}/\d{4})$")
    for n in range(36, 42):
        for line in page_lines(pdf, n):
            m = row.match(line)
            if m:
                last = {"entity": m.group(2), "band": m.group(3), "opinion_issued": m.group(4), "page": n}
                rows.append(last)
            elif last and re.fullmatch(r"[A-Z][a-z]+( [A-Z][a-z]+)*", line):
                last["entity"] += ("" if last["entity"].endswith("-") else " ") + line
                last = None
            else:
                last = None
    for r in rows:
        r["entity"] = clean(r["entity"])
    return rows


def appendix9(pdf):
    out = {}
    row = re.compile(r"^(.+?) (\d|Other) ([\d.]+|NCWST) ([\d.]+|NCWST)$")
    for n in range(55, 60):
        for line in page_lines(pdf, n):
            m = row.match(line)
            if m and START.match(m.group(1)):
                out[clean(m.group(1))] = {"band": m.group(2), "cr_2025": m.group(3), "cr_2024": m.group(4), "page": n}
    return out


def two_column(pdf, pages):
    """Entity column on the left, details on the right. Returns blocks with a delayed-2024 marker."""
    blocks, delayed = [], False
    for n in pages:
        p = pdf.pages[n - 1]
        heads = [w for w in p.extract_words() if w["text"] in ("Details", "Description")]
        if not heads:
            continue
        split = heads[0]["x0"] - 3
        bottom = p.height - 55
        events = []  # (top, kind, text)
        for ln in p.crop((0, 0, p.width, bottom)).extract_text_lines():
            text = ln["text"].strip()
            if SKIP.match(text):
                events.append((ln["top"], "delayed" if DELAYED.match(text) else "skip", text))
        skip_tops = [t for t, _, _ in events]
        for side, x0, x1 in (("L", 0, split), ("R", split, p.width)):
            for ln in p.crop((x0, 0, x1, bottom)).extract_text_lines():
                if ln["top"] < heads[0]["top"] - 2:
                    continue
                if any(abs(ln["top"] - t) < 3 for t in skip_tops):
                    continue
                events.append((ln["top"], side, ln["text"].strip()))
        last_left_top = None
        for top, kind, text in sorted(events, key=lambda e: (e[0], e[1] != "L")):
            if kind == "delayed":
                delayed = True
            elif kind == "L":
                if START.match(text) or not blocks or last_left_top is None or top - last_left_top > 14:
                    blocks.append({"name": [text], "detail": [], "page": n, "delayed": delayed})
                else:
                    blocks[-1]["name"].append(text)
                last_left_top = top
            elif kind == "R" and blocks:
                blocks[-1]["detail"].append(text)
    out = []
    for b in blocks:
        name = clean(" ".join(b["name"]))
        if START.match(name):
            out.append({"entity": name, "detail": " ".join(b["detail"]), "page": b["page"], "delayed": b["delayed"]})
    return out


def main():
    findings = []

    def add(entity, area, finding, page):
        findings.append({"council_id": slug(entity), "entity": entity, "report_year": 2025, "report_title": TITLE,
                         "area": area, "finding": finding, "page": page, **PROV})

    with pdfplumber.open(PDF) as pdf:
        a1, a9 = appendix1(pdf), appendix9(pdf)
        for q in two_column(pdf, [44, 45, 46]):
            add(q["entity"], "Qualified or disclaimer (delayed 2024 audit)" if q["delayed"] else "Qualified opinion 2025", q["detail"], q["page"])
        for r in two_column(pdf, [47, 48]):
            add(r["entity"], "Prior year qualification or disclaimer removed in 2025", r["detail"], r["page"])
        for e in two_column(pdf, [49, 50, 51]):
            add(e["entity"], "Emphasis of matter (delayed 2024 audit)" if e["delayed"] else "Emphasis of matter 2025", e["detail"], e["page"])
        for m in two_column(pdf, [52]):
            add(m["entity"], "Material uncertainty related to going concern (delayed 2024 audit)", m["detail"], m["page"])
        for line in page_lines(pdf, 43):
            m = re.match(r"^(.+?) (\d{1,2} \w+ 2025)$", line)
            if m and START.match(m.group(1)):
                add(clean(m.group(1)), "Extension to submit 2025 financial report", f"Extension approved to {m.group(2)}", 43)
        for line in page_lines(pdf, 42):
            m = re.match(r"^(.+?) 30 June 2024 (\d{1,2} \w+ 2025)$", line)
            if m:
                name = m.group(1)
                add(clean(name.rstrip("*")), "2024 audit finalised late (in 2025)",
                    f"2024 opinion issued {m.group(2)}" + ("; 2025 audit also delayed" if name.endswith("*") else ""), 42)
        for line in page_lines(pdf, 21):
            for part in re.findall(r"• ([^•]+)", line):
                name = clean(part.strip().rstrip("*").strip())
                if not START.match(name):
                    continue  # the rating criteria are bullets too
                add(name, "Best practice entity 2025",
                    "Top achiever for financial reporting practice (criteria include number and significance of management letter findings)", 21)
    for name, v in a9.items():
        if v["cr_2025"] == "NCWST":
            add(name, "2025 audit not completed within statutory timeframe", "Current ratio not available: audit not completed (NCWST)", v["page"])
        elif float(v["cr_2025"]) < 1:
            add(name, "Current ratio below 1", f"Current ratio {v['cr_2025']} (2024: {v['cr_2024']})", v["page"])
    for name in ("Shire of Toodyay", "Town of Claremont"):
        add(name, "Most adjusted errors 2025", "Equal largest number of adjusted errors in 2025 (10)", 20)

    entities = {}
    for r in a1:
        entities[r["entity"]] = {"entity": r["entity"], "band": r["band"], "opinion_issued_2025": r["opinion_issued"], "appendix1_page": r["page"]}
    for name, v in a9.items():
        e = entities.setdefault(name, {"entity": name, "band": v["band"], "opinion_issued_2025": "", "appendix1_page": ""})
        e.update(current_ratio_2025=v["cr_2025"], current_ratio_2024=v["cr_2024"], appendix9_page=v["page"])
    for e in entities.values():
        areas = sorted({f["area"] for f in findings if f["entity"] == e["entity"]})
        e.update(council_id=slug(e["entity"]), findings="; ".join(areas),
                 qualified_2025="yes" if "Qualified opinion 2025" in areas else "no",
                 late_audit="yes" if any(a.startswith(("2024 audit finalised late", "2025 audit not completed")) for a in areas) else "no",
                 **PROV)
    unknown = sorted({f["entity"] for f in findings} - set(entities))

    OUT.mkdir(parents=True, exist_ok=True)
    cols = ["entity", "council_id", "band", "opinion_issued_2025", "current_ratio_2025", "current_ratio_2024",
            "qualified_2025", "late_audit", "findings", "appendix1_page", "appendix9_page"] + list(PROV)
    with open(OUT / "entities.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(entities.values(), key=lambda e: e["entity"]))
    fcols = ["council_id", "entity", "report_year", "report_title", "area", "finding", "page"] + list(PROV)
    with open(OUT / "audit_findings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fcols)
        w.writeheader()
        w.writerows(findings)

    print(f"Appendix 1: {len(a1)} entities. Appendix 9: {len(a9)}. entities.csv: {len(entities)} rows. audit_findings.csv: {len(findings)} rows.")
    for area, n in Counter(f["area"] for f in findings).most_common():
        print(f"  {n:3d}  {area}")
    print("Finding entities not in Appendix 1 or 9:", unknown or "none")


if __name__ == "__main__":
    main()
