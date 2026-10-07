"""Masking scan: find Study A method strings in text meant for masked staff or learners.

Study A protocol section 2: the session experimenter never receives generation labels, and
the allocation key is held separately from learner-facing files. ``find_method_strings``
is the automated check used on every learner-facing allocation file and every value the
reveal API returns. It is deliberately conservative:

* method codes ``A1``, ``A2``, ``A3`` and anonymous designer IDs ``D1``, ``D2``, ``D3`` as
  case-sensitive substrings anywhere (learner-facing IDs are built so that they never
  contain them: lowercase hex, book codes without the letters A and D);
* method and role words, case-insensitive: ``method``, ``designer``, ``hand-designed``,
  ``human``, ``evolution``, ``optimizer``, ``transformer``, ``language model``, ``llm``
  and a few variants.

Atom IDs such as ``K-a1`` use lowercase letters and do not match.
"""

from __future__ import annotations

import re
from typing import Final

METHOD_CODES: Final[tuple[str, ...]] = ("A1", "A2", "A3")
DESIGNER_CODES: Final[tuple[str, ...]] = ("D1", "D2", "D3")
METHOD_WORDS: Final[tuple[str, ...]] = (
    "method",
    "designer",
    "hand-design",
    "hand design",
    "handmade",
    "hand-made",
    "human",
    "evolution",
    "genetic",
    "optimizer",
    "optimiser",
    "optimization",
    "optimisation",
    "transformer",
    "language model",
    "llm",
    "proposal model",
)

_CODES_RE: Final = re.compile("|".join(METHOD_CODES + DESIGNER_CODES))
_WORDS_RE: Final = re.compile("|".join(re.escape(w) for w in METHOD_WORDS), re.IGNORECASE)


def find_method_strings(text: str) -> list[str]:
    """All method codes, designer IDs and method words found in ``text`` (in order)."""
    hits = [(m.start(), m.group(0)) for m in _CODES_RE.finditer(text)]
    hits += [(m.start(), m.group(0)) for m in _WORDS_RE.finditer(text)]
    return [hit for _, hit in sorted(hits)]


def assert_masked(text: str, what: str = "text") -> None:
    """Raise ``ValueError`` if ``text`` contains any method string."""
    found = find_method_strings(text)
    if found:
        raise ValueError(f"{what} contains method strings: {sorted(set(found))}")
