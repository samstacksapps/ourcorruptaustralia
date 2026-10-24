"""Write the self-contained HTML case queue (SPEC.md section 11).

One file, no framework, no build step, works on a phone. Reviewer actions (notes, noise,
keep, refer, FOI sent, confirmed matches) live in the browser's localStorage and are
exported as decisions.json, which the next run reads so dismissed noise stays dismissed.
The report is for the person running the tool: a red flag is a question, not a finding.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from screener import foi, reconcile


def write(con, path: Path, *, calibration: dict, decisions: dict, git_commit: str, root: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ctx = _context(con, path.parent, decisions)
    html = _render(ctx, calibration, decisions, git_commit, reconcile.run_all(con))
    path.write_text(html, encoding="utf-8")
    return path


# ---------------------------------------------------------------- data

def _context(con, report_dir: Path, decisions: dict) -> dict:
    bodies = {r[0]: r[1] for r in con.execute("SELECT council_id, name FROM councils").fetchall()}
    entities = {r[0]: {"name": r[1], "abn": r[2]} for r in con.execute(
        "SELECT entity_id, canonical_name, abn FROM entities").fetchall()}
    # Prefer a raw name as seen in the records over the normalised canonical name.
    for eid, raw in con.execute("""
        SELECT entity_id, mode(name) FROM (
            SELECT entity_id, payee_raw AS name FROM payments UNION ALL
            SELECT entity_id, tenderer_raw FROM tenders UNION ALL
            SELECT entity_id, giver_raw FROM gifts UNION ALL
            SELECT entity_id, counterparty_raw FROM related_parties UNION ALL
            SELECT entity_id, entity_raw FROM interests
        ) WHERE entity_id IS NOT NULL AND name IS NOT NULL GROUP BY entity_id
    """).fetchall():
        entities.setdefault(eid, {"abn": None})["name"] = raw

    hits: dict[tuple, list] = {}
    held = []
    for row in con.execute("""
        SELECT flag, strength, body_id, entity_id, score, money, match_basis, held, detail, evidence_json
        FROM flag_hits ORDER BY flag_id
    """).fetchall():
        flag, strength, body, entity, sc, money, basis, is_held, detail, ev = row
        h = {"flag": flag, "strength": strength, "score": sc, "money": money, "basis": basis,
             "detail": detail, "evidence": [_link(e, report_dir) for e in json.loads(ev or "[]")]}
        if is_held:
            held.append({**h, "body_id": body, "entity_id": entity})
        else:
            hits.setdefault((body, entity), []).append(h)

    cases = []
    for c in con.execute("""
        SELECT case_key, rank, body_id, entity_id, score, money, n_flags, flags, summary, summary_model, summary_prompt_id, status
        FROM cases ORDER BY rank
    """).fetchall():
        key, rank, body, entity, sc, money, n, flags, summary, model, prompt_id, status = c
        span = con.execute("SELECT min(paid_date), max(paid_date) FROM payments WHERE council_id = ? AND entity_id = ?",
                           [body, entity]).fetchone()
        refs = [r[0] for r in con.execute(
            "SELECT DISTINCT tender_ref FROM tenders WHERE council_id = ? AND entity_id = ? AND is_winner ORDER BY 1",
            [body, entity]).fetchall()]
        name = entities.get(entity, {}).get("name", entity)
        cases.append({
            "key": key, "rank": rank, "body_id": body, "body": bodies.get(body, body), "entity_id": entity,
            "entity": name, "score": sc, "money": money, "flags": list(flags), "summary": summary,
            "summary_model": model, "summary_prompt_id": prompt_id, "status": status,
            "hits": hits.get((body, entity), []),
            "abn": _abn_facts(con, entities.get(entity, {}).get("abn")),
            "matches": _matches(con, body, entity),
            "foi": foi.draft(bodies.get(body, body), "council", name,
                             span[0] and span[0].strftime("%d %B %Y"), span[1] and span[1].strftime("%d %B %Y"), refs),
            "links": [r[0] for r in con.execute(
                "SELECT DISTINCT name_norm || ' (' || method || ')' FROM entity_links WHERE entity_id = ? ORDER BY 1",
                [entity]).fetchall()],
        })
    for h in held:
        h["entity"] = entities.get(h["entity_id"], {}).get("name", h["entity_id"])
        h["body"] = bodies.get(h["body_id"], h["body_id"])
        h["names"] = [r[0] for r in con.execute(
            "SELECT name_norm FROM entity_links WHERE entity_id = ? AND method = 'fuzzy'", [h["entity_id"]]).fetchall()]

    context_flags = [{"body": bodies.get(b, b), "detail": d} for b, d in con.execute(
        "SELECT body_id, detail FROM flag_hits WHERE strength = 'context'").fetchall()]
    sources = con.execute("""
        SELECT source_file, source_url, min(fetched_at), max(parser) FROM (
            SELECT source_file, source_url, fetched_at, parser FROM payments UNION ALL
            SELECT source_file, source_url, fetched_at, parser FROM tenders UNION ALL
            SELECT source_file, source_url, fetched_at, parser FROM gifts UNION ALL
            SELECT source_file, source_url, fetched_at, parser FROM interests UNION ALL
            SELECT source_file, source_url, fetched_at, parser FROM disclosures UNION ALL
            SELECT source_file, source_url, fetched_at, parser FROM items UNION ALL
            SELECT source_file, source_url, fetched_at, parser FROM delegations UNION ALL
            SELECT source_file, source_url, fetched_at, parser FROM related_parties UNION ALL
            SELECT source_file, source_url, fetched_at, parser FROM candidate_gifts UNION ALL
            SELECT source_file, source_url, fetched_at, parser FROM audit_findings UNION ALL
            SELECT source_file, source_url, fetched_at, parser FROM abn
        ) GROUP BY source_file, source_url ORDER BY source_file
    """).fetchall()
    reviews = con.execute("SELECT name_a, name_b, score, answer, model FROM match_reviews ORDER BY score DESC").fetchall()
    run = con.execute("SELECT run_at, mode FROM runs ORDER BY run_at DESC LIMIT 1").fetchone()
    return {"cases": cases, "held": held, "context": context_flags, "sources": sources, "reviews": reviews,
            "run": run, "entities": entities}


def _link(ev: dict, report_dir: Path) -> dict:
    f = ev.get("source_file")
    href = None
    if f:
        target = Path(f) if Path(f).is_absolute() else Path(__file__).resolve().parent.parent / f
        try:
            href = Path(os.path.relpath(target, report_dir)).as_posix()
        except ValueError:
            href = target.as_posix()
        if ev.get("page"):
            href += f"#page={ev['page']}"
    return {**ev, "href": href}


def _abn_facts(con, abn):
    if not abn:
        return None
    row = con.execute("""
        SELECT abn, string_agg(DISTINCT name_raw, '; '), any_value(entity_type), any_value(status),
               any_value(status_from), any_value(state), any_value(postcode)
        FROM abn WHERE abn = ? GROUP BY abn
    """, [abn]).fetchone()
    if not row:
        return None
    keys = ["abn", "names", "entity_type", "status", "status_from", "state", "postcode"]
    return dict(zip(keys, row))


def _matches(con, body, entity) -> list[str]:
    out = []
    for person, role, itype, rtype, rdate in con.execute("""
        SELECT person, role, interest_type, return_type, return_date FROM interests
        WHERE council_id = ? AND entity_id = ? ORDER BY return_date
    """, [body, entity]).fetchall():
        out.append(f"Interest return: {person} ({role}), {itype}, {rtype} return {rdate}")
    for cand, value, edate in con.execute("""
        SELECT candidate, value, election_date FROM candidate_gifts WHERE council_id = ? AND entity_id = ?
    """, [body, entity]).fetchall():
        out.append(f"Candidate gift: ${value:,.0f} to {cand}, election {edate}")
    for rec, value, rdate in con.execute("""
        SELECT recipient_name, value, received_date FROM gifts WHERE council_id = ? AND entity_id = ?
    """, [body, entity]).fetchall():
        out.append(f"Gifts register: ${value:,.0f} to {rec} on {rdate}")
    return out


# ---------------------------------------------------------------- html

def _money(v) -> str:
    return "" if v is None else f"${v:,.0f}"


def _render(ctx: dict, cal: dict, decisions: dict, git_commit: str, mismatches: list[dict]) -> str:
    open_cases = [c for c in ctx["cases"] if c["status"] != "noise"]
    noise = [c for c in ctx["cases"] if c["status"] == "noise"]
    run_at, mode = ctx["run"] or (datetime.now(timezone.utc), "unknown")
    parts = [_HEAD, '<main>', _header(open_cases, mode, run_at)]
    if ctx["context"]:
        parts.append('<section class="context"><h2>Context</h2><ul>' + "".join(
            f"<li><strong>{escape(c['body'])}</strong>: {escape(c['detail'])} Scores for this body are multiplied.</li>"
            for c in ctx["context"]) + "</ul></section>")
    parts.append('<section><h2>Case queue</h2>')
    parts.append("".join(_case(c) for c in open_cases) or '<p class="muted">No cases surfaced.</p>')
    parts.append('</section>')
    if ctx["held"]:
        parts.append('<section><h2>Held: strong flags resting on a fuzzy match</h2>'
                     '<p class="muted">These do not count until a person confirms the name match. Confirm, export, and rerun.</p>')
        for h in ctx["held"]:
            names = json.dumps(h["names"])
            parts.append(f'<div class="held"><span class="chip strong">{escape(h["flag"])}</span> '
                         f'<strong>{escape(h["entity"])}</strong>, {escape(h["body"])}: {escape(h["detail"])} '
                         f"<button type=\"button\" data-confirm='{escape(names)}'>Confirm match</button>"
                         f'{_evidence(h["evidence"])}</div>')
        parts.append('</section>')
    if noise:
        parts.append('<section><h2>Dismissed as noise</h2>' + "".join(_case(c) for c in noise) + '</section>')
    parts.append(_reconciliation(mismatches))
    parts.append(_calibration(cal))
    parts.append(_reviews(ctx["reviews"]))
    parts.append(_footer(run_at, git_commit, ctx["sources"]))
    parts.append('</main>')
    parts.append(f'<script id="initial-decisions" type="application/json">{_json_script(decisions)}</script>')
    parts.append(_SCRIPT)
    parts.append('</body></html>')
    return "\n".join(parts)


def _header(cases, mode, run_at) -> str:
    return f"""
<header>
  <h1>Case queue</h1>
  <p class="lede">{len(cases)} case{'s' if len(cases) != 1 else ''} where two or more independent signals line up, or one strong signal hits.
  Run {escape(str(run_at)[:16])} on {escape(mode)} data.</p>
  <p class="warn">A red flag is a question, not a finding. Names stay inside this tool. What leaves it is a pattern, a question,
  an FOI request or a referral.</p>
  <div class="toolbar"><button type="button" id="export">Export decisions.json</button>
  <span class="muted" id="export-note">Save it as data/review/decisions.json; the next run reads it.</span></div>
</header>"""


def _case(c: dict) -> str:
    chips = "".join(f'<span class="chip {escape(_strength(c, f))}">{escape(f)}</span>' for f in c["flags"])
    key = escape(c["key"])
    hits = "".join(
        f'<div class="hit"><h4><span class="chip {escape(h["strength"])}">{escape(h["flag"])}</span> '
        f'<span class="muted">{escape(h["strength"])}, {h["score"]:g} pt{"s" if h["score"] != 1 else ""}'
        f'{", match " + escape(h["basis"]) if h["basis"] else ""}</span></h4>'
        f'<p>{escape(h["detail"])}</p>{_evidence(h["evidence"])}</div>'
        for h in c["hits"])
    abn = c["abn"]
    abn_html = (f'<dl class="facts"><dt>ABN</dt><dd>{escape(abn["abn"])}</dd><dt>Names</dt><dd>{escape(abn["names"] or "")}</dd>'
                f'<dt>Type</dt><dd>{escape(abn["entity_type"] or "")}</dd><dt>Status</dt><dd>{escape(abn["status"] or "")} from {escape(str(abn["status_from"]))}</dd>'
                f'<dt>Location</dt><dd>{escape(abn["state"] or "")} {escape(abn["postcode"] or "")}</dd></dl>'
                if abn else '<p class="muted">No ABN matched.</p>')
    matches = "".join(f"<li>{escape(m)}</li>" for m in c["matches"])
    links = ", ".join(escape(l) for l in c["links"])
    return f"""
<details class="case" data-key="{key}">
  <summary>
    <span class="rank">{c['rank']}</span>
    <span class="who"><strong>{escape(c['entity'])}</strong><span class="muted">{escape(c['body'])}</span></span>
    <span class="score">{c['score']:g}</span>
    <span class="money">{_money(c['money'])}</span>
    <span class="chips">{chips}</span>
    <span class="summary">{escape(c['summary'])}</span>
    <span class="status" data-status-for="{key}"></span>
  </summary>
  <div class="body">
    <p class="muted small">Summary by {escape(c['summary_model'])}, prompt {escape(c['summary_prompt_id'])}.</p>
    <h3>Evidence</h3>{hits}
    <h3>Entity</h3>{abn_html}
    <p class="muted small">Names linked to this entity: {links}. ASIC extract not fetched.</p>
    <h3>Interest, gift and donor matches</h3>{f'<ul>{matches}</ul>' if matches else '<p class="muted">None.</p>'}
    <h3>Review</h3>
    <div class="actions">
      <button type="button" data-set="noise">Noise</button>
      <button type="button" data-set="keep">Keep</button>
      <button type="button" data-set="refer">Refer</button>
      <label>FOI sent <input type="date" data-foi></label>
      <span class="muted small" data-foi-due></span>
    </div>
    <textarea data-note placeholder="Notes stay in this browser and go into the export."></textarea>
    <details class="foi"><summary>FOI request draft</summary><pre>{escape(c['foi'])}</pre></details>
  </div>
</details>"""


def _strength(c, flag):
    for h in c["hits"]:
        if h["flag"] == flag:
            return h["strength"]
    return ""


def _evidence(rows: list[dict]) -> str:
    if not rows:
        return ""
    body = []
    for r in rows:
        src = escape(Path(r["source_file"] or "").name)
        where = f"p. {r['page']}" if r.get("page") else (f"line {r['line']}" if r.get("line") else "")
        link = f'<a href="{escape(r["href"])}">{src}</a>' if r.get("href") else src
        body.append(
            f"<tr><td>{escape(r.get('date') or '')}</td><td>{escape(r.get('name') or '')}</td>"
            f"<td class='num'>{_money(r.get('amount'))}</td><td>{escape(r.get('detail') or '')}</td>"
            f"<td>{link} {escape(where)}<br><span class='muted small'>{escape(r.get('source_url') or '')}</span></td></tr>")
    return ('<div class="scroll"><table><thead><tr><th>Date</th><th>Name</th><th>Amount</th><th>Detail</th><th>Source</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _reconciliation(mismatches) -> str:
    if not mismatches:
        return ('<section><h2>Reconciliation</h2><p class="muted">No reconciliation tests run in phase 1. '
                'The cross-source tests (annual report against AusTender, Senate Order lists, grants, ABN dates, '
                'related party notes against returns) arrive with their sources.</p></section>')
    rows = "".join(f"<tr><td>{escape(m['test'])}</td><td>{escape(str(m['left']))}</td><td>{escape(str(m['right']))}</td>"
                   f"<td>{escape(m.get('left_source', ''))}<br>{escape(m.get('right_source', ''))}</td><td>{escape(m.get('note', ''))}</td></tr>"
                   for m in mismatches)
    return f'<section><h2>Reconciliation</h2><div class="scroll"><table><thead><tr><th>Test</th><th>One side</th><th>Other side</th><th>Sources</th><th>Note</th></tr></thead><tbody>{rows}</tbody></table></div></section>'


def _calibration(cal) -> str:
    rows = []
    for r in cal["results"]:
        name = r.get("entity") or r.get("body")
        expect = ", ".join(r.get("expect_flags") or r.get("flags") or [])
        cls = "ok" if r["status"] == "found" else ("bad" if r["status"].startswith(("missed", "found,")) else "muted")
        rows.append(f"<tr><td>{escape(r['id'])}</td><td>{escape(str(name))}</td><td>{escape(expect)}</td>"
                    f"<td class='{cls}'>{escape(r['status'])}</td><td class='num'>{r.get('rank') or ''}</td></tr>")
    unexpected = "".join(f"<li>{escape(u['flag'])} on {escape(u['entity_id'])}: {escape(u['detail'])}</li>" for u in cal["unexpected"])
    verdict = '<p class="ok">Calibration passed.</p>' if cal["ok"] else '<p class="bad">Calibration FAILED.</p>'
    extra = f'<p class="bad">Flags on entities outside the calibration list:</p><ul>{unexpected}</ul>' if unexpected else ""
    return (f'<section><h2>Calibration</h2>{verdict}{extra}<div class="scroll"><table><thead><tr><th>Case</th><th>Entity or body</th>'
            f'<th>Flags it should raise</th><th>Status</th><th>Rank in body</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')


def _reviews(reviews) -> str:
    if not reviews:
        return ""
    rows = "".join(f"<tr><td>{escape(a)}</td><td>{escape(b)}</td><td class='num'>{s:.0f}</td><td>{escape(ans)}</td><td>{escape(m)}</td></tr>"
                   for a, b, s, ans, m in reviews)
    return (f'<section><h2>Name pairs for review</h2><p class="muted">Scored between the review and link thresholds; '
            f'left unmatched unless the model says same.</p><div class="scroll"><table><thead><tr><th>Name</th><th>Nearest</th>'
            f'<th>Score</th><th>Answer</th><th>Model</th></tr></thead><tbody>{rows}</tbody></table></div></section>')


def _footer(run_at, git_commit, sources) -> str:
    rows = "".join(f"<li>{escape(f or '')} <span class='muted'>({escape(u or '')}, downloaded {escape(str(t)[:16])}, parser {escape(p or '')})</span></li>"
                   for f, u, t, p in sources)
    return (f'<footer><p>Run {escape(str(run_at)[:19])}. Commit {escape(git_commit)}.</p>'
            f'<details><summary>Sources ({len(sources)})</summary><ul>{rows}</ul></details></footer>')


def _json_script(obj) -> str:
    return json.dumps(obj).replace("</", "<\\/")


_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Screener case queue</title>
<style>
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d2329;--muted:#5f6b76;--line:#d9dde1;--accent:#1f5f8b;
--weak:#e8eef3;--weak-ink:#2b4a63;--medium:#fdf0d9;--medium-ink:#7a4b00;--strong:#fbe1df;--strong-ink:#8a1c12;
--context:#e9e4f5;--context-ink:#46327a;--ok:#1d6b3a;--bad:#a1271b}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15191d;--panel:#1d2329;--ink:#e6e9ec;--muted:#9aa5af;--line:#333c45;
--accent:#7fb8e0;--weak:#24313c;--weak-ink:#b7cde0;--medium:#3a2f17;--medium-ink:#f1cf8f;--strong:#42201d;--strong-ink:#f4b3ab;
--context:#2d2740;--context-ink:#cbbcf2;--ok:#7fd19a;--bad:#f19a8f}}
:root[data-theme="dark"]{--bg:#15191d;--panel:#1d2329;--ink:#e6e9ec;--muted:#9aa5af;--line:#333c45;--accent:#7fb8e0;
--weak:#24313c;--weak-ink:#b7cde0;--medium:#3a2f17;--medium-ink:#f1cf8f;--strong:#42201d;--strong-ink:#f4b3ab;
--context:#2d2740;--context-ink:#cbbcf2;--ok:#7fd19a;--bad:#f19a8f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:1.6rem;margin:.2em 0}h2{font-size:1.2rem;margin:1.6em 0 .6em}h3{font-size:1rem;margin:1.2em 0 .4em}h4{margin:.4em 0;font-size:.95rem}
.lede{margin:.2em 0}.warn{background:var(--medium);color:var(--medium-ink);padding:8px 12px;border-radius:6px}
.muted{color:var(--muted)}.small{font-size:.85em}.ok{color:var(--ok)}.bad{color:var(--bad)}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
button{font:inherit;padding:6px 12px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);cursor:pointer}
button:hover{border-color:var(--accent)}button.on{background:var(--accent);color:var(--panel);border-color:var(--accent)}
.case{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:10px 0}
.case>summary{display:grid;grid-template-columns:2.2em 1fr auto auto;gap:4px 12px;padding:12px;cursor:pointer;list-style:none;align-items:baseline}
.case>summary::-webkit-details-marker{display:none}
.rank{font-weight:700;color:var(--muted)}.who{display:flex;flex-direction:column}.score{font-weight:700;font-size:1.1rem}
.money{font-variant-numeric:tabular-nums;text-align:right}
.chips,.summary,.status{grid-column:2/-1}.summary{color:var(--muted);font-size:.9em}
.status:not(:empty){font-size:.8em;font-weight:600;color:var(--accent)}
.chip{display:inline-block;font-size:.75rem;padding:1px 8px;border-radius:10px;margin:2px 4px 2px 0;background:var(--weak);color:var(--weak-ink)}
.chip.medium{background:var(--medium);color:var(--medium-ink)}.chip.strong{background:var(--strong);color:var(--strong-ink)}
.chip.context{background:var(--context);color:var(--context-ink)}
.body{padding:0 12px 12px;border-top:1px solid var(--line)}
.hit{margin:10px 0}.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.85rem}th,td{text-align:left;padding:4px 6px;border-bottom:1px solid var(--line);vertical-align:top}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}a{color:var(--accent)}
dl.facts{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;margin:0}dt{color:var(--muted)}dd{margin:0}
.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
textarea{width:100%;min-height:4em;font:inherit;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px}
input{font:inherit;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px}
pre{white-space:pre-wrap;background:var(--bg);padding:10px;border-radius:6px;font-size:.85rem}
.held,.context{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px;margin:8px 0}
footer{margin-top:2em;padding-top:1em;border-top:1px solid var(--line);color:var(--muted);font-size:.85rem}
@media (max-width:600px){.case>summary{grid-template-columns:2em 1fr auto}.money{grid-column:2/-1;text-align:left}}
</style></head><body>"""

_SCRIPT = """<script>
(function(){
  var KEY='screener-decisions';
  var initial={};try{initial=JSON.parse(document.getElementById('initial-decisions').textContent)||{};}catch(e){}
  var state={cases:Object.assign({},initial.cases||{}),confirmed_names:(initial.confirmed_names||[]).slice()};
  try{var saved=JSON.parse(localStorage.getItem(KEY)||'null');if(saved){Object.assign(state.cases,saved.cases||{});
    (saved.confirmed_names||[]).forEach(function(n){if(state.confirmed_names.indexOf(n)<0)state.confirmed_names.push(n);});}}catch(e){}
  function save(){try{localStorage.setItem(KEY,JSON.stringify(state));}catch(e){}}
  function rec(k){return state.cases[k]||(state.cases[k]={});}
  function due(sent){var d=new Date(sent);if(isNaN(d))return'';d.setDate(d.getDate()+45);return'Decision due '+d.toISOString().slice(0,10)+' (45 days, FOI Act 1992 (WA))';}
  document.querySelectorAll('.case').forEach(function(el){
    var k=el.getAttribute('data-key'),r=state.cases[k]||{};
    var st=document.querySelector('[data-status-for="'+CSS.escape(k)+'"]');
    function paint(){var s=(state.cases[k]||{}).status;el.querySelectorAll('[data-set]').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-set')===s);});
      if(st)st.textContent=s?('Marked '+s):'';}
    el.querySelectorAll('[data-set]').forEach(function(b){b.addEventListener('click',function(){var x=rec(k),v=b.getAttribute('data-set');
      x.status=(x.status===v)?undefined:v;x.at=new Date().toISOString();save();paint();});});
    var note=el.querySelector('[data-note]');if(note){note.value=r.note||'';note.addEventListener('input',function(){rec(k).note=note.value;save();});}
    var foi=el.querySelector('[data-foi]'),dueEl=el.querySelector('[data-foi-due]');
    if(foi){foi.value=r.foi_sent||'';dueEl.textContent=r.foi_sent?due(r.foi_sent):'';
      foi.addEventListener('change',function(){rec(k).foi_sent=foi.value;dueEl.textContent=foi.value?due(foi.value):'';save();});}
    paint();
  });
  document.querySelectorAll('[data-confirm]').forEach(function(b){b.addEventListener('click',function(){
    var names=[];try{names=JSON.parse(b.getAttribute('data-confirm'));}catch(e){}
    names.forEach(function(n){if(state.confirmed_names.indexOf(n)<0)state.confirmed_names.push(n);});save();b.textContent='Confirmed: export and rerun';b.disabled=true;});});
  document.getElementById('export').addEventListener('click',function(){
    var blob=new Blob([JSON.stringify(state,null,2)],{type:'application/json'});
    var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='decisions.json';document.body.appendChild(a);a.click();
    setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},0);});
})();
</script>"""
