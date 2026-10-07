"""The analysis report in analysis plan section 9 order (#34).

Shared contract (skeleton): the section order (:data:`REPORT_SECTIONS`) and the
denominator statement every empirical table carries (:class:`TableMeta`). Implemented by
#34: building the sections (:class:`ReportTable`, :class:`SectionContent`, filled by
``pipeline``) and writing ``estimates/`` (watermarked, ``paths.write_output``):

* ``estimates/report-<study>.md``: the report, sections in order, every table followed by
  its :class:`TableMeta` line and a link to its CSV;
* ``estimates/tables/<study>-<nn>-<table>.csv``: every table in full (first column
  ``data_kind``), and ``estimates/tables/<study>-index.csv`` with each table's section,
  independent unit, contributing and planned units, trials and missing units;
* ``estimates/glmm/<model_id>.json`` (``glmm-log.schema.json``), ``<model_id>-fixed.csv``,
  ``<model_id>-random.csv`` and ``<model_id>-data.csv`` (the model data sent to R);
* ``estimates/runs/<run>.json``: what a run read (input files with SHA-256), the seed
  labels it used and the files it wrote; ``estimates/manifest.json``
  (``outputs-manifest.schema.json``) lists every file of the area with the union of the
  runs' inputs and seeds;
* ``estimates/simulation/``: operating characteristics written by ``av-analysis
  simulate`` (:func:`write_simulation`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from . import __version__
from .fileio import csv_bytes, file_entry, json_bytes, parse_csv, read_bytes
from .paths import (
    OUTPUTS_MANIFEST,
    WATERMARK_BANNER,
    DataRoot,
    WatermarkError,
    write_output,
    write_synthetic_input,
)
from .vocab import DataKind

if TYPE_CHECKING:  # pragma: no cover
    from .glmm import GlmmFit
    from .simulate import Scenario, SyntheticDataset


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
    from .pipeline import run_analysis

    return run_analysis(root, study)


# ---------------------------------------------------------------------------------------
# Report content (filled by ``pipeline``)

Cell = str | int | float | bool | None
# Markdown formats of columns: text, int, f1, f2, f3, f4, p (p value), bool.
FORMATS: Final[tuple[str, ...]] = ("text", "int", "f1", "f2", "f3", "f4", "p", "bool")


@dataclass(frozen=True)
class ReportTable:
    """One table of the report: CSV in full, Markdown up to ``markdown_rows`` rows."""

    id: str  # file-name token, e.g. "a-primary"
    title: str
    columns: tuple[str, ...]
    formats: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]
    meta: TableMeta
    note: str = ""
    markdown_rows: int | None = None  # None: all rows; 0: CSV only

    def __post_init__(self) -> None:
        if len(self.columns) != len(self.formats) or any(f not in FORMATS for f in self.formats):
            raise ValueError(f"table {self.id}: one known format per column")
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError(f"table {self.id}: row length differs from the header")


@dataclass(frozen=True)
class SectionContent:
    """The paragraphs and tables of one section."""

    section_id: str
    paragraphs: tuple[str, ...] = ()
    tables: tuple[ReportTable, ...] = ()


@dataclass(frozen=True)
class StudyReport:
    """Everything a study run writes into ``estimates/``."""

    study: str
    set_name: str
    data_kind: DataKind
    root_label: str
    sections: Mapping[str, SectionContent]
    glmm: tuple[GlmmFit, ...] = ()
    glmm_data: Mapping[str, bytes] = field(default_factory=dict)
    seeds: tuple[str, ...] = ()
    inputs: tuple[Mapping[str, Any], ...] = ()  # file entries relative to the data root
    summary: tuple[str, ...] = ()  # key results printed under the title


def csv_cell(value: Cell) -> str:
    """CSV text of a cell (empty = null, ``true``/``false``, floats as ``repr``)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite value in a report table")
        return repr(value + 0.0)
    return str(value)


def md_cell(value: Cell, fmt: str) -> str:
    """Markdown text of a cell."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if fmt == "p" and isinstance(value, int | float):
        return "<0.0001" if value < 0.0001 else f"{value:.4f}"
    if fmt in ("f1", "f2", "f3", "f4") and isinstance(value, int | float):
        return f"{value:.{fmt[1]}f}"
    return str(value).replace("|", "\\|")


def table_csv(table: ReportTable, data_kind: DataKind) -> bytes:
    """The table's CSV bytes (first column ``data_kind``)."""
    return csv_bytes(
        ("data_kind", *table.columns),
        ([data_kind, *(csv_cell(c) for c in row)] for row in table.rows),
    )


def _md_table(table: ReportTable) -> list[str]:
    shown = table.rows if table.markdown_rows is None else table.rows[: table.markdown_rows]
    if not shown:
        return []
    lines = [
        "| " + " | ".join(table.columns) + " |",
        "| " + " | ".join("---" if f in ("text", "bool") else "---:" for f in table.formats) + " |",
    ]
    for row in shown:
        lines.append(
            "| " + " | ".join(md_cell(c, f) for c, f in zip(row, table.formats, strict=True)) + " |"
        )
    if len(shown) < len(table.rows):
        lines.append("")
        lines.append(f"First {len(shown)} of {len(table.rows)} rows; the CSV holds all.")
    return lines


def table_path(study: str, number: int, table: ReportTable) -> str:
    """Area-relative path of a table CSV."""
    return f"tables/{study}-{number:02d}-{table.id}.csv"


def render(report: StudyReport) -> dict[str, bytes]:
    """All files of a study run, keyed by path relative to ``estimates/``."""
    kind = report.data_kind
    files: dict[str, bytes] = {}
    md: list[str] = [
        f"# Analysis report: Study {report.study} ({report.set_name} set)",
        "",
        f"av-data-kind: {kind}",
        "",
    ]
    if kind == "SYNTHETIC":
        md += [
            f"> **{WATERMARK_BANNER}** data (`{report.root_label}`): generated to test the "
            f"pipeline. These are not study results. {WATERMARK_BANNER}.",
            "",
        ]
    md += [
        f"Generated by av-analysis {__version__} from the derived tables of this data root. "
        "Sections follow analysis plan section 9. Every table states its independent units, "
        "trials and missing units; proportions are percentages and differences percentage "
        "points (pp) unless a column says otherwise.",
        "",
    ]
    if report.summary:
        md += ["**Key results**", ""] + [f"- {s}" for s in report.summary] + [""]
    index_rows = []
    number = 0
    for section in sections_for(report.study):
        content = report.sections.get(section.id, SectionContent(section.id))
        md += [
            f"## {section.number}. {section.title}",
            "",
            f"Analysis plan {section.plan_ref}.",
            "",
        ]
        for p in content.paragraphs:
            md += [p, ""]
        for table in content.tables:
            number += 1
            path = table_path(report.study, number, table)
            files[path] = table_csv(table, kind)
            md += [f"**Table {number}. {table.title}**", ""]
            body = _md_table(table)
            if body:
                md += body + [""]
            md += [f"_{table.meta.line()}_ CSV: [`{path}`]({path})", ""]
            if table.note:
                md += [table.note, ""]
            index_rows.append(
                [
                    kind,
                    table.id,
                    section.id,
                    path,
                    table.title,
                    table.meta.independent_unit,
                    str(table.meta.units),
                    str(table.meta.units_planned),
                    str(table.meta.trials),
                    str(table.meta.missing),
                    table.meta.note,
                ]
            )
    if report.glmm:
        md += ["## Supporting model logs", ""]
        for fit in report.glmm:
            log = fit.log
            rel = f"glmm/{log.model_id}.json"
            md.append(f"- `{log.model_id}`: final rung `{log.final_rung}`; log [`{rel}`]({rel})")
        md.append("")
    md += [f"av-data-kind: {kind}", ""]
    files[f"report-{report.study}.md"] = "\n".join(md).encode("utf-8")
    files[f"tables/{report.study}-index.csv"] = csv_bytes(
        (
            "data_kind",
            "table",
            "section",
            "file",
            "title",
            "independent_unit",
            "units",
            "units_planned",
            "trials",
            "missing",
            "note",
        ),
        index_rows,
    )
    for fit in report.glmm:
        mid = fit.log.model_id
        files[f"glmm/{mid}.json"] = json_bytes(fit.log.document())
        files[f"glmm/{mid}-fixed.csv"] = csv_bytes(
            ("data_kind", "model_id", "term", "estimate", "se", "z", "p"),
            (
                [
                    kind,
                    mid,
                    f.term,
                    csv_cell(f.estimate),
                    csv_cell(f.se),
                    csv_cell(f.z),
                    csv_cell(f.p),
                ]
                for f in fit.fixed
            ),
        )
        files[f"glmm/{mid}-random.csv"] = csv_bytes(
            ("data_kind", "model_id", "group", "term1", "term2", "sdcor"),
            ([kind, mid, r.group, r.term1, r.term2 or "", csv_cell(r.sdcor)] for r in fit.random),
        )
        if mid in report.glmm_data:
            files[f"glmm/{mid}-data.csv"] = report.glmm_data[mid]
    return files


# ---------------------------------------------------------------------------------------
# Writing, run records and the area manifest

RUN_FORMAT: Final = "av-analysis/run-record"


def _analyzer() -> dict[str, str]:
    return {"name": "av-analysis", "version": __version__}


def write_files(
    root: DataRoot,
    area: str,
    files: Mapping[str, bytes],
    *,
    run_name: str,
    command: str,
    seeds: Sequence[str],
    inputs: Sequence[Mapping[str, Any]],
    extra: Mapping[str, Any] | None = None,
) -> list[Path]:
    """Write ``files`` into an output area with a run record and refresh the manifest."""
    written = [
        write_output(root, area, rel, data, root.data_kind)  # type: ignore[arg-type]
        for rel, data in sorted(files.items())
    ]
    record = {
        "format": RUN_FORMAT,
        "format_version": 1,
        "data_kind": root.data_kind,
        "analyzer": _analyzer(),
        "command": command,
        "seeds": sorted(set(seeds)),
        "inputs": sorted(inputs, key=lambda e: str(e["path"])),
        "outputs": [
            {"path": rel, "bytes": len(data), "sha256": file_entry_bytes(data)}
            for rel, data in sorted(files.items())
        ],
        **(extra or {}),
    }
    written.append(
        write_output(root, area, f"runs/{run_name}.json", json_bytes(record), root.data_kind)  # type: ignore[arg-type]
    )
    written.append(update_manifest(root, area))
    return written


def file_entry_bytes(data: bytes) -> str:
    """SHA-256 of bytes (run records)."""
    from .fileio import sha256_bytes

    return sha256_bytes(data)


def update_manifest(root: DataRoot, area: str) -> Path:
    """Rewrite ``<area>/manifest.json``: every file of the area, and the union of the
    inputs and seeds of its run records (``<area>/runs/*.json``)."""
    base = root.area(area)  # type: ignore[arg-type]
    files = []
    inputs: dict[tuple[str, str], Mapping[str, Any]] = {}
    seeds: set[str] = set()
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        rel = path.relative_to(base).as_posix()
        if rel == OUTPUTS_MANIFEST or path.name.startswith("."):
            continue
        files.append(file_entry(path, rel))
        if rel.startswith("runs/") and rel.endswith(".json"):
            import json

            doc = json.loads(read_bytes(path))
            for e in doc.get("inputs", []):
                inputs[(e["path"], e["sha256"])] = e
            seeds.update(doc.get("seeds", []))
    manifest = {
        "format": "av-analysis/outputs-manifest",
        "format_version": 1,
        "data_kind": root.data_kind,
        "area": area,
        "analyzer": _analyzer(),
        "seeds": sorted(seeds),
        "inputs": [inputs[k] for k in sorted(inputs)],
        "files": files,
    }
    return write_output(root, area, OUTPUTS_MANIFEST, json_bytes(manifest), root.data_kind)  # type: ignore[arg-type]


def input_entry(root: DataRoot, path: Path) -> dict[str, Any]:
    """Manifest entry of an input file (path relative to the data root)."""
    return file_entry(path, path.relative_to(root.path).as_posix())


def write_report(root: DataRoot, report: StudyReport) -> list[Path]:
    """Render and write a study report into ``estimates/``."""
    if report.data_kind != root.data_kind:
        raise WatermarkError("report data kind differs from the data root")
    return write_files(
        root,
        "estimates",
        render(report),
        run_name=f"run-{report.study}-{report.set_name}",
        command=f"av-analysis run --study {report.study} --set {report.set_name}",
        seeds=report.seeds,
        inputs=report.inputs,
    )


# ---------------------------------------------------------------------------------------
# Simulation outputs (``av-analysis simulate``)

DATASET_COLUMNS: Final[tuple[str, ...]] = (
    "data_kind",
    "scenario",
    "dataset",
    "contrast",
    "units",
    "estimate",
    "sd",
    "p",
    "reject",
)


def _merge_table(root: DataRoot, rel: str, spec_name: str, ds: SyntheticDataset) -> bytes:
    """Rows of ``ds`` for its study and set plus existing rows of other studies/sets."""
    from .derived import TABLES, parse_table, table_bytes

    spec = TABLES[spec_name]
    path = root.output_path("derived", rel)
    keep = []
    if path.is_file():
        keep = [
            r
            for r in parse_table(spec, read_bytes(path), data_kind=root.data_kind)
            if (r["study"], r["set"]) != (ds.study, ds.set_name)
        ]
    return table_bytes(spec, [*keep, *ds.tables[spec_name]], root.data_kind)


def write_dataset(root: DataRoot, ds: SyntheticDataset) -> list[Path]:
    """Write a synthetic dataset into a SYNTHETIC root: the key and lists through
    ``paths.write_synthetic_input``, the derived tables (merged with other studies' rows)
    and ``derived/manifest.json``."""
    root.require("SYNTHETIC")
    written = []
    inputs = []
    for rel, data in sorted(ds.files.items()):
        area, _, sub = rel.partition("/")
        path = write_synthetic_input(root, area, sub, data)  # type: ignore[arg-type]
        written.append(path)
        inputs.append(input_entry(root, path))
    tables = {
        "trials.csv": _merge_table(root, "trials.csv", "trials", ds),
        "endpoints.csv": _merge_table(root, "endpoints.csv", "endpoints", ds),
    }
    written += write_files(
        root,
        "derived",
        tables,
        run_name=f"simulate-{ds.study}-{ds.set_name}",
        command=f"av-analysis simulate --scenario {ds.scenario} --write-dataset",
        seeds=ds.seeds,
        inputs=inputs,
        extra={"scenario": ds.scenario, "dataset": ds.index},
    )
    return written


def _oc_combined(root: DataRoot) -> bytes:
    from .simulate import OC_COLUMNS

    base = root.output_path("estimates", "simulation")
    rows: list[tuple[str, ...]] = []
    if base.is_dir():
        for path in sorted(base.glob("*-operating-characteristics.csv")):
            header, body = parse_csv(read_bytes(path))
            if header != OC_COLUMNS:
                raise ValueError(f"{path.name}: unexpected header")
            rows += body
    return csv_bytes(OC_COLUMNS, rows)


def write_simulation(
    root: DataRoot,
    scenarios: Sequence[Scenario],
    seed: str,
    datasets: int,
    *,
    with_dataset: bool = False,
) -> list[Path]:
    """Operating characteristics of ``scenarios`` over ``datasets`` datasets each (and,
    with ``with_dataset``, dataset 0 of each scenario written into the root)."""
    from dataclasses import asdict

    from .simulate import OC_COLUMNS, operating_characteristics, simulate_dataset

    root.require("SYNTHETIC")
    written: list[Path] = []
    files: dict[str, bytes] = {}
    seeds = []
    for sc in scenarios:
        oc, results = operating_characteristics(sc, seed, datasets)
        seeds.append(f"{seed}:{sc.name}:dataset:0-{datasets - 1}")
        files[f"simulation/{sc.name}-operating-characteristics.csv"] = csv_bytes(
            OC_COLUMNS, ([o.row(root.data_kind)[c] for c in OC_COLUMNS] for o in oc)
        )
        files[f"simulation/{sc.name}-datasets.csv"] = csv_bytes(
            DATASET_COLUMNS,
            (
                [
                    root.data_kind,
                    sc.name,
                    str(r.index),
                    r.contrast,
                    str(r.units),
                    csv_cell(r.estimate),
                    csv_cell(r.sd),
                    csv_cell(r.p),
                    csv_cell(r.reject),
                ]
                for r in results
            ),
        )
        files[f"simulation/{sc.name}-scenario.json"] = json_bytes(
            {
                "data_kind": root.data_kind,
                "scenario": {**asdict(sc), "variances": dict(sorted(sc.variances.items()))},
                "seed": seed,
                "datasets": datasets,
            }
        )
        if with_dataset:
            written += write_dataset(root, simulate_dataset(sc, seed))
    for rel, data in files.items():
        write_output(root, "estimates", rel, data, root.data_kind)
    files["simulation/operating-characteristics.csv"] = _oc_combined(root)
    names = "-".join(s.name for s in scenarios)
    written += write_files(
        root,
        "estimates",
        files,
        run_name=f"simulate-{names}"
        if len(names) <= 80
        else f"simulate-{len(scenarios)}-scenarios",
        command=f"av-analysis simulate --scenario {','.join(s.name for s in scenarios)} "
        f"--datasets {datasets} --seed {seed}",
        seeds=seeds,
        inputs=[],
    )
    return written
