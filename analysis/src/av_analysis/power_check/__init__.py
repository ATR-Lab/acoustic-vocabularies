"""Power check for the protocol freeze (#309; ``analysis/docs/power-check.md``).

Each primary contrast must reach the criterion power (planning: 80%) for the target
difference (10 percentage points) with SDs and loss informed by the pilot. The check
simulates a grid of sizes (Study A batches, Study B pairs) x losses x SD sources x
scenarios with two engines and the analysis plan's tests:

* **logistic-normal**: the planning trial model of ``simulate`` (``draw_a``/``draw_b``,
  latent SD sets, ``intercept`` calibration), sized freely, without faults or late visits;
* **unit-normal**: unit differences drawn directly from pilot-style unit SDs.

``python -m av_analysis.power_check run --config X --out DIR [--replicates N]`` writes
``power.csv``, ``power.md`` and ``verdict.json`` (:mod:`.run`). The configuration is a
JSON document validated by ``power-check-config.schema.json`` (:mod:`.config`); the
committed example ``analysis/examples/power-check/planning.json`` holds the planning
values, the pilot-informed configuration lives in restricted storage.
"""

from __future__ import annotations

from typing import Final

from .config import (
    CONFIG_SCHEMA,
    ConfigError,
    EffectScenario,
    Heterogeneity,
    PowerConfig,
    StudyGrid,
    config_schema,
    is_planning_example,
    load_config,
    parse_config,
    planning_example_path,
)
from .engines import (
    Decision,
    balanced_structured,
    batch_drop_probability,
    decide,
    latent_scenario,
    replicate_function,
)
from .run import (
    POWER_COLUMNS,
    Cell,
    PowerRow,
    cells,
    run_cell,
    run_config,
    verdict,
    write_outputs,
)

SCHEMAS: Final = {CONFIG_SCHEMA: config_schema}

__all__ = [
    "CONFIG_SCHEMA",
    "POWER_COLUMNS",
    "SCHEMAS",
    "Cell",
    "ConfigError",
    "Decision",
    "EffectScenario",
    "Heterogeneity",
    "PowerConfig",
    "PowerRow",
    "StudyGrid",
    "balanced_structured",
    "batch_drop_probability",
    "cells",
    "config_schema",
    "decide",
    "is_planning_example",
    "latent_scenario",
    "load_config",
    "parse_config",
    "planning_example_path",
    "replicate_function",
    "run_cell",
    "run_config",
    "verdict",
    "write_outputs",
]
