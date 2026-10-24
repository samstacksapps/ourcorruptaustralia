"""Name cleaning and entity clustering (SPEC.md section 6).

1. Every name is normalised the same way; the raw form is kept beside it.
2. Names that match an ABN register name exactly take that ABN, and names sharing an ABN
   are one entity. That is exact and final.
3. Other names join an entity when rapidfuzz token_set_ratio is at or above the link
   threshold (method fuzzy).
4. Pairs in the review band go to the model as same, different or unsure (method model).
   Unsure stays unmatched. Offline, every answer is unsure.
5. Names a person confirmed in the report are marked method confirmed.

Strong flags check the link method of both sides (see db.py strong_basis), so a fuzzy or
model link never carries a strong flag on its own.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz, process

_PHRASES = [
    r"\bTHE TRUSTEE FOR\b",
    r"\bTRUSTEE FOR\b",
    r"\bTRADING AS\b",
    r"\bT/A\b",
    r"\bA\.?T\.?F\.?(?=\s|$)",
    r"\bAS TRUSTEE FOR\b",
]
_DROP_TOKENS = {"PTY", "LTD", "LIMITED", "PROPRIETARY", "THE", "ATF"}
_TRUST_SUFFIX = re.compile(r"(\s+(FAMILY|UNIT|DISCRETIONARY|SUPERANNUATION|SUPER|HYBRID))?\s+TRUST$")
_PERSON_TITLES = {"CR", "CLR", "COUNCILLOR", "MR", "MRS", "MS", "MISS", "DR", "PRESIDENT", "MAYOR", "DEPUTY", "SHIRE", "HON"}


def normalise_name(raw: str | None) -> str:
    """Upper case, strip punctuation, drop company and trust words, collapse whitespace."""
    if raw is None:
        return ""
    s = str(raw).upper().replace("&", " AND ")
    for phrase in _PHRASES:
        s = re.sub(phrase, " ", s)
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = " ".join(t for t in s.split() if t not in _DROP_TOKENS)
    s = _TRUST_SUFFIX.sub("", s)
    return s.strip()


def person_key(raw: str | None) -> str:
    """Surname key for matching a person across minutes and returns ("Cr T Hollis" -> HOLLIS)."""
    if not raw:
        return ""
    tokens = [t for t in re.sub(r"[^A-Z ]+", " ", str(raw).upper()).split() if t not in _PERSON_TITLES]
    return tokens[-1] if tokens else ""


def entity_id_for_abn(abn: str) -> str:
    return f"ABN-{abn}"


def entity_id_for_name(name_norm: str) -> str:
    return "NAME-" + hashlib.sha1(name_norm.encode("utf-8")).hexdigest()[:10]


@dataclass
class Link:
    name_norm: str
    entity_id: str
    method: str  # exact, abn, fuzzy, model or confirmed
    score: float


@dataclass
class Resolution:
    links: dict[str, Link]
    entities: dict[str, dict]  # entity_id -> {canonical_name, abn, acn}
    reviews: list[dict]  # pairs sent to the model and its answer


def resolve(
    names: list[str],
    abn_names: list[tuple[str, str]],
    *,
    fuzzy_link_min: float,
    model_review_min: float,
    fuzzy_min_tokens: int,
    same_entity=None,
    confirmed: set[str] | None = None,
) -> Resolution:
    """Cluster normalised names into entities.

    names: every normalised name seen in the source tables.
    abn_names: (abn, name_norm) pairs from the ABN register.
    same_entity: callable(name_a, name_b) -> (answer, model, prompt_id) for the review band.
    """
    confirmed = confirmed or set()
    names = sorted({n for n in names if n})
    abns_by_name: dict[str, set[str]] = defaultdict(set)
    for abn, name in abn_names:
        if name:
            abns_by_name[name].add(abn)

    links: dict[str, Link] = {}
    entities: dict[str, dict] = {}

    # Exact match to the ABN register; a name with two ABNs is ambiguous and left for fuzzy.
    for name in names:
        abns = abns_by_name.get(name, set())
        if len(abns) == 1:
            abn = next(iter(abns))
            eid = entity_id_for_abn(abn)
            entities.setdefault(eid, {"canonical_name": name, "abn": abn, "acn": None})
            links[name] = Link(name, eid, "abn", 100.0)

    # Candidates a remaining name can fuzzy-join: observed names already placed, and
    # register names (which bring their ABN).
    cand_names: list[str] = []
    cand_entity: list[str] = []
    cand_abn: list[str | None] = []
    for name, link in links.items():
        cand_names.append(name)
        cand_entity.append(link.entity_id)
        cand_abn.append(entities[link.entity_id]["abn"])
    for name, abns in abns_by_name.items():
        if len(abns) == 1 and name not in links:
            abn = next(iter(abns))
            cand_names.append(name)
            cand_entity.append(entity_id_for_abn(abn))
            cand_abn.append(abn)

    reviews: list[dict] = []
    for name in names:
        if name in links:
            continue
        best = _best_match(name, cand_names, model_review_min, fuzzy_min_tokens)
        placed = False
        if best is not None:
            idx, score = best
            eid, abn = cand_entity[idx], cand_abn[idx]
            if score >= fuzzy_link_min:
                method = "fuzzy"
            else:
                answer, model, prompt_id = (same_entity or _offline_same_entity)(name, cand_names[idx])
                reviews.append({
                    "name_a": name, "name_b": cand_names[idx], "score": score,
                    "answer": answer, "model": model, "prompt_id": prompt_id,
                })
                method = "model" if answer == "same" else None
            if method:
                if eid not in entities:
                    entities[eid] = {"canonical_name": cand_names[idx], "abn": abn, "acn": None}
                if name in confirmed:
                    method = "confirmed"
                links[name] = Link(name, eid, method, float(score))
                placed = True
        if not placed:
            eid = entity_id_for_name(name)
            entities[eid] = {"canonical_name": name, "abn": None, "acn": None}
            links[name] = Link(name, eid, "exact", 100.0)
        cand_names.append(name)
        cand_entity.append(links[name].entity_id)
        cand_abn.append(entities[links[name].entity_id]["abn"])

    return Resolution(links=links, entities=entities, reviews=reviews)


def _best_match(name, candidates, cutoff, min_tokens):
    if not candidates or len(name.split()) < min_tokens:
        return None
    pool = [(i, c) for i, c in enumerate(candidates) if len(c.split()) >= min_tokens and c != name]
    if not pool:
        return None
    hit = process.extractOne(
        name, [c for _, c in pool], scorer=fuzz.token_set_ratio, score_cutoff=cutoff
    )
    if hit is None:
        return None
    _, score, pos = hit
    return pool[pos][0], score


def _offline_same_entity(name_a, name_b):
    return "unsure", "none (offline)", None
