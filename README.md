# Our Corrupt Australia

A red-flag screener for Australian public money records. It loads council and federal
records into one DuckDB database, tests them against each other, and writes a short,
ranked queue of cases where two or more independent signals line up, each with the
evidence rows and the file and page they came from. A red flag is a question, not a
finding. See [SPEC.md](SPEC.md) for the rules, data, tests and build order.

## Status: phase 1

Phase 1 runs on a synthetic council ("Shire of Example") and fetches nothing from the
internet. It has the schema, the name normaliser and entity resolution, the fourteen
council flags, scoring, the HTML report and the FOI draft. Fetching and parsing real
council records start in phase 2.

## Run it

Python 3.11 or later.

```
python -m venv .venv
.venv\Scripts\activate          (Windows)   or   source .venv/bin/activate
pip install -r requirements.txt

python fixtures/make_fixture.py
python -m screener run --fixture
pytest
```

`run --fixture` parses the newest folder under `data/raw/fixture/`, builds
`data/screener.duckdb`, runs the flags, and writes `data/report/index.html`. It then checks
calibration: it exits with an error if a planted case is missed or any other entity
carries a flag. The steps can also run one at a time: `parse`, `build`, `score`,
`report`, each with `--fixture`.

## Layout

| Path | What it holds |
|---|---|
| `config/thresholds.yml` | Every number a flag depends on, with its source |
| `config/calibration.yml` | Known cases the run must find; the fixture's six planted cases |
| `screener/flags/*.sql` | One file per flag; `flags.yml` holds strength, weight and the detail line |
| `screener/normalise.py` | Name cleaning and entity clustering |
| `screener/db.py` | Schema, loaders, provenance, evidence views |
| `screener/score.py` | Runs flags, builds and ranks cases, checks calibration |
| `screener/report.py` | Self-contained HTML case queue |
| `screener/foi.py` | FOI request draft for a case |
| `screener/llm.py` | The only module that may call a model (offline in phase 1) |
| `fixtures/make_fixture.py` | The synthetic council and its accounts-paid PDF |

## Reviewing cases

The report keeps notes and the noise, keep and refer buttons in the browser. "Export
decisions.json" downloads them. Save the file as `data/review/decisions.json` and the next
run reads it: cases marked noise move to a dismissed section, and fuzzy name matches you
confirmed may then carry strong flags. `data/review/` is not committed, because notes can
hold names.
