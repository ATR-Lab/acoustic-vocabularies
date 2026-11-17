"""The campaign size: from the unit set, cross-checked against a preset or given counts.

`plan` never assumes a study count. It reads the size from the `--units` directory
(`units_size`): the confirmatory Study B units there must be exactly `B-C01`..`B-C<n>`
(main dyad slots) and `B-S01`..`B-S<m>` (spares, possibly none), each a directory with a
`permutation.json`. Other entries are not confirmatory units and are skipped: the pilot
units `B-P..` and the design tables and manifests that the schedules output keeps in the
same directory. A gap, a malformed confirmatory unit ID (`B-C1`, `B-C001`, `B-S00`) or a
unit without `permutation.json` is refused (`E_UNITS`).

An optional sample-size preset (`--preset NAME`, read from the schedules preset file,
`presets`) and the counts `--dyads N` and `--spares M` cross-check the derived size; a
mismatch is refused (`E_UNITS`). `requested_size` turns the same options into a size where
there is no unit set yet (the DEMO rehearsal).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final

from av_banks.presets import PresetError, get_preset

from .common import DEFAULT_SIZE, E_INPUT, E_UNITS, CampaignError, CampaignSize

UNIT_RE: Final = re.compile(r"B-([CS])([0-9]{2})")
"""A confirmatory Study B unit: a main dyad slot `B-Cnn` or a spare `B-Snn`."""
CONFIRMATORY_PREFIXES: Final = ("B-C", "B-S")
MAX_LISTED: Final = 5


def preset_size(name: str) -> CampaignSize:
    """The size of the sample-size preset `name` (`E_INPUT` when the preset file is
    missing or malformed or has no such preset)."""
    try:
        return CampaignSize.of(get_preset(name))
    except (OSError, PresetError) as err:
        raise CampaignError(E_INPUT, f"sample-size preset {name!r}: {err}") from err


def _missing(numbers: set[int], kind: str) -> list[str]:
    top = max(numbers, default=0)
    return [f"B-{kind}{n:02d}" for n in range(1, top + 1) if n not in numbers]


def units_size(units: str | os.PathLike[str]) -> CampaignSize:
    """The size of the unit set under `units` (module docstring)."""
    root = Path(units)
    if not root.is_dir():
        raise CampaignError(E_UNITS, f"{root.name}: not a units directory")
    numbers: dict[str, set[int]] = {"C": set(), "S": set()}
    problems: list[str] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.name.startswith(CONFIRMATORY_PREFIXES):
            continue  # pilot units, other studies, design tables and manifests
        match = UNIT_RE.fullmatch(entry.name)
        if match is None or match.group(2) == "00":
            problems.append(f"{entry.name} is not a confirmatory unit ID (B-C01.., B-S01..)")
        elif not (entry / "permutation.json").is_file():
            problems.append(f"{entry.name} has no permutation.json")
        else:
            numbers[match.group(1)].add(int(match.group(2)))
    if not numbers["C"]:
        problems.append("no main dyad slot (B-C01..)")
    for kind in ("C", "S"):
        gaps = _missing(numbers[kind], kind)
        if gaps:
            listed = ", ".join(gaps[:MAX_LISTED]) + (" ..." if len(gaps) > MAX_LISTED else "")
            problems.append(f"missing {listed} (the B-{kind} units are not contiguous)")
    if problems:
        raise CampaignError(
            E_UNITS,
            f"{root.name}: the confirmatory units must be exactly B-C01..B-C<n> and "
            f"B-S01..B-S<m>: {'; '.join(problems[:MAX_LISTED])}",
        )
    return CampaignSize(len(numbers["C"]), len(numbers["S"]))


def check_size(
    size: CampaignSize,
    *,
    preset: str | None = None,
    dyads: int | None = None,
    spares: int | None = None,
) -> CampaignSize:
    """Refuse (`E_UNITS`) a unit-set size that differs from the preset or the given
    counts; returns `size`."""
    if preset is not None:
        expected = preset_size(preset)
        if expected != size:
            raise CampaignError(
                E_UNITS,
                f"the units hold {size.text()}; preset {preset} has {expected.text()}",
            )
    if dyads is not None and dyads != size.n_main:
        raise CampaignError(
            E_UNITS, f"--dyads {dyads}: the units hold {size.n_main} main dyad slots"
        )
    if spares is not None and spares != size.n_spares:
        raise CampaignError(
            E_UNITS, f"--spares {spares}: the units hold {size.n_spares} spare slots"
        )
    return size


def requested_size(
    *, preset: str | None = None, dyads: int | None = None, spares: int | None = None
) -> CampaignSize:
    """The size named by a preset and/or counts, where there is no unit set yet. Counts
    not given come from the preset, else from the default preset (`DEFAULT_SIZE`);
    counts that differ from a given preset are refused (`E_INPUT`)."""
    base = preset_size(preset) if preset is not None else DEFAULT_SIZE
    size = CampaignSize(
        base.n_main if dyads is None else dyads, base.n_spares if spares is None else spares
    )
    if preset is not None and size != base:
        raise CampaignError(
            E_INPUT, f"preset {preset} has {base.text()}; the counts ask for {size.text()}"
        )
    return size
