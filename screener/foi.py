"""Draft the freedom of information request for a case (SPEC.md section 11).

The person running the tool sends it; the tool records the date sent and the statutory
clock: 45 days under the Freedom of Information Act 1992 (WA), 30 days under the Freedom of
Information Act 1982 (Cth). A red flag is a question, so the request asks for documents
and states no conclusion.
"""

from __future__ import annotations

from datetime import date, timedelta

ACTS = {
    "council": ("Freedom of Information Act 1992 (WA)", 45),
    "agency": ("Freedom of Information Act 1982 (Cth)", 30),
}


def date_range(first_payment: date | None, last_payment: date | None,
               first_item: date | None) -> tuple[date | None, date | None]:
    """The period the request covers.

    It starts 12 months before the first payment, so the procurement and approval records
    that came before it are included, or at the first agenda item naming the entity if that
    is earlier. It ends at the last payment (or the first item when nothing was paid).
    """
    starts = [d for d in (first_payment and _year_before(first_payment), first_item) if d]
    start = min(starts) if starts else None
    end = last_payment or first_item
    return start, end


def _year_before(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year - 1, day=28)


def draft(body_name: str, body_type: str, entity_name: str, first_payment: date | None,
          last_payment: date | None, first_item: date | None = None, refs: list[str] | None = None) -> str:
    act, days = ACTS[body_type]
    start, end = date_range(first_payment, last_payment, first_item)
    period = (f"between {_long(start)} and {_long(end)}" if start and end
              else "in the last two financial years")
    refs_line = f" including {', '.join(refs)}" if refs else ""
    return f"""To the FOI Coordinator
{body_name}

Application for access to documents under the {act}

I apply for access to the following documents held by {body_name} relating to the
procurement of goods or services from, and payments made to, {entity_name} {period}{refs_line}:

1. the procurement plan or purchase requisition for each engagement;
2. the quotes received or the tender evaluation report;
3. the record of approval of each purchase or payment, showing the name and position of
   the delegate who approved it;
4. any variation to the contract or purchase order;
5. any declaration of a conflict of interest, or of a financial or impartiality interest,
   made by an elected member or employee in connection with these engagements.

I am happy to receive the documents electronically and to discuss narrowing the scope if
that would help. I understand the agency must deal with this application within {days} days
of receiving it.

Name:
Address for correspondence:
Date:
"""


def _long(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def due_date(sent: date, body_type: str) -> date:
    return sent + timedelta(days=ACTS[body_type][1])
