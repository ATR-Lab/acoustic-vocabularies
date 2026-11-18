"""Run the grid of a configuration, summarise rejection rates and write the outputs.

A cell is one study x SD source x scenario x loss x size. Replicate ``k`` of a cell draws
from ``seeds.rng(seed, "power-check", study, source, scenario, "loss-<q>", "n-<size>",
k)``, so every cell is reproducible on its own and the same configuration gives the same
bytes. Outputs (all marked SYNTHETIC; no wall-clock time):

* ``power.csv``: one row per cell and contrast (:data:`POWER_COLUMNS`).
* ``power.md``: verdict summary and power tables (percent with Monte Carlo SE).
* ``verdict.json``: per primary contrast and case (SD source x loss x scenario in which
  the contrast's true effect equals ``effect_pp``), whether the point estimate reaches
  the criterion at each size, the smallest such size, and the same with the Wilson 95%
  Monte Carlo lower limit; ``all_cases``: the smallest size at which every case holds.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np

from ..estimators import wilson
from ..fileio import csv_bytes, json_bytes, write_bytes
from ..seeds import rng
from .config import PRIMARY, EffectScenario, Heterogeneity, PowerConfig, StudyGrid
from .engines import Decision, decide, replicate_function

DATA_KIND: Final = "SYNTHETIC"
VERDICT_FORMAT: Final = "av-analysis/power-check-verdict"
VERDICT_FORMAT_VERSION: Final = 1
DECISION_REPLICATES: Final = 1000  # fewer replicates: not for the freeze decision
OUTPUT_FILES: Final = ("power.csv", "power.md", "verdict.json")
REPORTED: Final[dict[str, tuple[str, ...]]] = {"A": ("A3-A2",), "B": ("C", "S", "any", "both")}
RULES: Final[dict[str, str]] = {
    "A3-A2": "two-sided one-sample t over complete batches, p <= alpha",
    "C": "one-sample t over complete pairs; Holm over C and S (alpha/2, then alpha)",
    "S": "one-sample t over complete pairs; Holm over C and S (alpha/2, then alpha)",
    "any": "C or S rejected by Holm (family-wise under the global null)",
    "both": "C and S both rejected by Holm",
}
POWER_COLUMNS: Final[tuple[str, ...]] = (
    "data_kind",
    "basis",
    "study",
    "engine",
    "sd_source",
    "sd_setting",
    "scenario",
    "loss",
    "size",
    "contrast",
    "true_effect_pp",
    "rule",
    "replicates",
    "rejections",
    "rate",
    "mcse",
    "mc95_low",
    "mc95_high",
    "mean_complete_units",
    "implied_unit_sd",
    "implied_unit_sd_mcse",
    "mean_estimate",
    "unavailable",
    "seed",
)


@dataclass(frozen=True)
class Cell:
    """One study x SD source x scenario x loss x size."""

    grid: StudyGrid
    heterogeneity: Heterogeneity
    scenario: EffectScenario
    loss: float
    size: int


@dataclass(frozen=True)
class PowerRow:
    """Rejection rate of one contrast in one cell."""

    study: str
    engine: str
    sd_source: str
    sd_setting: str
    scenario: str
    loss: float
    size: int
    contrast: str
    true_effect_pp: float | None
    replicates: int
    rejections: int
    rate: float
    mcse: float
    mc95_low: float
    mc95_high: float
    mean_complete_units: float
    implied_unit_sd: float | None  # mean over replicates of the SD of the unit differences
    implied_unit_sd_mcse: float | None  # its Monte Carlo SE (SD of the SDs / sqrt(count))
    mean_estimate: float | None
    unavailable: int  # replicates with fewer than 2 complete units (or no Holm family)
    seed: str

    @property
    def case(self) -> tuple[str, float, str]:
        return (self.sd_source, self.loss, self.scenario)

    def csv_cells(self, basis: str) -> list[str]:
        def f(x: float | None) -> str:
            return "" if x is None else repr(float(x))

        return [
            DATA_KIND,
            basis,
            self.study,
            self.engine,
            self.sd_source,
            self.sd_setting,
            self.scenario,
            repr(self.loss),
            str(self.size),
            self.contrast,
            f(self.true_effect_pp),
            RULES[self.contrast],
            str(self.replicates),
            str(self.rejections),
            f(self.rate),
            f(self.mcse),
            f(self.mc95_low),
            f(self.mc95_high),
            f(self.mean_complete_units),
            f(self.implied_unit_sd),
            f(self.implied_unit_sd_mcse),
            f(self.mean_estimate),
            str(self.unavailable),
            self.seed,
        ]


def cells(config: PowerConfig) -> list[Cell]:
    """Every cell, in configuration order (study, source, scenario, loss, size)."""
    return [
        Cell(grid, het, scenario, loss, size)
        for grid in config.studies
        for het in grid.heterogeneity
        for scenario in grid.scenarios
        for loss in grid.loss
        for size in grid.sizes
    ]


def cell_rng(seed: str, cell: Cell, replicate: int) -> np.random.Generator:
    """Generator of replicate ``replicate`` of ``cell``."""
    return rng(
        seed,
        "power-check",
        cell.grid.study,
        cell.heterogeneity.name,
        cell.scenario.name,
        f"loss-{cell.loss!r}",
        f"n-{cell.size}",
        str(replicate),
    )


def _mean(xs: Sequence[float]) -> float | None:
    return math.fsum(xs) / len(xs) if xs else None


def _mcse(xs: Sequence[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = math.fsum(xs) / len(xs)
    return math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1) / len(xs))


def summarise(cell: Cell, decisions: Sequence[Decision], seed: str) -> list[PowerRow]:
    """Rows of one cell from its replicate decisions."""
    n = len(decisions)
    if n < 1:
        raise ValueError("a cell needs at least one replicate")
    out = []
    for contrast in REPORTED[cell.grid.study]:
        rejections = sum(d.reject[contrast] for d in decisions)
        rate = rejections / n
        low, high = wilson(rejections, n)
        if contrast in cell.scenario.effect_pp:
            tests = [d.tests[contrast] for d in decisions]
            units = [float(t.n) for t in tests]
            sds = [t.sd for t in tests if t.sd is not None]
            means = [t.mean for t in tests if t.mean is not None]
            unavailable = sum(1 for t in tests if t.p is None)
            effect: float | None = cell.scenario.effect_pp[contrast]
        else:
            units = [float(min(t.n for t in d.tests.values())) for d in decisions]
            sds, means, effect = [], [], None
            unavailable = sum(1 for d in decisions if any(t.p is None for t in d.tests.values()))
        out.append(
            PowerRow(
                study=cell.grid.study,
                engine=cell.heterogeneity.engine,
                sd_source=cell.heterogeneity.name,
                sd_setting=cell.heterogeneity.describe(),
                scenario=cell.scenario.name,
                loss=cell.loss,
                size=cell.size,
                contrast=contrast,
                true_effect_pp=effect,
                replicates=n,
                rejections=rejections,
                rate=rate,
                mcse=math.sqrt(rate * (1.0 - rate) / n),
                mc95_low=low,
                mc95_high=high,
                mean_complete_units=math.fsum(units) / n,
                implied_unit_sd=_mean(sds),
                implied_unit_sd_mcse=_mcse(sds),
                mean_estimate=_mean(means),
                unavailable=unavailable,
                seed=seed,
            )
        )
    return out


def run_cell(config: PowerConfig, cell: Cell) -> list[PowerRow]:
    """Simulate ``config.replicates`` replicates of one cell."""
    replicate = replicate_function(
        cell.grid, cell.heterogeneity, cell.scenario, cell.loss, cell.size
    )
    decisions = [
        decide(cell.grid.study, replicate(cell_rng(config.seed, cell, k)), config.alpha)
        for k in range(config.replicates)
    ]
    return summarise(cell, decisions, config.seed)


def run_config(
    config: PowerConfig, progress: Callable[[Cell, int, int], None] | None = None
) -> list[PowerRow]:
    """Rows of every cell of the configuration."""
    todo = cells(config)
    rows: list[PowerRow] = []
    for i, cell in enumerate(todo, start=1):
        rows.extend(run_cell(config, cell))
        if progress is not None:
            progress(cell, i, len(todo))
    return rows


# ---------------------------------------------------------------------------------------
# Verdict


def _size_entry(row: PowerRow, criterion: float) -> dict[str, Any]:
    return {
        "size": row.size,
        "rate": row.rate,
        "mcse": row.mcse,
        "mc95_low": row.mc95_low,
        "mc95_high": row.mc95_high,
        "holds": row.rate >= criterion,
        "holds_mc95_low": row.mc95_low >= criterion,
    }


def _smallest(entries: Iterable[dict[str, Any]], key: str) -> int | None:
    return next((int(e["size"]) for e in entries if e[key]), None)


def verdict(config: PowerConfig, rows: Sequence[PowerRow]) -> dict[str, Any]:
    """The verdict document (``verdict.json``)."""
    contrasts = []
    for grid in config.studies:
        for contrast in PRIMARY[grid.study]:
            mine = [
                r
                for r in rows
                if r.study == grid.study
                and r.contrast == contrast
                and r.true_effect_pp is not None
                and math.isclose(r.true_effect_pp, config.effect_pp)
            ]
            cases: list[dict[str, Any]] = []
            for case in dict.fromkeys(r.case for r in mine):
                case_rows = sorted((r for r in mine if r.case == case), key=lambda r: r.size)
                entries = [_size_entry(r, config.criterion) for r in case_rows]
                cases.append(
                    {
                        "sd_source": case[0],
                        "engine": case_rows[0].engine,
                        "sd_setting": case_rows[0].sd_setting,
                        "loss": case[1],
                        "scenario": case[2],
                        "sizes": entries,
                        "smallest_size": _smallest(entries, "holds"),
                        "smallest_size_mc95_low": _smallest(entries, "holds_mc95_low"),
                    }
                )
            all_cases: list[dict[str, Any]] = []
            for size in grid.sizes:
                at = [e for c in cases for e in c["sizes"] if e["size"] == size]
                all_cases.append(
                    {
                        "size": size,
                        "holds": bool(at) and all(e["holds"] for e in at),
                        "holds_mc95_low": bool(at) and all(e["holds_mc95_low"] for e in at),
                    }
                )
            contrasts.append(
                {
                    "study": grid.study,
                    "contrast": contrast,
                    "rule": RULES[contrast],
                    "cases": cases,
                    "all_cases": {
                        "sizes": all_cases,
                        "smallest_size": _smallest(all_cases, "holds"),
                        "smallest_size_mc95_low": _smallest(all_cases, "holds_mc95_low"),
                    },
                }
            )
    return {
        "format": VERDICT_FORMAT,
        "format_version": VERDICT_FORMAT_VERSION,
        "data_kind": DATA_KIND,
        "basis": config.basis,
        "config_sha256": config.sha256,
        "seed": config.seed,
        "replicates": config.replicates,
        "decision_grade": config.replicates >= DECISION_REPLICATES,
        "alpha": config.alpha,
        "effect_pp": config.effect_pp,
        "criterion": config.criterion,
        "verdict_rule": "the point estimate of power must reach the criterion; "
        "smallest_size_mc95_low uses the Wilson 95% Monte Carlo lower limit instead",
        "contrasts": contrasts,
    }


# ---------------------------------------------------------------------------------------
# Outputs


def power_csv(config: PowerConfig, rows: Sequence[PowerRow]) -> bytes:
    """``power.csv`` bytes."""
    return csv_bytes(POWER_COLUMNS, (r.csv_cells(config.basis) for r in rows))


def _pct(x: float) -> str:
    return f"{100.0 * x:.1f}"


def _size_text(n: int | None) -> str:
    return "not reached" if n is None else str(n)


def _table(header: Sequence[str], body: Iterable[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return [*lines, ""]


def _verdict_lines(doc: Mapping[str, Any]) -> list[str]:
    body = []
    for c in doc["contrasts"]:
        for case in c["cases"]:
            body.append(
                [
                    c["study"],
                    c["contrast"],
                    f"{case['sd_source']} ({case['sd_setting']}), loss {_pct(case['loss'])}%, "
                    f"{case['scenario']}",
                    _size_text(case["smallest_size"]),
                    _size_text(case["smallest_size_mc95_low"]),
                ]
            )
        body.append(
            [
                c["study"],
                c["contrast"],
                "**all cases**",
                _size_text(c["all_cases"]["smallest_size"]),
                _size_text(c["all_cases"]["smallest_size_mc95_low"]),
            ]
        )
    return [
        "## Verdict",
        "",
        "Smallest size in the grid at which power reaches the criterion; a case is an SD "
        "source x loss x scenario in which the contrast's true effect is the target.",
        "",
        *_table(["Study", "Contrast", "Case", "Point estimate", "MC 95% lower limit"], body),
    ]


def _cases(rows: Sequence[PowerRow]) -> dict[tuple[str, float, str], dict[int, PowerRow]]:
    """Rows by case (in row order), then by size."""
    out: dict[tuple[str, float, str], dict[int, PowerRow]] = {}
    for r in rows:
        out.setdefault(r.case, {})[r.size] = r
    return out


def _power_lines(config: PowerConfig, grid: StudyGrid, rows: Sequence[PowerRow]) -> list[str]:
    unit = "batches" if grid.study == "A" else "pairs"
    lines = []
    for contrast in REPORTED[grid.study]:
        body = []
        mine = [r for r in rows if r.study == grid.study and r.contrast == contrast]
        for (source, loss, scenario), by_size in _cases(mine).items():
            first = by_size[grid.sizes[0]]
            effect = "" if first.true_effect_pp is None else f"{first.true_effect_pp:g}"
            rates = []
            for size in grid.sizes:
                r = by_size[size]
                text = f"{_pct(r.rate)} ({_pct(r.mcse)})"
                rates.append(f"**{text}**" if r.rate >= config.criterion else text)
            body.append([source, first.engine, f"{_pct(loss)}%", scenario, effect, *rates])
        lines += [
            f"## Study {grid.study}: {contrast}",
            "",
            f"Rule: {RULES[contrast]}. Power % (Monte Carlo SE) by {unit}; **bold** meets the "
            "criterion.",
            "",
            *_table(
                ["SD source", "Engine", "Loss", "Scenario", "True pp", *map(str, grid.sizes)], body
            ),
        ]
    return lines


def _sd_lines(grid: StudyGrid, rows: Sequence[PowerRow]) -> list[str]:
    body = []
    for contrast in PRIMARY[grid.study]:
        mine = [r for r in rows if r.study == grid.study and r.contrast == contrast]
        for (source, loss, scenario), by_size in _cases(mine).items():
            sds = [by_size[n].implied_unit_sd for n in grid.sizes]
            body.append(
                [
                    contrast,
                    source,
                    f"{_pct(loss)}%",
                    scenario,
                    *("" if sd is None else f"{sd:.3f}" for sd in sds),
                ]
            )
    return [
        f"### Study {grid.study}",
        "",
        *_table(["Contrast", "SD source", "Loss", "Scenario", *map(str, grid.sizes)], body),
    ]


def power_md(config: PowerConfig, rows: Sequence[PowerRow], doc: Mapping[str, Any]) -> bytes:
    """``power.md`` bytes: verdict summary, power tables and implied unit SDs."""
    grade = (
        ""
        if doc["decision_grade"]
        else f" Fewer than {DECISION_REPLICATES:,} replicates: not for the freeze decision."
    )
    lines = [
        f"<!-- av-data-kind: {DATA_KIND} -->",
        "# Power check",
        "",
        f"**{DATA_KIND}**: simulated power, no participant data. Basis: {config.basis}. "
        f"{config.description}",
        "",
        f"Seed `{config.seed}`, {config.replicates:,} replicates per cell, alpha "
        f"{config.alpha:g}, target effect {config.effect_pp:g} pp, criterion "
        f"{_pct(config.criterion)}% on the point estimate (the Wilson 95% Monte Carlo lower "
        f"limit is shown too).{grade} Configuration SHA-256 (canonical JSON) "
        f"`{config.sha256}`.",
        "",
        *_verdict_lines(doc),
    ]
    for grid in config.studies:
        lines += _power_lines(config, grid, rows)
    lines += [
        "## Implied unit SD",
        "",
        "Mean over replicates of the SD of the complete unit differences (proportion scale).",
        "",
    ]
    for grid in config.studies:
        lines += _sd_lines(grid, rows)
    return ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")


def write_outputs(out: Path, config: PowerConfig, rows: Sequence[PowerRow]) -> list[Path]:
    """Write ``power.csv``, ``power.md`` and ``verdict.json`` into ``out``."""
    doc = verdict(config, rows)
    data = {
        "power.csv": power_csv(config, rows),
        "power.md": power_md(config, rows, doc),
        "verdict.json": json_bytes(doc),
    }
    paths = []
    for name in OUTPUT_FILES:
        path = out / name
        write_bytes(path, data[name])
        paths.append(path)
    return paths
