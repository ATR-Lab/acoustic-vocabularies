"""Masking rules: what may name a method, and a string check for masked outputs.

Restricted (may name methods): run manifests, batch configs, every log under a run
directory, unmasked audit outputs. Masked (must not): everything raters, session
experimenters and blinded analysts see: rater-station messages and pages, the operator
console of the panel, masked audit outputs (#24) and anything derived for #34's blinded
tables. Masked outputs identify books by anonymous book ID only and also drop
method-revealing fields: designer IDs, seed keys (they start with the method), prompt
hashes, token counts, model runtime, A1 design time and A2 mutation details.

`masking_findings()` is the string test of #24's acceptance criterion ("0 method
labels"); #19, #20 and #21 use it for their sentinel tests.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

METHOD_LABEL_TOKENS: Final[frozenset[str]] = frozenset({"A1", "A2", "A3"})
"""Case-sensitive tokens: lowercase `a1` is a matrix index (`K-a1`)."""
DESIGNER_TOKENS: Final[frozenset[str]] = frozenset({"D1", "D2", "D3"})
"""Anonymous A1 designer IDs: they reveal the A1 book."""
METHOD_WORD_PREFIXES: Final[tuple[str, ...]] = (
    "designer",
    "handdesign",
    "human",
    "mutation",
    "mutate",
    "evolution",
    "genetic",
    "optimi",
    "transformer",
    "llm",
    "vllm",
    "qwen",
    "gpt",
    "prompt",
    "seedkey",
)
"""Case-insensitive prefixes of alphanumeric tokens (underscores and hyphens split tokens,
and `seed_key` / `hand-design` are also checked joined)."""
METHOD_REVEALING_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "method",
        "designer_id",
        "seed_key",
        "seed",
        "wire_seed",
        "prompt_sha256",
        "schema_sha256",
        "llm_status",
        "tokens_in",
        "tokens_out",
        "design_ms",
        "a2",
        "raw_output",
    }
)
"""Record fields that must be dropped from any masked output."""

_TOKEN: Final = re.compile(r"[A-Za-z0-9]+")
_JOINED: Final = re.compile(r"[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)+")


def masking_findings(text: str, *, extra_words: Iterable[str] = ()) -> tuple[str, ...]:
    """Every method label, designer ID or method word in `text` (empty = masked).

    `extra_words` adds case-insensitive prefixes (e.g. a book's real method names in a
    sentinel test).
    """
    prefixes = tuple(w.casefold() for w in (*METHOD_WORD_PREFIXES, *extra_words))
    found: list[str] = []
    for token in _TOKEN.findall(text):
        folded = token.casefold()
        if token in METHOD_LABEL_TOKENS:
            found.append(f"method label {token!r}")
        elif token in DESIGNER_TOKENS:
            found.append(f"designer ID {token!r}")
        elif any(folded.startswith(p) for p in prefixes):
            found.append(f"method word {token!r}")
    for joined in _JOINED.findall(text):
        squashed = re.sub(r"[_-]", "", joined).casefold()
        if squashed.startswith(("seedkey", "handdesign")):
            found.append(f"method word {joined!r}")
    return tuple(found)


def drop_method_fields(record: dict[str, object]) -> dict[str, object]:
    """A copy of a record dict without `METHOD_REVEALING_FIELDS` (top level)."""
    return {k: v for k, v in record.items() if k not in METHOD_REVEALING_FIELDS}
