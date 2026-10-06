"""The analysis report in analysis plan section 9 order (#34).

Implemented here (shared contract): the section order (:data:`REPORT_SECTIONS`) and the
denominator statement every empirical table carries (:class:`TableMeta`). Interface
(#34): building the sections and writing ``estimates/report.md`` (watermarked, see
``paths.check_watermark``) with its tables as CSV and the GLMM logs as JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .paths import DataRoot


@dataclass(frozen=True)
class Section:
    """One section of the report."""

    number: int
    id: str
    title: str
    plan_ref: str
    studies: tuple[str, ...]


REPORT_SECTIONS: Final[tuple[Section, ...]] = (
    Section(1, "flow", "Participant and codebook flow", "section 6", ("A", "B")),
    Section(2, "fidelity", "Apparatus and exposure fidelity", "sections 8-9", ("A", "B")),
    Section(
        3, "a_primary", "Study A primary estimate and codebook distribution", "section 3", ("A",)
    ),
    Section(
        4,
        "b_primary",
        "Study B role and scaffold estimates with joint multiplicity",
        "section 4",
        ("B",),
    ),
    Section(5, "secondary", "Delayed, novel and component outcomes", "sections 2, 5", ("A", "B")),
    Section(
        6,
        "ownership_consultation",
        "Ownership and consultation, reported separately",
        "section 5",
        ("A", "B"),
    ),
    Section(
        7,
        "sensitivities",
        "Technical, missingness and late-visit sensitivities",
        "section 6",
        ("A", "B"),
    ),
    Section(8, "deviations", "Deviations and bounded conclusions", "sections 6, 9", ("A", "B")),
)


@dataclass(frozen=True)
class TableMeta:
    """What every empirical table states (section 9): independent units, trials, missing."""

    independent_unit: str  # "batch", "dyad", "book", "person"
    units: int  # contributing independent units
    units_planned: int
    trials: int  # trial rows behind the table
    missing: int  # planned units without a complete endpoint
    note: str = ""

    def line(self) -> str:
        """One-line statement printed under the table."""
        text = (
            f"Independent unit: {self.independent_unit}; {self.units} of {self.units_planned} "
            f"planned units contribute ({self.missing} missing); {self.trials} trials."
        )
        return f"{text} {self.note}".rstrip()


def sections_for(study: str) -> tuple[Section, ...]:
    """Sections of one study's report, in order."""
    return tuple(s for s in REPORT_SECTIONS if study in s.studies)


def build_report(root: DataRoot, study: str) -> list[Path]:
    """Write the section 9 report of a study into ``estimates/``; returns the files."""
    raise NotImplementedError("#34: section 9 report")
