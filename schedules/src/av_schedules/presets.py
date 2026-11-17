"""Sample-size presets (#266): the confirmatory unit counts as a configuration choice.

The Study A batch count and the Study B pair count of the confirmatory sets are the study
owner's decision. ``schedules/presets/sample-size-presets.json`` (schema
``schema/sample-size-presets.schema.json``) is the single source of those counts; the
generators take a preset name (default :data:`DEFAULT_PRESET`, the file's ``default``),
so a different choice needs no code change. Pilot sizes and the per-unit structure
(profiles, designers, methods, learners per book, raters, Study B blocks of 4) are the
same in every preset.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

from ._paths import data_root

PRESETS_FORMAT: Final = "av-schedules/sample-size-presets"
PRESETS_FORMAT_VERSION: Final = 1
PRESET_FIELDS: Final[tuple[str, ...]] = ("name", "description", "a_batches", "b_pairs", "b_spares")

# Fixed in every preset (protocol constants); used for the derived counts only.
BOOKS_PER_BATCH: Final = 3  # one book per Study A method
LEARNERS_PER_BOOK: Final = 4  # confirmatory sets
RATERS_PER_BATCH: Final = 3  # one rating panel of 3 raters per batch
PANEL_SESSIONS_PER_BATCH: Final = 4
PEOPLE_PER_PAIR: Final = 2
# Count rules (as in the schema): Study A needs 3 profiles x 2 swap levels per 6 batches,
# Study B complete blocks of 4 (SQ arm x swap); unit IDs have two digits.
_STEP: Final[dict[str, int]] = {"a_batches": 6, "b_pairs": 4, "b_spares": 4}
_MAX_UNITS: Final = 96


@dataclass(frozen=True)
class SampleSizePreset:
    """Confirmatory unit counts of one preset (pilot sizes are fixed)."""

    name: str
    description: str
    a_batches: int  # Study A confirmatory batches
    b_pairs: int  # Study B confirmatory pairs (dyad slots)
    b_spares: int  # Study B spare slots (one spare bank each)

    def counts(self) -> dict[str, int]:
        """Derived confirmatory counts: Study A books, learners, raters, panel sessions;
        Study B people and banks (pairs + spare slots)."""
        books = self.a_batches * BOOKS_PER_BATCH
        return {
            "batches": self.a_batches,
            "books": books,
            "learners": books * LEARNERS_PER_BOOK,
            "raters": self.a_batches * RATERS_PER_BATCH,
            "panel_sessions": self.a_batches * PANEL_SESSIONS_PER_BATCH,
            "pairs": self.b_pairs,
            "people": self.b_pairs * PEOPLE_PER_PAIR,
            "spare_slots": self.b_spares,
            "banks": self.b_pairs + self.b_spares,
        }


def presets_path() -> Path:
    """``schedules/presets/sample-size-presets.json``."""
    return data_root() / "presets" / "sample-size-presets.json"


def _preset(entry: object) -> SampleSizePreset:
    if not isinstance(entry, dict) or set(entry) != set(PRESET_FIELDS):
        raise ValueError(f"a preset needs exactly the fields {', '.join(PRESET_FIELDS)}")
    name, description = entry["name"], entry["description"]
    if not isinstance(name, str) or not name or not isinstance(description, str):
        raise ValueError("preset name and description must be non-empty strings")
    for field, step in _STEP.items():
        value = entry[field]
        low = 0 if field == "b_spares" else step
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not low <= value <= _MAX_UNITS
            or value % step
        ):
            raise ValueError(
                f"preset {name}: {field} must be a multiple of {step} from {low} to {_MAX_UNITS}"
            )
    return SampleSizePreset(
        name, description, entry["a_batches"], entry["b_pairs"], entry["b_spares"]
    )


def parse_presets(data: object) -> tuple[str, dict[str, SampleSizePreset]]:
    """Default name and presets (in file order) of a sample-size-presets document.

    Raises ``ValueError`` for a malformed document, a duplicate name or an unknown default.
    """
    if (
        not isinstance(data, dict)
        or data.get("format") != PRESETS_FORMAT
        or data.get("format_version") != PRESETS_FORMAT_VERSION
        or not isinstance(data.get("presets"), list)
        or not data["presets"]
    ):
        raise ValueError(f"not a {PRESETS_FORMAT} version {PRESETS_FORMAT_VERSION} document")
    presets: dict[str, SampleSizePreset] = {}
    for entry in data["presets"]:
        preset = _preset(entry)
        if preset.name in presets:
            raise ValueError(f"duplicate preset {preset.name}")
        presets[preset.name] = preset
    default = data.get("default")
    if not isinstance(default, str) or default not in presets:
        raise ValueError(f"default preset {default!r} is not defined")
    return default, presets


@cache
def _load() -> tuple[str, dict[str, SampleSizePreset]]:
    return parse_presets(json.loads(presets_path().read_text(encoding="utf-8")))


DEFAULT_PRESET: Final[str] = _load()[0]
PRESET_NAMES: Final[tuple[str, ...]] = tuple(_load()[1])


def get_preset(name: str = DEFAULT_PRESET) -> SampleSizePreset:
    """The preset called ``name``; ``ValueError`` names the known presets otherwise."""
    presets = _load()[1]
    if name not in presets:
        raise ValueError(f"unknown sample-size preset {name!r}; choose one of {', '.join(presets)}")
    return presets[name]
