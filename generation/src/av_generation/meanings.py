"""Meaning texts: the one source of the operational meanings shown to people and the model.

Every screen and prompt that names an atom's meaning reads the same frozen texts: the
rater stations (`slot.meaning`, the reference meaning; #20/#21), the A1 designer screen
(#19), the A3 and Study B prompts (#17) and the threshold-free parts of the dry run
(#22). A meaning set is a directory holding `meanings.json`
(`meanings.schema.json`, format `av-generation/meanings`): semantic label -> printable
ASCII text of 1-200 characters (the `slot` message limit).

The real texts follow the protocol (Common procedures §3) and stay in restricted storage
with only their hash committed, like the prompt sets; the repository carries a clearly
labelled DEMO set (`generation/examples/demo-meanings/`). The hash is
`MeaningSet.sha256()` (canonical JSON of the document); runs record it in the run
manifest (`meanings_sha256`) and the generation config (`genconfig`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from av_sound.store import SEMANTIC_LABELS

from av_generation.ids import is_demo
from av_generation.masking import masking_findings
from av_generation.records import Document, RecordError

MEANINGS_FILE: Final = "meanings.json"
ALL_LABELS: Final[frozenset[str]] = frozenset(
    label for labels in SEMANTIC_LABELS.values() for label in labels
)


@dataclass(frozen=True, slots=True)
class MeaningSet(Document):
    """Semantic label -> meaning text (16 labels)."""

    TAG = "av-generation/meanings"
    VERSION = 1
    SCHEMA = "meanings.schema.json"

    name: str
    demo: bool
    meanings: Mapping[str, str]

    def check_consistency(self) -> MeaningSet:
        """Labels, demo naming and masking rules the schema cannot express."""
        problems: list[str] = []
        if set(self.meanings) != ALL_LABELS:
            problems.append("meanings must cover exactly the 16 semantic labels")
        if self.demo != is_demo(self.name):
            problems.append("demo sets, and only they, have DEMO- names")
        for label, text in sorted(self.meanings.items()):
            findings = masking_findings(text)
            if findings:
                problems.append(f"{label}: meaning text reveals a method ({findings[0]})")
        if problems:
            raise RecordError("inconsistent meaning set", tuple(problems))
        return self

    def text(self, label: str) -> str:
        """The meaning text of a semantic label."""
        try:
            return self.meanings[label]
        except KeyError:
            raise KeyError(f"no meaning for label {label!r}") from None

    def for_atom(self, atom_id: str, labels: Mapping[str, str]) -> str:
        """The meaning of an atom under a batch's or bank's label permutation."""
        return self.text(labels[atom_id])


def load_meanings(
    path: str | os.PathLike[str], *, expected_sha256: str | None = None
) -> MeaningSet:
    """Load `<path>/meanings.json` (or the file itself) and check it.

    `expected_sha256` (from the generation config) must equal `MeaningSet.sha256()`;
    a mismatch raises `RecordError`.
    """
    source = Path(path)
    if source.is_dir():
        source = source / MEANINGS_FILE
    meanings = MeaningSet.read(source).check_consistency()
    if expected_sha256 is not None and meanings.sha256() != expected_sha256:
        raise RecordError(
            f"meaning set {meanings.name!r} has SHA-256 {meanings.sha256()}, "
            f"expected {expected_sha256}"
        )
    return meanings
