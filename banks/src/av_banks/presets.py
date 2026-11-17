"""Study B sample sizes from the schedules preset file (#266): pairs, spare slots, banks.

`schedules/presets/sample-size-presets.json` (`av_schedules.presets`, schema
`schedules/schema/sample-size-presets.schema.json`) is the single source of the
confirmatory unit counts. The bank tools read its Study B part as a file contract (the
schedules package is not imported): each preset names the Study B pairs (`b_pairs`, the
main dyad slots `B-C01`..) and spare slots (`b_spares`, `B-S01`..), one bank each, and the
file names the `default` preset. The other fields (Study A batches, the description) are
not used here.

The confirmatory campaign takes its size from the unit set it is given
(`confirmatory.size`); a preset name only cross-checks it. The defaults that remain
(the throughput projection, `permutation.CONFIRMATORY_MAIN_SLOTS`, the rehearsal) come
from the file's default preset, so no module hard-codes a study count.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Final

from av_banks._paths import sample_size_presets_path

PRESETS_FORMAT: Final = "av-schedules/sample-size-presets"
PRESETS_FORMAT_VERSION: Final = 1
MAX_SLOTS: Final = 99
"""Unit IDs have two digits (`B-C01`..`B-C99`, `B-S01`..`B-S99`)."""


class PresetError(ValueError):
    """The preset file is malformed, or names no such preset."""


@dataclass(frozen=True, slots=True)
class BankPreset:
    """The Study B part of one sample-size preset."""

    name: str
    pairs: int
    """Main dyad slots (`b_pairs`): one main bank each."""
    spares: int
    """Spare slots (`b_spares`): one spare bank each."""

    @property
    def banks(self) -> int:
        """Confirmatory banks: one per main dyad slot and per spare slot."""
        return self.pairs + self.spares


def _count(entry: Mapping[str, object], field: str, low: int) -> int:
    value = entry.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= MAX_SLOTS:
        raise PresetError(f"preset {entry.get('name')!r}: {field} must be {low}..{MAX_SLOTS}")
    return value


def parse_presets(data: object) -> tuple[str, Mapping[str, BankPreset]]:
    """The default preset name and the presets (file order) of a preset document."""
    if (
        not isinstance(data, dict)
        or data.get("format") != PRESETS_FORMAT
        or data.get("format_version") != PRESETS_FORMAT_VERSION
        or not isinstance(data.get("presets"), list)
        or not data["presets"]
    ):
        raise PresetError(f"not a {PRESETS_FORMAT} version {PRESETS_FORMAT_VERSION} document")
    presets: dict[str, BankPreset] = {}
    for entry in data["presets"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise PresetError("every preset is an object with a name")
        name = entry["name"]
        if name in presets:
            raise PresetError(f"duplicate preset {name!r}")
        presets[name] = BankPreset(name, _count(entry, "b_pairs", 1), _count(entry, "b_spares", 0))
    default = data.get("default")
    if not isinstance(default, str) or default not in presets:
        raise PresetError(f"default preset {default!r} is not defined")
    return default, MappingProxyType(presets)


@cache
def _load(path: Path) -> tuple[str, Mapping[str, BankPreset]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise PresetError(f"{path.name}: {err}") from err
    return parse_presets(data)


def load_presets(
    path: str | os.PathLike[str] | None = None,
) -> tuple[str, Mapping[str, BankPreset]]:
    """The default name and the presets of `path` (default: the checkout's preset file,
    `_paths.sample_size_presets_path`; `FileNotFoundError` when it is missing)."""
    return _load(Path(path) if path is not None else sample_size_presets_path())


def preset_names(path: str | os.PathLike[str] | None = None) -> tuple[str, ...]:
    return tuple(load_presets(path)[1])


def all_presets(path: str | os.PathLike[str] | None = None) -> tuple[BankPreset, ...]:
    """Every preset, in file order."""
    return tuple(load_presets(path)[1].values())


def get_preset(
    name: str | None = None, *, path: str | os.PathLike[str] | None = None
) -> BankPreset:
    """The preset `name` (default: the file's default preset)."""
    default, presets = load_presets(path)
    key = default if name is None else name
    if key not in presets:
        raise PresetError(f"unknown sample-size preset {key!r}; choose one of {', '.join(presets)}")
    return presets[key]


def default_preset() -> BankPreset:
    """The default preset of the checkout's preset file."""
    return get_preset()
