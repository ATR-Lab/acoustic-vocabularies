"""Published JSON Schemas of the analysis stack (``analysis/schema/``).

All schemas are generated from the Python contracts (single source of truth) and
committed; ``av-analysis schemas --check`` (and the tests) fail when a committed file
differs from the generated one, ``--write`` regenerates them.

* ``<table>-row.schema.json``: one typed row of each reconciled and derived table
  (``derived.TABLES``).
* ``data-root.schema.json``: the data-root marker (``paths``).
* ``exit-manifest.schema.json``: raw-file hashes saved at the end of a visit (input;
  producer: data logging #72 / operator console #73, **Pending** agreement).
* ``reconciliation.schema.json``: the per-visit report of #33.
* ``outputs-manifest.schema.json``: ``<area>/manifest.json`` of every output area.
* ``glmm-log.schema.json``: the GLMM fallback-ladder log of #34.

Issue modules publish further schemas without editing this module: any ``av_analysis``
module may define ``SCHEMAS``, a mapping from file name (``<name>.schema.json``) to a
function returning the schema document; :func:`schema_documents` collects them from every
module (sorted by module name) and refuses duplicate names. For example #34 can add an
operating-characteristics row schema in ``simulate`` and #35 a dashboard-data schema in
``monitoring``.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator

from ._paths import schema_dir
from .codes import CHECKS, CODES, SUSPENSION_TITLES
from .derived import (
    PERSON_RE,
    SCHEMA_ID_BASE,
    SHA256_RE,
    TABLES,
    TOKEN_RE,
    UNIT_RE,
    VISIT_ID_RE,
    row_schema,
)
from .fileio import json_bytes
from .glmm import ATTEMPT_STATUS, GLMM_LOG_FORMAT, GLMM_LOG_FORMAT_VERSION, LADDER
from .paths import MARKER_FORMAT, MARKER_FORMAT_VERSION, OUTPUT_AREAS
from .vocab import ALL_VISITS, CHECK_STATUS, DATA_KINDS, RECONCILIATION_STATES

DRAFT: Final = "https://json-schema.org/draft/2020-12/schema"
RECONCILIATION_FORMAT: Final = "av-analysis/reconciliation"
RECONCILIATION_FORMAT_VERSION: Final = 1
EXIT_MANIFEST_FORMAT: Final = "av-analysis/exit-manifest"
EXIT_MANIFEST_FORMAT_VERSION: Final = 1
OUTPUTS_MANIFEST_FORMAT: Final = "av-analysis/outputs-manifest"
OUTPUTS_MANIFEST_FORMAT_VERSION: Final = 1

_SHA: Final = {"type": "string", "pattern": SHA256_RE}
_DATA_KIND: Final = {"enum": list(DATA_KINDS), "description": "Watermark (paths)."}
_ANALYZER: Final = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "version"],
    "properties": {"name": {"const": "av-analysis"}, "version": {"type": "string"}},
}


def _strict(name: str, title: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "$schema": DRAFT,
        "$id": SCHEMA_ID_BASE + name,
        "title": title,
        "description": description,
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _file_list(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["path", "bytes", "sha256"],
            "properties": {
                "path": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$",
                    "description": "POSIX path relative to the folder or data root.",
                },
                "bytes": {"type": "integer", "minimum": 0},
                "sha256": _SHA,
            },
        },
    }


def data_root_schema() -> dict[str, Any]:
    return _strict(
        "data-root.schema.json",
        "Data-root marker (av-data-root.json)",
        "Marks a directory as an analysis data root and fixes its data kind: synthetic "
        "outputs are written only into SYNTHETIC roots, real outputs only into REAL roots "
        "outside any committable git path. See analysis/src/av_analysis/paths.py.",
        {
            "format": {"const": MARKER_FORMAT},
            "format_version": {"const": MARKER_FORMAT_VERSION},
            "data_kind": _DATA_KIND,
            "study": {"enum": ["A", "B", "both"]},
            "set": {"enum": ["pilot", "confirmatory", "both"]},
            "label": {
                "type": "string",
                "pattern": r"^[A-Za-z0-9._:-]{1,80}$",
                "description": "DEMO-... for SYNTHETIC roots; never DEMO- for REAL roots.",
            },
        },
    )


def _export_source() -> dict[str, Any]:
    fields: dict[str, Any] = {
        "format": {
            "type": "string",
            "pattern": TOKEN_RE,
            "description": "ExportBundle schema_version (e.g. data-export-provisional-1).",
        },
        "export_id": {"type": "string", "pattern": TOKEN_RE},
        "manifest_sha256": {
            **_SHA,
            "description": "SHA-256 of the original export manifest bytes (supplied out of "
            "band, as the producer requires to reopen an export).",
        },
        "protocol_version": {"type": "string", "minLength": 1},
        "build_sha256": _SHA,
        "headers_qualified": {"type": "boolean"},
        "unacknowledged_torn_tail": {"type": "boolean"},
        "record_count": {"type": "integer", "minimum": 0},
        "trial_rows": {"type": "integer", "minimum": 0},
        "exposure_rows": {"type": "integer", "minimum": 0},
    }
    return {
        "type": ["object", "null"],
        "description": "Projection of the station's ExportBundle manifest (#72, "
        "docs/data/README.md) this folder was imported from; null only for folders written "
        "by the synthetic generator. A REAL root requires it.",
        "additionalProperties": False,
        "required": list(fields),
        "properties": fields,
    }


def exit_manifest_schema() -> dict[str, Any]:
    return _strict(
        "exit-manifest.schema.json",
        "Exit manifest of a raw visit folder (raw/<visit_id>/exit-manifest.json)",
        "SHA-256 and size of every raw file of a visit, saved when the session closes "
        "(Common procedures section 7: save and hash raw logs at exit). Check C1 compares "
        "the files with it. A documented projection of the data logger's export manifest "
        "(#72 ExportBundle; field map in docs/interfaces/analysis.md), written when an "
        "export is imported into a data root (agreement Pending, #72/#73); the synthetic "
        "log generator (#33) writes it with source null.",
        {
            "format": {"const": EXIT_MANIFEST_FORMAT},
            "format_version": {"const": EXIT_MANIFEST_FORMAT_VERSION},
            "data_kind": _DATA_KIND,
            "visit_id": {"type": "string", "pattern": VISIT_ID_RE},
            "session_id": {"type": "string", "pattern": TOKEN_RE},
            "station_id": {"type": ["string", "null"], "pattern": TOKEN_RE},
            "closed": {
                "enum": ["complete", "interrupted"],
                "description": "interrupted: partial logs preserved after a stop or crash "
                "(an unacknowledged torn tail, or no visit_complete record).",
            },
            "source": _export_source(),
            "files": _file_list("Every other file of the folder, sorted by path."),
        },
    )


def reconciliation_schema() -> dict[str, Any]:
    check_ids = [c.id for c in CHECKS]
    check_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["check", "name", "status", "discrepancies", "unresolved"],
        "properties": {
            "check": {"enum": check_ids},
            "name": {"enum": [c.name for c in CHECKS]},
            "status": {"enum": list(CHECK_STATUS)},
            "discrepancies": {"type": "integer", "minimum": 0},
            "unresolved": {"type": "integer", "minimum": 0},
        },
    }
    discrepancy = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "seq",
            "check",
            "code",
            "rows",
            "deviation_id",
            "resolved",
            "suspension_event",
            "detail",
        ],
        "properties": {
            "seq": {"type": "integer", "minimum": 1},
            "check": {"enum": check_ids},
            "code": {"enum": [c.code for c in CODES]},
            "rows": {"type": "array", "items": {"type": "string", "pattern": r"^[^|]+$"}},
            "deviation_id": {"type": ["string", "null"], "pattern": TOKEN_RE},
            "resolved": {"type": "boolean"},
            "suspension_event": {"enum": [*SUSPENSION_TITLES, None]},
            "detail": {"type": "string", "minLength": 1},
        },
    }
    summary = {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "discrepancies", "unresolved", "suspension_events"],
        "properties": {
            "status": {"enum": [s for s in RECONCILIATION_STATES if s != "not_run"]},
            "discrepancies": {"type": "integer", "minimum": 0},
            "unresolved": {"type": "integer", "minimum": 0},
            "suspension_events": {
                "type": "array",
                "items": {"enum": list(SUSPENSION_TITLES)},
                "uniqueItems": True,
            },
        },
    }
    return _strict(
        "reconciliation.schema.json",
        "Per-visit reconciliation report (reconciled/<visit_id>/reconciliation.json)",
        "Result of checks C1-C8 for one visit (#33). Deterministic: the same inputs give "
        "identical bytes (no run time, sorted inputs). Never contains accuracy, response, "
        "response-time, rating or condition fields (masking policy 'masked').",
        {
            "format": {"const": RECONCILIATION_FORMAT},
            "format_version": {"const": RECONCILIATION_FORMAT_VERSION},
            "data_kind": _DATA_KIND,
            "analyzer": _ANALYZER,
            "study": {"enum": ["A", "B"]},
            "set": {"enum": ["pilot", "confirmatory"]},
            "unit_id": {"type": "string", "pattern": UNIT_RE},
            "person_id": {"type": "string", "pattern": PERSON_RE},
            "visit": {"enum": list(ALL_VISITS)},
            "visit_id": {"type": "string", "pattern": VISIT_ID_RE},
            "session_id": {"type": ["string", "null"], "pattern": TOKEN_RE},
            "station_id": {"type": ["string", "null"], "pattern": TOKEN_RE},
            "inputs": _file_list("Every file read (raw and reference), relative to the root."),
            "raw_unchanged": {
                "type": "boolean",
                "description": "Raw-file SHA-256 values identical before and after the run.",
            },
            "checks": {
                "type": "array",
                "items": check_item,
                "minItems": len(CHECKS),
                "maxItems": len(CHECKS),
                "description": "One entry per check, in order C1..C8.",
            },
            "discrepancies": {"type": "array", "items": discrepancy},
            "summary": summary,
        },
    )


def outputs_manifest_schema() -> dict[str, Any]:
    return _strict(
        "outputs-manifest.schema.json",
        "Outputs manifest (<area>/manifest.json)",
        "SHA-256 of every output file of an area and of the inputs it was computed from. "
        "The analysis report (#34) cites the manifest hash; data locks archive it.",
        {
            "format": {"const": OUTPUTS_MANIFEST_FORMAT},
            "format_version": {"const": OUTPUTS_MANIFEST_FORMAT_VERSION},
            "data_kind": _DATA_KIND,
            "area": {"enum": list(OUTPUT_AREAS)},
            "analyzer": _ANALYZER,
            "seeds": {
                "type": "array",
                "items": {"type": "string", "pattern": TOKEN_RE},
                "description": "Seed labels used by the outputs (seeds module).",
            },
            "inputs": _file_list("Input files, relative to the data root."),
            "files": _file_list("Output files, relative to the area."),
        },
    )


def glmm_log_schema() -> dict[str, Any]:
    attempt = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "step",
            "rung",
            "formula",
            "status",
            "converged",
            "singular",
            "messages",
            "reason",
        ],
        "properties": {
            "step": {"type": "integer", "minimum": 1},
            "rung": {"enum": list(LADDER)},
            "formula": {"type": ["string", "null"]},
            "status": {"enum": list(ATTEMPT_STATUS)},
            "converged": {"type": ["boolean", "null"]},
            "singular": {"type": ["boolean", "null"]},
            "messages": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
    }
    engine = {
        "type": ["object", "null"],
        "additionalProperties": False,
        "required": ["name", "r_version", "lme4_version", "optimizer"],
        "properties": {
            "name": {"type": "string"},
            "r_version": {"type": "string"},
            "lme4_version": {"type": "string"},
            "optimizer": {"type": "string"},
        },
    }
    return _strict(
        "glmm-log.schema.json",
        "GLMM fallback-ladder log (estimates/glmm/<model_id>.json)",
        "Every rung of the analysis plan section 5 ladder that was tried or skipped, in "
        "order full, no_correlations, no_dyad_role_slope, no_participant_teaching_slope, "
        "descriptive (#34).",
        {
            "format": {"const": GLMM_LOG_FORMAT},
            "format_version": {"const": GLMM_LOG_FORMAT_VERSION},
            "data_kind": _DATA_KIND,
            "study": {"enum": ["A", "B"]},
            "model_id": {"type": "string", "pattern": TOKEN_RE},
            "engine": engine,
            "data_sha256": _SHA,
            "attempts": {"type": "array", "items": attempt, "minItems": 1},
            "final_rung": {"enum": list(LADDER)},
        },
    )


SchemaFactory = Callable[[], dict[str, Any]]
_NOT_SCANNED: Final = frozenset({"__main__", "cli", "schemas"})


def module_schemas() -> dict[str, SchemaFactory]:
    """File name -> factory, from the ``SCHEMAS`` mapping of every ``av_analysis`` module."""
    package = importlib.import_module(__package__ or "av_analysis")
    found: dict[str, SchemaFactory] = {}
    names = sorted(m.name for m in pkgutil.iter_modules(package.__path__))
    for name in names:
        if name in _NOT_SCANNED:
            continue
        module = importlib.import_module(f"{package.__name__}.{name}")
        provided: Mapping[str, SchemaFactory] | None = getattr(module, "SCHEMAS", None)
        for file_name, factory in (provided or {}).items():
            if file_name in found:
                raise ValueError(f"schema {file_name} published twice ({name})")
            found[file_name] = factory
    return found


def core_schemas() -> dict[str, dict[str, Any]]:
    """The schemas generated by this module (tables, data root, manifests, report, log)."""
    docs = {t.schema_name: row_schema(t) for t in TABLES.values()}
    docs["data-root.schema.json"] = data_root_schema()
    docs["exit-manifest.schema.json"] = exit_manifest_schema()
    docs["reconciliation.schema.json"] = reconciliation_schema()
    docs["outputs-manifest.schema.json"] = outputs_manifest_schema()
    docs["glmm-log.schema.json"] = glmm_log_schema()
    return docs


def schema_documents() -> dict[str, dict[str, Any]]:
    """File name -> schema document, for every published schema (core and modules)."""
    docs = core_schemas()
    for name, factory in module_schemas().items():
        if name in docs:
            raise ValueError(f"schema {name} is already a core schema")
        docs[name] = factory()
    for name, doc in docs.items():
        if not name.endswith(".schema.json") or doc.get("$id") != SCHEMA_ID_BASE + name:
            raise ValueError(f"schema {name}: file name must end .schema.json and match $id")
        Draft202012Validator.check_schema(doc)
    return dict(sorted(docs.items()))


def check_schema_files(directory: Path | None = None) -> list[str]:
    """Problems: committed schema files that differ from the generated ones, or extras."""
    directory = schema_dir() if directory is None else directory
    docs = schema_documents()
    problems = []
    for name, doc in docs.items():
        path = directory / name
        if not path.is_file():
            problems.append(f"{name}: missing")
        elif path.read_bytes() != json_bytes(doc):
            problems.append(f"{name}: differs from the generated schema (run schemas --write)")
    for path in sorted(directory.glob("*.schema.json")):
        if path.name not in docs:
            problems.append(f"{path.name}: not generated (no core schema or module SCHEMAS entry)")
    return problems


def write_schema_files(directory: Path | None = None) -> list[str]:
    """Write every generated schema; returns the file names written."""
    directory = schema_dir() if directory is None else directory
    directory.mkdir(parents=True, exist_ok=True)
    for name, doc in schema_documents().items():
        (directory / name).write_bytes(json_bytes(doc))
    return list(schema_documents())


@cache
def validator(name: str) -> Draft202012Validator:
    """Validator for a committed schema file, e.g. ``validator("reconciliation.schema.json")``."""
    doc = json.loads((schema_dir() / name).read_text(encoding="utf-8"))
    return Draft202012Validator(doc)
