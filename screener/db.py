"""Schema, loaders and provenance (SPEC.md sections 3 and 5).

Every source table carries the provenance columns. Each one also has a row_id so a flag
can cite the exact rows it rests on, and an <table>_ev view that packs a row into the
uniform evidence struct the flags and the report use.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from screener import normalise

PROVENANCE = """
    source_url VARCHAR, source_file VARCHAR, page INTEGER, source_line INTEGER,
    fetched_at TIMESTAMP, parser VARCHAR, parser_version VARCHAR, model VARCHAR, prompt_id VARCHAR
"""

# Section 5 tables. Phase 1 loads the council tables and the ABN register; the federal
# tables are created so the schema is whole, and are filled from phase 6.
TABLES = {
    "councils": "council_id VARCHAR, name VARCHAR, region VARCHAR, website VARCHAR, minutes_url VARCHAR, population INTEGER, notes VARCHAR",
    "payments": "council_id VARCHAR, paid_date DATE, ref VARCHAR, payee_raw VARCHAR, payee_norm VARCHAR, entity_id VARCHAR, description VARCHAR, amount DOUBLE, approver VARCHAR, fy INTEGER",
    "tenders": "council_id VARCHAR, tender_ref VARCHAR, description VARCHAR, closed_date DATE, awarded_date DATE, tenderer_raw VARCHAR, tenderer_norm VARCHAR, entity_id VARCHAR, is_winner BOOLEAN, contract_amount DOUBLE",
    "gifts": "council_id VARCHAR, recipient_name VARCHAR, recipient_role VARCHAR, giver_raw VARCHAR, giver_norm VARCHAR, entity_id VARCHAR, value DOUBLE, received_date DATE, description VARCHAR",
    "interests": "council_id VARCHAR, person VARCHAR, person_key VARCHAR, role VARCHAR, interest_type VARCHAR, entity_raw VARCHAR, entity_norm VARCHAR, entity_id VARCHAR, detail VARCHAR, return_type VARCHAR, return_date DATE",
    "disclosures": "council_id VARCHAR, meeting_date DATE, item_ref VARCHAR, item_title VARCHAR, person VARCHAR, person_key VARCHAR, interest_type VARCHAR",
    "items": "council_id VARCHAR, meeting_date DATE, item_ref VARCHAR, item_title VARCHAR, entities_mentioned VARCHAR[], entities_norm VARCHAR[], entity_ids VARCHAR[]",
    "delegations": "council_id VARCHAR, role VARCHAR, limit_amount DOUBLE, source_date DATE",
    "related_parties": "council_id VARCHAR, fy INTEGER, counterparty_raw VARCHAR, counterparty_norm VARCHAR, entity_id VARCHAR, amount DOUBLE, nature VARCHAR",
    "candidate_gifts": "council_id VARCHAR, election_date DATE, candidate VARCHAR, giver_raw VARCHAR, giver_norm VARCHAR, entity_id VARCHAR, value DOUBLE",
    "audit_findings": "council_id VARCHAR, report_year INTEGER, report_title VARCHAR, area VARCHAR, finding VARCHAR",
    "agencies": "agency_id VARCHAR, name VARCHAR, portfolio VARCHAR, annual_report_url VARCHAR, senate_order_url VARCHAR",
    "contracts": "cn_id VARCHAR, agency_id VARCHAR, supplier_raw VARCHAR, supplier_abn VARCHAR, entity_id VARCHAR, value_original DOUBLE, value_current DOUBLE, start_date DATE, end_date DATE, published_date DATE, procurement_method VARCHAR, limited_tender_reason VARCHAR, confidentiality_contract BOOLEAN, confidentiality_outputs BOOLEAN, category VARCHAR, description VARCHAR, amendment_of VARCHAR",
    "contract_changes": "cn_id VARCHAR, changed_at TIMESTAMP, field VARCHAR, old_value VARCHAR, new_value VARCHAR",
    "grants": "ga_id VARCHAR, agency_id VARCHAR, recipient_raw VARCHAR, recipient_abn VARCHAR, entity_id VARCHAR, value DOUBLE, program VARCHAR, selection_process VARCHAR, postcode VARCHAR, electorate VARCHAR, awarded_date DATE",
    "donations": "fy INTEGER, donor_raw VARCHAR, entity_id VARCHAR, recipient_party VARCHAR, amount DOUBLE, return_type VARCHAR",
    "lobbyists": "firm VARCHAR, person VARCHAR, former_government BOOLEAN, client_raw VARCHAR, entity_id VARCHAR, registered_from DATE, registered_to DATE",
    "annual_report_tables": "agency_id VARCHAR, fy INTEGER, table_name VARCHAR, row_label VARCHAR, column_label VARCHAR, value DOUBLE",
    "kmp_remuneration": "agency_id VARCHAR, fy INTEGER, person VARCHAR, position VARCHAR, total DOUBLE, termination_benefits DOUBLE",
    "senate_order": "agency_id VARCHAR, period VARCHAR, contract_ref VARCHAR, supplier_raw VARCHAR, entity_id VARCHAR, value DOUBLE, confidentiality BOOLEAN",
    "people": "person_id VARCHAR, name VARCHAR, role VARCHAR, body VARCHAR, from_date DATE, to_date DATE",
    "declared_entities": "person_id VARCHAR, entity_raw VARCHAR, entity_id VARCHAR, relationship VARCHAR, source VARCHAR",
    "abn": "abn VARCHAR, name_raw VARCHAR, name_norm VARCHAR, entity_type VARCHAR, status VARCHAR, status_from DATE, state VARCHAR, postcode VARCHAR",
    "snapshots": "source VARCHAR, captured_at TIMESTAMP, record_id VARCHAR, content_hash VARCHAR",
}

# Tables that are derived during a run rather than loaded from source files.
DERIVED = {
    "entities": "entity_id VARCHAR PRIMARY KEY, canonical_name VARCHAR, abn VARCHAR, acn VARCHAR",
    "entity_links": "name_norm VARCHAR PRIMARY KEY, entity_id VARCHAR, method VARCHAR, score DOUBLE",
    "match_reviews": "name_a VARCHAR, name_b VARCHAR, score DOUBLE, answer VARCHAR, model VARCHAR, prompt_id VARCHAR",
    "flag_hits": "flag_id INTEGER, flag VARCHAR, strength VARCHAR, body_id VARCHAR, entity_id VARCHAR, score DOUBLE, money DOUBLE, match_basis VARCHAR, held BOOLEAN, detail VARCHAR, facts_json VARCHAR, evidence_json VARCHAR",
    "cases": "case_key VARCHAR, rank INTEGER, body_id VARCHAR, entity_id VARCHAR, score DOUBLE, money DOUBLE, n_flags INTEGER, flags VARCHAR[], summary VARCHAR, summary_model VARCHAR, summary_prompt_id VARCHAR, status VARCHAR",
    "runs": "run_at TIMESTAMP, mode VARCHAR, git_commit VARCHAR, thresholds_json VARCHAR",
}

# Which column of each table holds a name to resolve, and where its normal form goes.
NAME_COLUMNS = {
    "payments": ("payee_raw", "payee_norm"),
    "tenders": ("tenderer_raw", "tenderer_norm"),
    "gifts": ("giver_raw", "giver_norm"),
    "interests": ("entity_raw", "entity_norm"),
    "related_parties": ("counterparty_raw", "counterparty_norm"),
    "candidate_gifts": ("giver_raw", "giver_norm"),
}

DATE_COLUMNS = {
    "payments": ["paid_date"], "tenders": ["closed_date", "awarded_date"], "gifts": ["received_date"],
    "interests": ["return_date"], "disclosures": ["meeting_date"], "items": ["meeting_date"],
    "delegations": ["source_date"], "candidate_gifts": ["election_date"], "abn": ["status_from"],
}

# The uniform evidence struct: table, row, date, name, amount, detail, and where it came from.
EVIDENCE = {
    "payments": ("paid_date", "payee_raw", "amount", "concat_ws(' ', ref, description)"),
    "tenders": ("coalesce(awarded_date, closed_date)", "tenderer_raw", "contract_amount",
                "concat_ws(' ', tender_ref, description, CASE WHEN is_winner THEN '(winner)' ELSE '(tenderer)' END)"),
    "gifts": ("received_date", "giver_raw", "value", "concat_ws(' ', 'to', recipient_name, '(' || recipient_role || ')', '-', description)"),
    "interests": ("return_date", "entity_raw", "NULL", "concat_ws(' ', person, '(' || role || ')', interest_type, '-', detail, '(' || return_type || ' return)')"),
    "disclosures": ("meeting_date", "person", "NULL", "concat_ws(' ', item_ref, item_title, '-', interest_type)"),
    "items": ("meeting_date", "array_to_string(entities_mentioned, '; ')", "NULL", "concat_ws(' ', item_ref, item_title)"),
    "delegations": ("source_date", "role", "limit_amount", "'delegated approval limit'"),
    "related_parties": ("NULL", "counterparty_raw", "amount", "concat_ws(' ', 'FY', fy, nature)"),
    "candidate_gifts": ("election_date", "giver_raw", "value", "concat_ws(' ', 'gift to candidate', candidate)"),
    "audit_findings": ("NULL", "council_id", "NULL", "concat_ws(' ', report_year, report_title, '-', area, ':', finding)"),
    "abn": ("status_from", "name_raw", "NULL", "concat_ws(' ', 'ABN', abn, entity_type, status, 'from', status_from, state, postcode)"),
}

MACROS = [
    # Financial year ending 30 June, stored as the ending year (2025 is 2024-25).
    "CREATE OR REPLACE MACRO fy(d) AS CASE WHEN month(d) >= 7 THEN year(d) + 1 ELSE year(d) END",
    "CREATE OR REPLACE MACRO fy_label(y) AS CAST(y - 1 AS VARCHAR) || '-' || right(CAST(y AS VARCHAR), 2)",
    # How two names on one entity were matched. Strong flags need anything but fuzzy.
    """CREATE OR REPLACE MACRO strong_basis(name_a, method_a, name_b, method_b) AS
        CASE WHEN name_a = name_b THEN 'exact'
             WHEN method_a IN ('exact', 'abn', 'confirmed') AND method_b IN ('exact', 'abn', 'confirmed')
                  THEN CASE WHEN 'confirmed' IN (method_a, method_b) THEN 'confirmed' ELSE 'abn' END
             ELSE 'fuzzy' END""",
    """CREATE OR REPLACE MACRO best_basis(bases) AS
        CASE WHEN list_contains(bases, 'exact') THEN 'exact'
             WHEN list_contains(bases, 'abn') THEN 'abn'
             WHEN list_contains(bases, 'confirmed') THEN 'confirmed'
             ELSE 'fuzzy' END""",
]


def connect(path: Path | str) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    for name, cols in TABLES.items():
        con.execute(f"CREATE OR REPLACE TABLE {name} (row_id BIGINT, {cols}, {PROVENANCE})")
    for name, cols in DERIVED.items():
        con.execute(f"CREATE OR REPLACE TABLE {name} ({cols})")
    for macro in MACROS:
        con.execute(macro)
    for name, (d, nm, amt, det) in EVIDENCE.items():
        con.execute(f"""
            CREATE OR REPLACE VIEW {name}_ev AS
            SELECT *, struct_pack(
                tbl := '{name}', row_id := CAST(row_id AS BIGINT), date := CAST({d} AS VARCHAR),
                name := CAST({nm} AS VARCHAR), amount := CAST({amt} AS DOUBLE), detail := CAST({det} AS VARCHAR),
                source_url := source_url, source_file := source_file,
                page := CAST(page AS INTEGER), line := CAST(source_line AS INTEGER)
            ) AS ev
            FROM {name}
        """)


def load_parsed(con: duckdb.DuckDBPyConnection, parsed_dir: Path) -> dict[str, int]:
    """Load every <table>.csv in parsed_dir into its table, normalising names on the way."""
    counts = {}
    next_id = 1
    for name in TABLES:
        path = parsed_dir / f"{name}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        df = df.replace({"": None})
        df = _prepare(name, df)
        df.insert(0, "row_id", range(next_id, next_id + len(df)))
        next_id += len(df)
        con.register("_incoming", df)
        con.execute(f"INSERT INTO {name} BY NAME SELECT * FROM _incoming")
        con.unregister("_incoming")
        counts[name] = len(df)
    return counts


def _prepare(name: str, df: pd.DataFrame) -> pd.DataFrame:
    for col in DATE_COLUMNS.get(name, []):
        if col in df:
            df[col] = pd.to_datetime(df[col], format="%Y-%m-%d").dt.date
    if name in NAME_COLUMNS:
        raw, norm = NAME_COLUMNS[name]
        df[norm] = df[raw].map(lambda v: normalise.normalise_name(v) or None)
    if name in ("interests", "disclosures"):
        df["person_key"] = df["person"].map(normalise.person_key)
    if name == "items":
        df["entities_mentioned"] = df["entities_mentioned"].map(
            lambda v: [s.strip() for s in v.split("|") if s.strip()] if v else []
        )
        df["entities_norm"] = df["entities_mentioned"].map(
            lambda names: [normalise.normalise_name(n) for n in names]
        )
    if name == "abn":
        df["name_norm"] = df["name_raw"].map(normalise.normalise_name)
    if name == "payments" and "fy" not in df:
        df["fy"] = df["paid_date"].map(lambda d: d.year + 1 if d.month >= 7 else d.year)
    if name == "tenders" and "is_winner" in df:
        df["is_winner"] = df["is_winner"].str.lower().isin(["true", "1", "yes", "y"])
    return df


def resolve_entities(con: duckdb.DuckDBPyConnection, thresholds: dict, *, confirmed=None, same_entity=None):
    """Cluster every name into entities and write entity_id back onto the source rows."""
    names = []
    for table, (_, norm) in NAME_COLUMNS.items():
        names += [r[0] for r in con.execute(f"SELECT DISTINCT {norm} FROM {table} WHERE {norm} IS NOT NULL").fetchall()]
    names += [r[0] for r in con.execute("SELECT DISTINCT unnest(entities_norm) FROM items").fetchall()]
    abn_names = con.execute("SELECT DISTINCT abn, name_norm FROM abn").fetchall()

    er = thresholds["entity_resolution"]
    res = normalise.resolve(
        names, abn_names,
        fuzzy_link_min=er["fuzzy_link_min"], model_review_min=er["model_review_min"],
        fuzzy_min_tokens=er["fuzzy_min_tokens"], same_entity=same_entity, confirmed=set(confirmed or []),
    )

    con.execute("DELETE FROM entity_links")
    con.execute("DELETE FROM entities")
    con.execute("DELETE FROM match_reviews")
    con.executemany("INSERT INTO entity_links VALUES (?, ?, ?, ?)",
                    [(l.name_norm, l.entity_id, l.method, l.score) for l in res.links.values()])
    con.executemany("INSERT INTO entities VALUES (?, ?, ?, ?)",
                    [(eid, e["canonical_name"], e["abn"], e["acn"]) for eid, e in res.entities.items()])
    if res.reviews:
        con.executemany("INSERT INTO match_reviews VALUES (?, ?, ?, ?, ?, ?)",
                        [(r["name_a"], r["name_b"], r["score"], r["answer"], r["model"], r["prompt_id"]) for r in res.reviews])

    for table, (_, norm) in NAME_COLUMNS.items():
        con.execute(f"""
            UPDATE {table} SET entity_id = l.entity_id
            FROM entity_links l WHERE l.name_norm = {table}.{norm}
        """)
    item_rows = con.execute("SELECT row_id, entities_norm FROM items").fetchall()
    if item_rows:
        ids = pd.DataFrame({
            "row_id": [r[0] for r in item_rows],
            "ids": [[res.links[n].entity_id if n in res.links else None for n in (r[1] or [])] for r in item_rows],
        })
        con.register("_item_ids", ids)
        con.execute("UPDATE items SET entity_ids = CAST(_item_ids.ids AS VARCHAR[]) FROM _item_ids WHERE _item_ids.row_id = items.row_id")
        con.unregister("_item_ids")
    return res


def write_run(con, mode: str, git_commit: str, thresholds: dict) -> None:
    con.execute("INSERT INTO runs VALUES (now(), ?, ?, ?)", [mode, git_commit, json.dumps(thresholds)])
