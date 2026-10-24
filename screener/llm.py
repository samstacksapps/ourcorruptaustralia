"""The only module that calls a model (SPEC.md section 2, rule 2).

A model is used in two places only: reading unstructured documents into rows, and writing
the plain-English "why this was flagged" summary for a case, citing the rows. It also
answers same, different or unsure for name pairs in the entity-resolution review band.
It never produces a score. Every output is stored with the model name and the prompt id.

Phase 1 runs offline: summaries are built from the flags' own detail lines (model
"none (template)"), and name pairs are answered unsure, so they stay unmatched. The model
calls arrive in phase 2 with the document parsers.
"""

from __future__ import annotations

PROMPTS = {
    "summary-template-v1": "Offline: join the flags' detail sentences in score order.",
}


def summarise_case(details: list[str]) -> tuple[str, str, str]:
    """Return (summary, model, prompt_id) for a case from its flags' detail lines."""
    text = " ".join(d.rstrip(".") + "." for d in details if d)
    return text, "none (template)", "summary-template-v1"


def same_entity(name_a: str, name_b: str, context: str = "") -> tuple[str, str, str | None]:
    """Answer same, different or unsure for two names. Offline in phase 1: always unsure."""
    return "unsure", "none (offline)", None


def read_document(path, kind: str):
    """Read an unstructured document (minutes, register, return) into rows. Phase 2."""
    raise NotImplementedError("model document reading arrives in phase 2; phase 1 makes no model calls")
