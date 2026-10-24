# Our Corrupt Australia — build brief

Repository: https://github.com/samstacksapps/ourcorruptaustralia

This file is the brief for Claude Code. Read all of it before writing any code. It records what the tool is, the rules it must follow, the data it uses, the tests it runs, and the order to build in.

## 1. What this is

A screener that pulls Australia's public money records into one database and tests them against each other, so that the small number of cases that do not add up can be found, checked and referred. It is a red-flag screener, not a fraud detector. Fraud is rare, so the output has to be a short, ranked queue of cases where two or more independent signals line up, with the evidence rows attached, so a person can check each one in a few minutes.

It works in two directions:

1. From the money outwards: contracts, grants and payments, tested for the patterns that have shown up in proven Australian cases.
2. From the people inwards: the few hundred people who can steer public money, and whether the companies, trusts and addresses they have declared, or the companies their former colleagues now run, turn up as payees.

Two layers of data, built separately, same database:

- Layer A, federal: contracts, grants, donations, lobbyists, annual report tables, interests registers.
- Layer B, Western Australian local government: the monthly list of every account paid, tender registers, gift registers, interest returns, disclosures at meetings, delegation registers. Nobody reads these systematically. This is where the tool is first.

## 2. Rules that keep it honest

These are not optional and every part of the code has to respect them.

1. Scores are deterministic. Every red flag is a SQL view or a plain Python rule with a stated threshold. A language model never produces a score.
2. The model is used in two places only: reading unstructured documents (PDF minutes, registers, returns, annual report tables) into rows, and writing the plain-English "why this was flagged" summary for a case, citing the rows. Both outputs are stored with the model name and the prompt used.
3. No evidence, no flag. Every flag row links to the source rows and every source row links to the file and page it came from, with the URL it was downloaded from and the download date.
4. Baselines are local. A limited tender is routine at Defence and strange at a small agency. A $9,000 payment is routine at a city and strange at a shire of 800 people. Flags are scored relative to the agency's or council's own distribution, not a global one.
5. Two signals or nothing. A case surfaces when two or more unrelated flags hit the same entity, or when a single "strong" flag hits (a declared interest, a gift from a payee, an undeclared conflict at a vote).
6. Names stay inside the tool. The report is for the person running it. What leaves the tool is a pattern, a question, an FOI request or a referral to the NACC, the CCC WA or the Department of Local Government. Australian defamation law is unforgiving and a red flag is a question, not a finding.
7. Everything is reproducible. Scoring views and thresholds live in version control, so any run can be re-created from the source files.
8. Keep a calibration table of known cases and check every run still finds them (section 9).

## 3. Architecture

- Python 3.11 or later. One `pip install -r requirements.txt`.
- DuckDB as the single database file, `data/screener.duckdb`. It reads CSV and Parquet directly and runs the SQL fast.
- Libraries: `duckdb`, `requests`, `beautifulsoup4`, `lxml`, `pdfplumber`, `pypdf`, `rapidfuzz`, `pandas`, `pyyaml`, `anthropic` (optional, only for the document-reading step), `reportlab` (fixtures only).
- Layout:

```
ourcorruptaustralia/
  SPEC.md                    this file
  README.md                  how to run it
  requirements.txt
  config/
    councils.csv             registry of WA local governments: name, region, website, minutes URL, registers URLs
    agencies.csv             federal agencies with annual report and Senate Order list URLs
    thresholds.yml           every number a flag depends on, with its source
    calibration.yml          the known cases the tool must find
  screener/
    fetch/                   one module per source; download only, no parsing
    parse/                   one module per document type; file in, rows out
    normalise.py             name cleaning and entity clustering
    db.py                    schema, loaders, provenance
    flags/                   one .sql file per flag, plus flags.yml with weights
    reconcile/               the "does not add up" cross-source tests
    score.py                 runs flags, builds cases and evidence
    report.py                self-contained HTML case queue
    foi.py                   drafts the FOI request for a case
    llm.py                   the only module that calls a model
    cli.py                   python -m screener fetch|parse|build|score|report|run
  fixtures/
    make_fixture.py          synthetic council with planted anomalies (section 8)
  tests/                     pytest: every flag has a test that plants the pattern and checks it fires, and a test that checks it does not fire on clean data
  .github/workflows/
    screen.yml               weekly run; commits parsed CSVs and the report, never raw PDFs
  data/                      git-ignored except data/parsed and data/report
    raw/<source>/<date>/     downloaded files, untouched
    parsed/<source>/         CSV or Parquet rows with provenance columns
    snapshots/               daily copies for the diff tests
    report/index.html
```

- Every parsed row carries: `source_url`, `source_file`, `page`, `fetched_at`, `parser`, `parser_version`, and for model-read rows `model`, `prompt_id`.
- Fetch and parse are separate steps so a parser can be rerun on files already downloaded.
- The scheduled run is GitHub Actions, weekly for the full run and daily for the snapshot diffs. GitHub's servers can reach every source. Raw PDFs are not committed; parsed rows and the report are.

## 4. Data sources

### Layer B: WA local government (build first)

There are 137 local governments. Seed `config/councils.csv` from the Department of Local Government, Sport and Cultural Industries list and verify each website on the first run. Most council websites run on a handful of content systems, so a generic crawler covers most of them: from the minutes/agenda index page, collect PDF links with dates; from a "registers" or "governance" page, collect the gifts, tenders and delegations pages. Keep a per-council override file for the ones the generic crawler misses.

| Record | Where it is | What it gives |
|---|---|---|
| List of accounts paid | Monthly in council minutes or agenda attachments, as a table: date, cheque or EFT number, payee, description, amount | Every payment the council makes. The core table. |
| Tenders register | Council website or on request; many publish it | Tender number, description, close date, names of all tenderers, winner, contract amount |
| Gifts register | Must be published on the council website; gifts of $300 or more to council members and the CEO, disclosed within ten days | Giver, recipient, value, date, description |
| Financial interest returns | Annual and primary returns of council members, the CEO and designated employees; published on the website with personal details removed | Employers, businesses, directorships, trusts, land, debts, gifts |
| Disclosures of interest | In the minutes, item by item: "Cr X declared a financial interest in item 10.2" | Who declared what on which item |
| Delegations register | Council website | Which officer can approve what, up to what amount |
| Related party note | Audited annual financial statements, "Related party transactions" | Transactions with entities connected to councillors and key management, as the council itself states them |
| Candidate gift disclosures | Published by each council after an election | Who funded council campaigns |
| Compliance Audit Return | Adopted in minutes each year | Self-reported tender and purchasing compliance |
| Auditor General local government audit results | audit.wa.gov.au, annual | Councils named for control weaknesses; where to look first |

### Layer A: federal

| Source | Access | What it gives |
|---|---|---|
| AusTender | Open Contracting API at api.tenders.gov.au/ocds, no key; also CSV export per search | Every contract of $10,000 or more: agency, supplier ABN, value, dates, procurement method, limited-tender reason, amendments, confidentiality flags |
| ContractDelta AU | github.com/slicedearth/contract-delta-au | Already records how AusTender notices change after publication; use its change data rather than rebuilding the differ |
| OPAX | opax.com.au exports (for example /graph/money.json) and code at github.com/jaketracey/opax (AGPL-3.0: use its data, be careful about copying its code into a differently licensed repo) | Donations 1998-99 onwards, contracts, grants, declared interests, lobbyists, already joined by entity |
| GrantConnect | grants.gov.au CSV export | Every grant: recipient ABN, value, program, selection process, postcode |
| AEC Transparency Register | transparency.aec.gov.au CSV | Donor and party returns, associated entities, third parties |
| Register of Lobbyists | lobbyists.ag.gov.au | Firms, staff with former-government flag, clients |
| Transparency Portal | transparency.gov.au | Annual report tables as data: key management personnel remuneration with termination benefits, consultancy and non-consultancy contracts, legal expenditure |
| Senate Order lists | Each agency's website | Contracts of $100,000 or more, twice a year, with confidentiality flags |
| directory.gov.au | Web | Every board member and statutory office holder |
| Registers of interests | aph.gov.au, PDFs, some handwritten | Shares, property, directorships, gifts, spouse interests |
| IPEA | ipea.gov.au quarterly | Parliamentarians' travel and office expenses |
| ANAO, NACC, Ombudsman, APSC | Web | Reports and findings; the ANAO financial statements audit names control weaknesses by agency |
| FOI disclosure logs and Right to Know | Each agency; righttoknow.org.au | Documents already released |

### Shared

| Source | Access | What it gives |
|---|---|---|
| ABN bulk extract | data.gov.au, XML, several gigabytes | ABN, names, entity type, status and status-from date, state and postcode. Stream it once into Parquet and keep only those columns |
| ABN Lookup web service | Free GUID registration | Name search when the bulk extract does not match |
| ASIC company extracts | Paid, a few dollars to about $20 each | Current and former directors, shareholders, registered office. Manual, only for shortlisted entities |
| ASIC banned and disqualified register, insolvency notices | Free | Directors and companies to match |

## 5. Schema

All tables keep the provenance columns from section 3. Names are stored raw and normalised; `entity_id` comes from section 6.

```
councils(council_id, name, region, website, minutes_url, population, notes)
payments(council_id, paid_date, ref, payee_raw, payee_norm, entity_id, description, amount, approver, fy)
tenders(council_id, tender_ref, description, closed_date, awarded_date, tenderer_raw, tenderer_norm, entity_id, is_winner, contract_amount)
gifts(council_id, recipient_name, recipient_role, giver_raw, giver_norm, entity_id, value, received_date, description)
interests(council_id, person, role, interest_type, entity_raw, entity_norm, entity_id, detail, return_type, return_date)
disclosures(council_id, meeting_date, item_ref, item_title, person, interest_type)
items(council_id, meeting_date, item_ref, item_title, entities_mentioned[])
delegations(council_id, role, limit_amount, source_date)
related_parties(council_id, fy, counterparty_raw, entity_id, amount, nature)
candidate_gifts(council_id, election_date, candidate, giver_raw, entity_id, value)

agencies(agency_id, name, portfolio, annual_report_url, senate_order_url)
contracts(cn_id, agency_id, supplier_raw, supplier_abn, entity_id, value_original, value_current, start_date, end_date, published_date, procurement_method, limited_tender_reason, confidentiality_contract, confidentiality_outputs, category, description, amendment_of)
contract_changes(cn_id, changed_at, field, old_value, new_value)         from ContractDelta
grants(ga_id, agency_id, recipient_raw, recipient_abn, entity_id, value, program, selection_process, postcode, electorate, awarded_date)
donations(fy, donor_raw, entity_id, recipient_party, amount, return_type)
lobbyists(firm, person, former_government, client_raw, entity_id, registered_from, registered_to)
annual_report_tables(agency_id, fy, table_name, row_label, column_label, value)
kmp_remuneration(agency_id, fy, person, position, total, termination_benefits)
senate_order(agency_id, period, contract_ref, supplier_raw, entity_id, value, confidentiality)
people(person_id, name, role, body, from_date, to_date)                   MPs, senators, agency heads, KMP, board members, councillors, CEOs
declared_entities(person_id, entity_raw, entity_id, relationship, source)

abn(abn, name_norm, entity_type, status, status_from, state, postcode)
entities(entity_id, canonical_name, abn, acn)
entity_links(name_norm, entity_id, method, score)                         method is exact, abn, fuzzy or model
snapshots(source, captured_at, record_id, content_hash)
```

## 6. Entity resolution

1. Normalise every name the same way: upper case, strip punctuation, drop PTY, LTD, LIMITED, PROPRIETARY, THE, TRADING AS, T/A, ATF, trust suffixes, collapse whitespace. Keep the raw form.
2. Match on ABN wherever both sides have one. That is exact and final.
3. Otherwise cluster normalised names with rapidfuzz `token_set_ratio` at 92 or more into one `entity_id`. Store every link with its method and score.
4. Anything between 80 and 92 goes to the model with both names and any address or ABN context, and it answers same, different or unsure. Store the answer as method `model`. Unsure stays unmatched.
5. Never let a fuzzy or model match carry a strong flag on its own. A strong flag needs an ABN or exact match, or a fuzzy match that a person has confirmed in the report.

## 7. Red-flag tests

Each flag is one SQL file in `screener/flags/`, returning `(entity_id, body_id, flag, score, evidence_json)`. Weights and thresholds are in `flags.yml` and `thresholds.yml`, never hard-coded. Every flag has a pytest that plants the pattern in the fixture and checks it fires, and a second test on clean data that checks it does not.

### Layer B: council flags

| Flag | Rule | Strength |
|---|---|---|
| over_threshold_no_tender | Payee's total in a financial year exceeds the tender threshold ($250,000) and the payee is not a winner in the tenders register that year or the year before. Exempt WALGA preferred supplier and State contract purchases where the description says so | medium |
| just_under_limit | Three or more payments to one payee within twelve months each between 90 and 100 per cent of an approval limit from the delegations register, or of the tender threshold | medium |
| repeat_round_amount | Same payee, same amount to the nearest $100, four or more times in twelve months, where the description does not say lease, rent, licence, insurance, loan, subscription or retainer | weak |
| new_abn_payee | Payee matched to an ABN whose status-from date is within twelve months before the first payment, and payments total more than $20,000 | medium |
| unmatched_payee | Payee with more than $20,000 in a year that matches no ABN and no tenderer | weak |
| gift_from_payee | Gift giver is a payee or tenderer of the same council within twelve months either side of the gift | strong |
| interest_matches_payee | An entity declared in a person's interest return is a payee, tenderer, grant recipient or related party of that person's council | strong |
| undeclared_at_meeting | An agenda item mentions an entity that matches a councillor's declared interest, and no disclosure by that councillor is recorded for that item | strong |
| cover_bidding | A winner with three or more tenders where the same set of two or more losers appears every time | medium |
| post_award_overrun | Payments to a tender winner in the 24 months after award exceed 130 per cent of the contract amount, with no later tender or variation recorded | medium |
| repeat_winner | A tenderer wins 40 per cent or more of a council's tenders in a period with at least four tenders | weak |
| related_party_not_declared | A counterparty in the related party note matches no entity in any interest return of that council | medium |
| campaign_funder_wins | A candidate gift giver wins a tender or receives a grant from that council within two years of the election, or has a planning matter decided in that period | medium |
| oag_control_weakness | The Auditor General named the council for purchasing, payment or payroll control weaknesses in the last two reports | context, multiplies other flags by 1.25 |

### Layer A: federal flags

| Flag | Rule | Strength |
|---|---|---|
| new_supplier | First contract within twelve months of ABN status-from date | medium |
| contract_splitting | Same agency, same supplier, three or more contracts each under the open-tender threshold ($80,000 for non-corporate entities) within 90 days | medium |
| limited_tender_concentration | Supplier's share of an agency's limited-tender spend is more than three standard deviations above that agency's supplier mean | medium |
| amendment_creep | Current value more than 150 per cent of original, or three or more amendments | medium |
| confidentiality | Contract or outputs confidentiality flag set, scored relative to the agency's rate | weak |
| late_publication | Published more than 42 days after start date | weak |
| just_under_threshold | Value between $9,000 and $9,999 or between $72,000 and $79,999, three or more times per agency-supplier pair | medium |
| shared_address | Two or more unrelated suppliers to one agency share an address or phone number | medium |
| vague_description | Large contract with a generic description or a category that does not match the description; keyword rules first, model classification second | weak |
| donor_wins | Donor, associated entity or lobbyist client receives a contract or grant within twelve months after a donation or registration change | medium |
| declared_entity_wins | An entity declared in a parliamentarian's or official's register of interests is a supplier or grant recipient | strong |
| revolving_door | A director of a supplier was a staff member or office holder of the awarding agency in the previous three years | strong |
| grants_marginal_seats | Grant program where the share going to seats under a 5 per cent margin is more than two standard deviations above the program's mean | medium, program level |
| payout_outlier | Termination benefit for key management personnel above the 99th percentile across all agencies for that year | medium |
| in_house_spend | Annual report shows asset or manufacturing spend with no AusTender counterpart, and a question on notice or ANAO finding exists | context |

### Scoring

- Weak = 1, medium = 2, strong = 4. Context flags multiply.
- A case is `(body_id, entity_id)` or `(body_id, person_id)`.
- Surface a case when its distinct flags number two or more, or any strong flag hits. Rank by total score, then by money involved.
- Every case stores the evidence rows and the file references that produced each flag.

## 8. Reconciliation tests ("does not add up")

Each one in `screener/reconcile/`, output as a line with both sides and both source links.

1. Agency annual report consultancy and non-consultancy totals versus AusTender totals for the same financial year.
2. Senate Order list versus AusTender: contracts of $100,000 or more missing from either side.
3. Grants in budget papers and ministerial announcements versus GrantConnect records and values.
4. Payment to a supplier dated before its ABN status-from date, or after the ABN was cancelled.
5. Gift recorded from a supplier to an agency or council that awarded that supplier a contract in the same year.
6. Related party note counterparties versus interest returns (council) or registers of interests (federal).
7. Council accounts-paid payee in no tender register, no WALGA or State contract, and no ABN.
8. Daily snapshot diffs: a record edited or deleted after publication on AusTender, a gifts register or an FOI log. Record what changed and when.

## 9. Calibration

`config/calibration.yml` lists known cases. A run fails its own check if a known case does not score near the top of its body and year.

- Paladin Holdings, Department of Home Affairs, 2017-18: limited tender, large value, amendments. In AusTender.
- Eastern Australia Agriculture water purchase, Department of Agriculture, 2017: limited tender, high price. In AusTender.
- Community Sport Infrastructure grants, 2018-19: grants to marginal seats; ANAO report 2020. In GrantConnect.
- Shire of Dowerin, 2013 to 2015: CEO fraud of about $600,000 through false payments; CCC and court records. If the minutes of that period are still online, the accounts-paid pattern is the test for the payee flags.
- City of Perth: the 2020 inquiry report describes governance failures that the disclosure and gift flags should see.
- Department of Communities (Paul Whyte), 2008 to 2019: state level, not council, but the invoice pattern (approver-controlled companies, work never done) is the template for `new_abn_payee`, `repeat_round_amount` and `unmatched_payee`.
- Department of Parliamentary Services furniture and exit payment, 2021 to 2025: the in-house build never appears on AusTender. Tests `in_house_spend` and `payout_outlier` against the Transparency Portal tables.

Add every new confirmed case to this list.

## 10. Fixture

`fixtures/make_fixture.py` builds a synthetic council ("Shire of Example", population 4,000) with twelve months of records and these planted cases:

1. A sole trader with an ABN registered two months before its first payment, paid $9,800 each month, no tender, description "maintenance services".
2. A civil contractor paid $410,000 across nine invoices in a year, no tender.
3. A landscaping company that gives a $450 gift to a councillor and wins the landscaping tender that year against the same two losers it beat the previous two times.
4. A councillor whose annual return lists a directorship in a consultancy that is paid $45,000, with no disclosure recorded at the meeting that approved the engagement.
5. A tender awarded at $220,000 with $340,000 paid to the winner in the following eighteen months.
6. A related party note naming a company that appears in no interest return.
7. Clean records for forty ordinary suppliers, including rent, insurance and WALGA preferred suppliers, which must not fire.

The fixture also writes one accounts-paid PDF in a typical council layout so the parser is tested end to end.

## 11. Report

`screener/report.py` writes one self-contained HTML file. No framework, no build step, works on a phone.

- Top: the ranked case queue with body, entity, score, flags as chips, money involved, and a one-line model summary.
- Each case expands to: the evidence rows per flag with source links and page numbers; the entity's ABN and ASIC facts if fetched; any donor, lobbyist or interest match; a notes box (localStorage, wrapped in try/catch); buttons to mark noise, keep or refer, exported as a JSON file the next run reads so dismissed noise stays dismissed; the FOI draft from `foi.py`.
- A reconciliation section listing every mismatch from section 8.
- A calibration section showing the known cases and where they ranked.
- A footer with the run date, source download dates and the git commit.

`foi.py` drafts a request for: the procurement plan or purchase requisition, the quotes or evaluation report, the approval record with the delegate's name, variations, and the conflict-of-interest declarations, addressed to the body's FOI officer under the Freedom of Information Act 1992 (WA) or the Freedom of Information Act 1982 (Cth). The person sends it; the tool records the date and the 30-day (federal) or 45-day (WA) clock.

## 12. Build order

Each phase ends with a run that produces a report and passes its tests.

1. Skeleton, schema, normaliser, the fixture, the fourteen council flags, scoring, the HTML report. All on the fixture. Every planted case found, no clean supplier flagged.
2. Council fetch and parse for five metropolitan councils (pick ones named in the latest Auditor General local government results). Accounts-paid parser with pdfplumber first and model fallback. Gifts and tenders registers. First real report.
3. ABN bulk extract streamed to Parquet; payee matching; `new_abn_payee` and `unmatched_payee` live.
4. Interest returns, disclosures and delegations parsed; the three strong council flags live.
5. All 137 councils on the weekly schedule, per-council overrides for the crawler.
6. Layer A: AusTender via the OCDS API, ContractDelta change data, GrantConnect, AEC, lobbyists; the federal flags.
7. Transparency Portal tables, Senate Order lists, registers of interests, directory.gov.au; reconciliation tests; `declared_entity_wins` and `revolving_door`.
8. Daily snapshot diffs. FOI tracker. Calibration gate in the workflow.

## 13. First task for Claude Code

Build phase 1. Do not fetch anything from the internet in phase 1. When it is done, running

```
python fixtures/make_fixture.py
python -m screener run --fixture
```

must produce `data/report/index.html` showing the six planted cases ranked at the top, the clean suppliers absent, and `pytest` passing. Then commit with a message that lists the flags implemented and stop for review.
