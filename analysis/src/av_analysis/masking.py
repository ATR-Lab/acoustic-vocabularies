"""Field deny lists that keep outcomes and conditions apart (analysis plan section 8).

Rules (``docs/architecture.md``, "Masking"):

* ``masked`` policy - reconciliation reports and reconciled tables (#33) and everything
  the integrity dashboard reads or renders (#35): no outcome, response, response-time,
  hidden-answer, rating, condition (method, role, scaffold) or personal field. Staff can
  run reconciliation and read the dashboard without unmasking.
* ``derived`` policy - the derived trial and endpoint tables (#33 -> #34): outcomes are
  allowed, condition labels and personal fields are not. Conditions are joined only in
  the analysis pipeline (#34), from the allocation key, at unmasking.

A field is forbidden when its exact name is a template column of a forbidden class, when
one of its ``_``-separated tokens is a deny token (``exact_correct`` -> ``correct``), or
when it contains a deny sequence (``response_time``). The dashboard additionally renders
only allow-listed columns (#35); this module is the defence-in-depth deny list.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Final, Literal

from .templates import columns_of_class

Policy = Literal["masked", "derived"]
POLICIES: Final[tuple[Policy, ...]] = ("masked", "derived")

# Exact template columns per reason.
_OUTCOME: Final = columns_of_class("outcome")
_RESPONSE: Final = columns_of_class("response")
_HIDDEN: Final = columns_of_class("hidden_answer")
_CONDITION: Final = columns_of_class("condition")
_FREE_TEXT: Final = columns_of_class("free_text")

# Tokens (exact ``_``-separated parts of a field name) per reason.
OUTCOME_TOKENS: Final = frozenset({"correct", "accuracy", "score", "scores", "rt", "latency"})
RATING_TOKENS: Final = frozenset(
    {"rating", "ratings", "ownership", "pleasantness", "preference", "influence", "demand"}
)
CONDITION_TOKENS: Final = frozenset({"method", "methods", "role", "roles", "scaffold", "designer"})
PERSONAL_TOKENS: Final = frozenset(
    {"email", "phone", "contact", "address", "birth", "dob", "postcode", "surname"}
)
# Person names (a bare ``name`` key, e.g. a check name, is allowed).
PERSONAL_SEQUENCES: Final = (
    "first_name",
    "last_name",
    "full_name",
    "given_name",
    "family_name",
    "participant_name",
    "person_name",
)
# Substrings that are forbidden wherever they appear.
OUTCOME_SEQUENCES: Final = ("response_time", "response_action", "response_target", "commit_ms")
RESPONSE_SEQUENCES: Final = ("response_code", "commit_mono")
HIDDEN_SEQUENCES: Final = ("target_action", "target_referent", "semantic_action", "intended")
CONDITION_SEQUENCES: Final = ("sq_arm", "yoked_source", "structured", "dictionary_family")
# Exact names that reveal a condition (schedule ``presentation`` is structured/dictionary).
CONDITION_NAMES: Final = frozenset({"presentation", "arm"})


def _tokens(field: str) -> set[str]:
    return {t for t in field.lower().replace("-", "_").split("_") if t}


def forbidden_reason(field: str, policy: Policy) -> str | None:
    """Why ``field`` may not appear under ``policy`` (``outcome``, ``response``,
    ``hidden_answer``, ``rating``, ``condition``, ``personal``), or None if allowed."""
    name = field.lower()
    tokens = _tokens(field)
    if tokens & PERSONAL_TOKENS or any(s in name for s in PERSONAL_SEQUENCES):
        return "personal"
    if name in _CONDITION or name in CONDITION_NAMES or tokens & CONDITION_TOKENS:
        return "condition"
    if any(s in name for s in CONDITION_SEQUENCES):
        return "condition"
    if policy == "derived":
        return None
    if name in _OUTCOME or tokens & OUTCOME_TOKENS or any(s in name for s in OUTCOME_SEQUENCES):
        return "outcome"
    if name in _RESPONSE or any(s in name for s in RESPONSE_SEQUENCES):
        return "response"
    if name in _HIDDEN or any(s in name for s in HIDDEN_SEQUENCES):
        return "hidden_answer"
    if tokens & RATING_TOKENS:
        return "rating"
    return None


def forbidden_columns(columns: Iterable[str], policy: Policy) -> dict[str, str]:
    """``{column: reason}`` for every forbidden column (empty when all are allowed)."""
    out: dict[str, str] = {}
    for c in columns:
        reason = forbidden_reason(c, policy)
        if reason is not None:
            out[c] = reason
    return out


def forbidden_keys(obj: object, policy: Policy, path: str = "$") -> dict[str, str]:
    """``{json_path: reason}`` for every forbidden object key in a JSON value, recursively."""
    out: dict[str, str] = {}
    if isinstance(obj, Mapping):
        for key in sorted(obj, key=str):
            sub = f"{path}.{key}"
            reason = forbidden_reason(str(key), policy)
            if reason is not None:
                out[sub] = reason
            out.update(forbidden_keys(obj[key], policy, sub))
    elif isinstance(obj, list | tuple):
        for i, value in enumerate(obj):
            out.update(forbidden_keys(value, policy, f"{path}[{i}]"))
    return out


def free_text_columns() -> frozenset[str]:
    """Template columns holding operator free text (never rendered by the dashboard)."""
    return _FREE_TEXT
