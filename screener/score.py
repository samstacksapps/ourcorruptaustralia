"""Run the flags, build cases and check calibration (SPEC.md sections 7 and 9).

Scores are deterministic: each flag is one SQL file with its thresholds bound from
config/thresholds.yml and its weight from flags/flags.yml. Weak = 1, medium = 2, strong = 4;
context flags multiply. A case is (body, entity). It surfaces when it carries two or more
distinct flags or any strong flag, and ranks by total score, then money involved.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from screener import llm, settings
from screener.normalise import normalise_name

_PARAM = re.compile(r"\$([a-z_]+)")


def keyword_pattern(words: list[str]) -> str:
    """Case-insensitive whole-word pattern for a keyword list (RE2 syntax)."""
    escaped = [re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", w) for w in words]
    return r"(?i)\b(?:" + "|".join(escaped) + r")\b"


def flag_params(name: str, sql: str, thresholds: dict, flag_cfg: dict) -> dict:
    spec = flag_cfg["flags"][name]
    weight = spec["multiplier"] if spec["strength"] == "context" else flag_cfg["weights"][spec["strength"]]
    pool = {**thresholds.get("common", {}), **thresholds.get(name, {}), "weight": weight}
    params = {}
    for p in set(_PARAM.findall(sql)):
        if p not in pool:
            raise KeyError(f"flag {name} uses ${p}, which is not in thresholds.yml or flags.yml")
        v = pool[p]
        params[p] = keyword_pattern(v) if isinstance(v, list) else v
    return params


def run_flags(con, thresholds: dict, flag_cfg: dict, only: list[str] | None = None) -> list[dict]:
    """Run every flag's SQL and store the hits in flag_hits."""
    con.execute("DELETE FROM flag_hits")
    hits = []
    for name, spec in flag_cfg["flags"].items():
        if only and name not in only:
            continue
        sql = (settings.FLAGS_DIR / f"{name}.sql").read_text(encoding="utf-8")
        cur = con.execute(sql, flag_params(name, sql, thresholds, flag_cfg))
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            facts = json.loads(r["facts"]) if r.get("facts") else {}
            basis = r.get("match_basis")
            hits.append({
                "flag": name,
                "strength": spec["strength"],
                "body_id": r["body_id"],
                "entity_id": r["entity_id"],
                "score": float(r["score"]),
                "money": r.get("money"),
                "match_basis": basis,
                "held": spec["strength"] == "strong" and basis == "fuzzy",
                "detail": _detail(spec.get("detail", ""), facts),
                "facts_json": json.dumps(facts),
                "evidence_json": r["evidence_json"],
            })
    for i, h in enumerate(hits, start=1):
        h["flag_id"] = i
    if hits:
        cols = ["flag_id", "flag", "strength", "body_id", "entity_id", "score", "money", "match_basis",
                "held", "detail", "facts_json", "evidence_json"]
        con.executemany(f"INSERT INTO flag_hits ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [[h[c] for c in cols] for h in hits])
    return hits


def _detail(template: str, facts: dict) -> str:
    try:
        return template.format(**facts)
    except (KeyError, ValueError, TypeError):
        return "; ".join(f"{k}: {v}" for k, v in facts.items())


def build_cases(con, thresholds: dict, flag_cfg: dict, decisions: dict | None = None) -> list[dict]:
    """Group hits into cases, score, rank and store them in cases."""
    decisions = (decisions or {}).get("cases", {})
    min_flags = thresholds["scoring"]["min_distinct_flags"]
    rows = con.execute("""
        SELECT flag, strength, body_id, entity_id, score, money, held, detail
        FROM flag_hits ORDER BY flag_id
    """).fetchall()

    multiplier: dict[str, float] = {}
    groups: dict[tuple, list] = {}
    for flag, strength, body, entity, score, money, held, detail in rows:
        if strength == "context":
            multiplier[body] = multiplier.get(body, 1.0) * score
        elif not held and entity is not None:
            groups.setdefault((body, entity), []).append((flag, strength, score, money, detail))

    cases = []
    for (body, entity), hits in groups.items():
        per_flag: dict[str, tuple] = {}
        for flag, strength, score, money, detail in hits:
            per_flag.setdefault(flag, (strength, score, detail))
        strong = any(s == "strong" for s, _, _ in per_flag.values())
        if len(per_flag) < min_flags and not strong:
            continue
        total = sum(score for _, score, _ in per_flag.values()) * multiplier.get(body, 1.0)
        paid = con.execute("SELECT sum(amount) FROM payments WHERE council_id = ? AND entity_id = ?",
                           [body, entity]).fetchone()[0]
        money = paid if paid is not None else max((m or 0) for *_, m, _ in hits)
        key = f"{body}|{entity}"
        summary, model, prompt_id = llm.summarise_case([d for _, _, d in per_flag.values()])
        cases.append({
            "case_key": key, "body_id": body, "entity_id": entity, "score": round(total, 2),
            "money": float(money or 0), "n_flags": len(per_flag), "flags": list(per_flag),
            "summary": summary, "summary_model": model, "summary_prompt_id": prompt_id,
            "status": decisions.get(key, {}).get("status", "open"),
        })
    cases.sort(key=lambda c: (-c["score"], -c["money"], c["case_key"]))
    for i, c in enumerate(cases, start=1):
        c["rank"] = i

    con.execute("DELETE FROM cases")
    if cases:
        cols = ["case_key", "rank", "body_id", "entity_id", "score", "money", "n_flags", "flags",
                "summary", "summary_model", "summary_prompt_id", "status"]
        con.executemany(f"INSERT INTO cases ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [[c[k] for k in cols] for c in cases])
    return cases


def check_calibration(con, calibration: dict, fixture_mode: bool) -> dict:
    """Check the known cases. Returns {ok, results, unexpected}."""
    results, unexpected, ok = [], [], True
    fx = calibration.get("fixture")
    if fixture_mode and fx:
        planted_ids = set()
        for case in fx["cases"]:
            link = con.execute("SELECT entity_id FROM entity_links WHERE name_norm = ?",
                               [normalise_name(case["entity"])]).fetchone()
            eid = link[0] if link else None
            planted_ids.add(eid)
            row = con.execute("""
                SELECT rank, flags,
                       (SELECT count(*) FROM cases c2 WHERE c2.body_id = c.body_id AND c2.rank <= c.rank) AS body_rank
                FROM cases c WHERE body_id = ? AND entity_id = ?
            """, [fx["body_id"], eid]).fetchone()
            if row is None:
                results.append({**case, "status": "missed", "rank": None, "flags": []})
                ok = False
                continue
            rank, flags, body_rank = row
            missing = sorted(set(case["expect_flags"]) - set(flags))
            passed = body_rank <= fx["top_n"] and not missing
            ok &= passed
            results.append({**case, "status": "found" if passed else "found, check failed",
                            "rank": body_rank, "flags": list(flags), "missing_flags": missing})
        unexpected = [
            {"entity_id": e, "body_id": b, "flag": f, "detail": d}
            for f, b, e, d in con.execute(
                "SELECT flag, body_id, entity_id, detail FROM flag_hits WHERE entity_id IS NOT NULL ORDER BY flag_id"
            ).fetchall()
            if e not in planted_ids
        ]
        ok &= not unexpected
    for case in calibration.get("known_cases", []):
        results.append({**case, "status": f"not checked: source loads in phase {case['phase']}"
                        if str(case["phase"]).isdigit() else "not checked: template only", "rank": None})
    return {"ok": ok, "results": results, "unexpected": unexpected}


def load_decisions(path: Path | None) -> dict:
    """Reviewer decisions exported from the report: case statuses, notes, confirmed matches."""
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {"cases": {}, "confirmed_names": []}
