"""Power-check configuration: JSON document, its schema and the parsed form.

The schema ``power-check-config.schema.json`` is generated from :func:`config_schema`
(published through ``power_check.SCHEMAS``, committed under ``analysis/schema/``) and
:func:`load_config` validates a document against the committed file, then applies the
checks a JSON Schema cannot express (unique names, cell targets inside (0, 1), a scenario
that carries the target effect for every primary contrast).

The committed example ``analysis/examples/power-check/planning.json`` holds the planning
values (``basis: planning``). The pilot-informed configuration of the freeze
(``basis: pilot``) is written from pilot summaries and kept in restricted storage.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from .._paths import data_root
from ..derived import SCHEMA_ID_BASE
from ..fileio import canonical_json_bytes, read_bytes, sha256_bytes

CONFIG_SCHEMA: Final = "power-check-config.schema.json"
CONFIG_FORMAT: Final = "av-analysis/power-check-config"
CONFIG_FORMAT_VERSION: Final = 1
DEFAULT_REPLICATES: Final = 2000
MIN_REPLICATES: Final = 10
LOGISTIC: Final = "logistic-normal"
UNIT: Final = "unit-normal"
ENGINES: Final = (LOGISTIC, UNIT)
LATENT_LEVELS: Final = ("central", "pessimistic")
# Unit-level contrasts of each study (the primary contrasts).
PRIMARY: Final[dict[str, tuple[str, ...]]] = {"A": ("A3-A2",), "B": ("C", "S")}
# Study A sizes are whole blocks of 6 batches; Study B sizes are even (balanced families).
SIZE_STEP: Final[dict[str, int]] = {"A": 6, "B": 2}
NAME_RE: Final = r"^[a-z0-9][a-z0-9._-]{0,39}$"
SEED_RE: Final = r"^DEMO-[A-Za-z0-9._-]{1,64}$"

Study = Literal["A", "B"]
Engine = Literal["logistic-normal", "unit-normal"]


class ConfigError(ValueError):
    """A power-check configuration that fails the schema or a semantic check."""


@dataclass(frozen=True)
class Heterogeneity:
    """One SD source: latent logit SDs (logistic-normal engine) or unit SDs (unit-normal)."""

    name: str
    engine: Engine
    latent: str | None = None  # "central" or "pessimistic" (logistic-normal)
    multiplier: float = 1.0  # on every latent SD (logistic-normal)
    unit_sd: Mapping[str, float] | None = None  # per primary contrast (unit-normal)
    correlation: float = 0.0  # Study B (C, S) correlation (unit-normal)

    def describe(self) -> str:
        """Short text of the SD setting, e.g. ``latent pessimistic x1`` or ``SD C .22``."""
        if self.engine == LOGISTIC:
            return f"latent {self.latent} x{self.multiplier:g}"
        sds = ", ".join(f"{k} {v:g}" for k, v in (self.unit_sd or {}).items())
        rho = f", rho {self.correlation:g}" if len(self.unit_sd or {}) > 1 else ""
        return f"unit SD {sds}{rho}"


@dataclass(frozen=True)
class EffectScenario:
    """True contrasts of a scenario, in percentage points (for example C 10, S 0)."""

    name: str
    effect_pp: Mapping[str, float]


@dataclass(frozen=True)
class StudyGrid:
    """Sizes, losses, SD sources and scenarios of one study."""

    study: Study
    baseline: float
    sizes: tuple[int, ...]  # batches (A) or pairs (B), ascending
    loss: tuple[float, ...]  # independent per-person loss probabilities
    heterogeneity: tuple[Heterogeneity, ...]
    scenarios: tuple[EffectScenario, ...]
    learners_per_book: int = 4  # Study A only


@dataclass(frozen=True)
class PowerConfig:
    """A validated power-check configuration."""

    basis: str  # "planning" or "pilot"
    description: str
    seed: str  # DEMO- label (seeds.rng)
    replicates: int
    alpha: float
    effect_pp: float  # the difference each primary contrast must detect
    criterion: float  # required power
    studies: tuple[StudyGrid, ...]
    sha256: str  # of the canonical JSON of the document as loaded

    def with_replicates(self, replicates: int) -> PowerConfig:
        """The same configuration with another replicate count (``--replicates``)."""
        if replicates < MIN_REPLICATES:
            raise ConfigError(f"replicates must be at least {MIN_REPLICATES}")
        return replace(self, replicates=replicates)


def planning_example_path() -> Path:
    """``analysis/examples/power-check/planning.json`` (committed, synthetic)."""
    return data_root() / "examples" / "power-check" / "planning.json"


def is_planning_example(doc: object) -> bool:
    """True if ``doc`` equals the committed planning example (compared as parsed JSON)."""
    path = planning_example_path()
    return path.is_file() and json.loads(read_bytes(path)) == doc


# ---------------------------------------------------------------------------------------
# Schema


def _name(what: str) -> dict[str, Any]:
    return {"type": "string", "pattern": NAME_RE, "description": what}


def _heterogeneity(study: Study) -> dict[str, Any]:
    contrasts = PRIMARY[study]
    latent = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "engine", "latent"],
        "properties": {
            "name": _name("Name of the SD source (unique within the study; part of the seed)."),
            "engine": {"const": LOGISTIC},
            "latent": {
                "enum": list(LATENT_LEVELS),
                "description": "Latent logit SD set of the planning model (simulate.py "
                f"{study}_CENTRAL_SD or {study}_PESSIMISTIC_SD).",
            },
            "multiplier": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 5,
                "default": 1.0,
                "description": "Multiplies every latent SD (heterogeneity stress).",
            },
        },
    }
    unit_props: dict[str, Any] = {
        "name": _name("Name of the SD source (unique within the study; part of the seed)."),
        "engine": {"const": UNIT},
        "unit_sd": {
            "type": "object",
            "additionalProperties": False,
            "required": list(contrasts),
            "properties": {
                c: {"type": "number", "exclusiveMinimum": 0, "maximum": 1} for c in contrasts
            },
            "description": "SD of the unit differences (proportion scale), for example "
            "from the pilot.",
        },
    }
    if study == "B":
        unit_props["correlation"] = {
            "type": "number",
            "minimum": -1,
            "maximum": 1,
            "default": 0.0,
            "description": "Correlation of a pair's C and S differences.",
        }
    unit = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "engine", "unit_sd"],
        "properties": unit_props,
    }
    return {
        "type": "array",
        "minItems": 1,
        "items": {"oneOf": [latent, unit]},
        "description": "SD sources; the engine follows from the source (latent SDs: "
        "logistic-normal trial model; unit SDs: unit-level normal model).",
    }


def _study(study: Study) -> dict[str, Any]:
    contrasts = PRIMARY[study]
    step = SIZE_STEP[study]
    unit = "batches" if study == "A" else "pairs"
    props: dict[str, Any] = {
        "baseline": {
            "type": "number",
            "exclusiveMinimum": 0,
            "exclusiveMaximum": 1,
            "description": "Mean accuracy of the reference cells (A1, A2; assigned member "
            "on dictionary items).",
        },
        "sizes": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "integer", "minimum": step, "maximum": 600, "multipleOf": step},
            "description": f"Planned {unit} (grid; multiples of {step}).",
        },
        "loss": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "number", "minimum": 0, "exclusiveMaximum": 1},
            "description": "Independent per-person loss probabilities (MCAR).",
        },
        "heterogeneity": _heterogeneity(study),
        "scenarios": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "effect_pp"],
                "properties": {
                    "name": _name("Scenario name (unique within the study)."),
                    "effect_pp": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(contrasts),
                        "properties": {
                            c: {"type": "number", "minimum": -50, "maximum": 50} for c in contrasts
                        },
                        "description": "True contrasts in percentage points.",
                    },
                },
            },
        },
    }
    if study == "A":
        props["learners_per_book"] = {
            "type": "integer",
            "minimum": 1,
            "maximum": 64,
            "description": "Learners per book (planning: 4).",
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(props),
        "properties": props,
    }


def config_schema() -> dict[str, Any]:
    """JSON Schema of a power-check configuration."""
    props: dict[str, Any] = {
        "format": {"const": CONFIG_FORMAT},
        "format_version": {"const": CONFIG_FORMAT_VERSION},
        "basis": {
            "enum": ["planning", "pilot"],
            "description": "planning: the committed synthetic example; pilot: SDs and loss "
            "informed by pilot summaries (kept in restricted storage, never committed).",
        },
        "description": {"type": "string", "minLength": 1, "maxLength": 400},
        "seed": {
            "type": "string",
            "pattern": SEED_RE,
            "description": "DEMO- seed label; every replicate draws from seeds.rng(seed, "
            "'power-check', study, source, scenario, loss, size, replicate).",
        },
        "replicates": {
            "type": "integer",
            "minimum": MIN_REPLICATES,
            "maximum": 100000,
            "default": DEFAULT_REPLICATES,
            "description": "Replicates per cell; 1,000-2,000 for a decision run.",
        },
        "alpha": {"type": "number", "exclusiveMinimum": 0, "maximum": 0.5},
        "effect_pp": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 50,
            "description": "Difference (percentage points) each primary contrast must detect.",
        },
        "criterion": {
            "type": "number",
            "exclusiveMinimum": 0,
            "exclusiveMaximum": 1,
            "description": "Required power (point estimate) for every primary contrast.",
        },
        "studies": {
            "type": "object",
            "additionalProperties": False,
            "minProperties": 1,
            "properties": {"A": _study("A"), "B": _study("B")},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID_BASE + CONFIG_SCHEMA,
        "title": "Power-check configuration (python -m av_analysis.power_check run --config)",
        "description": "Grid, losses, SD sources and scenarios of the freeze power check "
        "(#309, analysis/docs/power-check.md). Synthetic simulation input; a pilot-informed "
        "configuration stays in restricted storage.",
        "type": "object",
        "additionalProperties": False,
        "required": [k for k in props if k != "replicates"],
        "properties": props,
    }


# ---------------------------------------------------------------------------------------
# Loading


def _validator() -> Draft202012Validator:
    from ..schemas import validator

    return validator(CONFIG_SCHEMA)


def _heterogeneity_of(doc: Mapping[str, Any]) -> Heterogeneity:
    if doc["engine"] == LOGISTIC:
        return Heterogeneity(
            doc["name"], LOGISTIC, latent=doc["latent"], multiplier=float(doc.get("multiplier", 1))
        )
    return Heterogeneity(
        doc["name"],
        UNIT,
        unit_sd={k: float(v) for k, v in doc["unit_sd"].items()},
        correlation=float(doc.get("correlation", 0)),
    )


def _cells(study: str, baseline: float, effects: Mapping[str, float]) -> list[float]:
    if study == "A":
        return [baseline, baseline + effects["A3-A2"] / 100.0]
    c, s = effects["C"] / 100.0, effects["S"] / 100.0
    return [baseline, baseline + c, baseline + s, baseline + c + s]


def _study_of(study: Study, doc: Mapping[str, Any], effect_pp: float) -> StudyGrid:
    het = tuple(_heterogeneity_of(h) for h in doc["heterogeneity"])
    scenarios = tuple(EffectScenario(s["name"], dict(s["effect_pp"])) for s in doc["scenarios"])
    for what, names in (
        ("SD source", [h.name for h in het]),
        ("scenario", [s.name for s in scenarios]),
    ):
        if len(set(names)) != len(names):
            raise ConfigError(f"study {study}: {what} names must be unique")
    for s in scenarios:
        if not all(0.0 < p < 1.0 for p in _cells(study, doc["baseline"], s.effect_pp)):
            raise ConfigError(f"study {study}, scenario {s.name}: a cell target leaves (0, 1)")
    for contrast in PRIMARY[study]:
        if not any(math.isclose(s.effect_pp[contrast], effect_pp) for s in scenarios):
            raise ConfigError(
                f"study {study}: no scenario has {contrast} = effect_pp ({effect_pp:g} pp)"
            )
    return StudyGrid(
        study=study,
        baseline=float(doc["baseline"]),
        sizes=tuple(sorted(int(n) for n in doc["sizes"])),
        loss=tuple(sorted(float(q) for q in doc["loss"])),
        heterogeneity=het,
        scenarios=scenarios,
        learners_per_book=int(doc.get("learners_per_book", 4)),
    )


def parse_config(doc: object) -> PowerConfig:
    """Validate a configuration document (schema, then semantic checks)."""
    error = best_match(_validator().iter_errors(doc))
    if error is not None:
        where = "/".join(str(p) for p in error.absolute_path) or "(document)"
        raise ConfigError(
            f"configuration does not match {CONFIG_SCHEMA} at {where}: {error.message}"
        )
    assert isinstance(doc, dict)
    effect = float(doc["effect_pp"])
    studies = tuple(
        _study_of(name, doc["studies"][name], effect)
        for name in ("A", "B")
        if name in doc["studies"]
    )
    return PowerConfig(
        basis=doc["basis"],
        description=doc["description"],
        seed=doc["seed"],
        replicates=int(doc.get("replicates", DEFAULT_REPLICATES)),
        alpha=float(doc["alpha"]),
        effect_pp=effect,
        criterion=float(doc["criterion"]),
        studies=studies,
        sha256=sha256_bytes(canonical_json_bytes(doc)),
    )


def load_document(path: Path) -> object:
    """The JSON document of a configuration file."""
    try:
        return json.loads(read_bytes(path))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read configuration {path.name}: {exc}") from exc


def load_config(path: Path) -> PowerConfig:
    """Read and validate a configuration file."""
    return parse_config(load_document(path))
