"""DEMO inputs and a DEMO job file: a rehearsal of the whole unattended run.

`write_demo_job(out, llm_url=...)` writes, under `out/`:

- `inputs/meanings/`, `inputs/prompts/`: copies of the repository's DEMO meaning set and
  committed prompt set (the stage commands load them as they would load restricted ones);
- `inputs/decoding-schema.json`: the recipe schema;
- `inputs/generation-config.json`: a DEMO generation config pinned to those files (0.10
  threshold, the DEMO fallback set);
- `inputs/freeze-manifest.json`: a DEMO draft freeze manifest holding that config's hash
  (`confirmatory.rehearsal.demo_freeze_manifest`; never frozen, no tag);
- `inputs/pilot-units/B-P01..`, `inputs/confirmatory-units/B-C01..B-S08`: DEMO unit
  stand-ins (`confirmatory.rehearsal.demo_unit`; not schedules output);
- `job.json`: a pilot stage on `DEMO-bank-P001..` and a confirmatory DEMO stage limited
  to the first `banks` banks (`DEMO-C001..`; no register, which needs all 72), with the
  job root `job/`.

The job runs against any OpenAI-compatible server at `llm_url`: the real one on the
GPU host (first-hour rehearsal) or `av_generation.mock_llm` (tests). Nothing here is
study material; every ID starts with `DEMO-`.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from av_generation._paths import examples_path
from av_generation.genconfig import GenerationConfig, PromptHashes, build_generation_config
from av_generation.genconfig import fallback_pins as _fallback_pins
from av_generation.jsonio import schema_sha256, write_document
from av_generation.meanings import load_meanings
from av_generation.prompts import default_prompt_set_dir, load_prompt_set
from av_sound._paths import data_root as sound_root
from av_sound._paths import schema_path as sound_schema_path
from av_sound.fallback import load_fallback

from av_banks.confirmatory.common import campaign_bank_ids, dyad_slot
from av_banks.confirmatory.rehearsal import DEMO_THRESHOLD, demo_freeze_manifest, demo_unit
from av_banks.proposer import b_prompt_sha256


def demo_config(meanings_dir: Path, prompts_dir: Path, schema: dict[str, Any]) -> GenerationConfig:
    """A DEMO generation config pinned to a meaning set, a prompt set and a schema."""
    meanings = load_meanings(meanings_dir)
    prompt_set = load_prompt_set(prompts_dir, meanings=meanings)
    fallback = load_fallback(sound_root() / "testvectors" / "fallback" / "demo-manifest.json")
    return build_generation_config(
        "DEMO-unattended-config",
        llm_manifest_sha256=None,
        decoding_schema_sha256=schema_sha256(schema),
        prompts=PromptHashes(prompt_set.a3_sha256, b_prompt_sha256(prompt_set)),
        meanings_sha256=meanings.sha256(),
        separation_threshold=DEMO_THRESHOLD,
        fallback=_fallback_pins(fallback),
    )


def write_demo_job(
    out: str | os.PathLike[str],
    *,
    llm_url: str,
    job_id: str = "DEMO-unattended-01",
    dyads: int = 2,
    spares: int = 1,
    banks: int = 3,
) -> Path:
    """Write the DEMO inputs and `job.json` (module docstring); returns the job file."""
    if not job_id.startswith("DEMO-"):
        raise ValueError("a DEMO job ID starts with 'DEMO-'")
    if not 1 <= banks <= len(campaign_bank_ids(demo=True)):
        raise ValueError("banks must be 1..72")
    base = Path(out).resolve()
    inputs = base / "inputs"
    if inputs.exists():
        raise ValueError(f"{inputs} already exists")
    inputs.mkdir(parents=True)
    meanings = inputs / "meanings"
    prompts = inputs / "prompts"
    shutil.copytree(examples_path("demo-meanings"), meanings)
    shutil.copytree(default_prompt_set_dir(), prompts)
    schema: dict[str, Any] = json.loads(
        sound_schema_path("recipe.schema.json").read_text(encoding="utf-8")
    )
    write_document(inputs / "decoding-schema.json", schema)
    config = demo_config(meanings, prompts, schema)
    config.write(inputs / "generation-config.json")
    write_document(inputs / "freeze-manifest.json", demo_freeze_manifest(config))
    for slot in range(1, dyads + 1):
        unit = demo_unit(f"B-P{slot:02d}")
        unit["set"] = "pilot"
        path = inputs / "pilot-units" / unit["unit_id"] / "permutation.json"
        path.parent.mkdir(parents=True)
        write_document(path, unit)
    for bank_id in campaign_bank_ids(demo=True):
        path = inputs / "confirmatory-units" / dyad_slot(bank_id) / "permutation.json"
        path.parent.mkdir(parents=True)
        write_document(path, demo_unit(dyad_slot(bank_id)))
    common = {
        "meanings": "inputs/meanings",
        "prompts": "inputs/prompts",
        "decoding_schema": "inputs/decoding-schema.json",
    }
    job = {
        "format": "av-banks/unattended-job",
        "format_version": 1,
        "job_id": job_id,
        "root": "job",
        "model": {"url": llm_url},
        "stages": [
            {
                "name": "pilot",
                "kind": "pilot",
                "run_id": f"{job_id}-P",
                "demo": True,
                "units": "inputs/pilot-units",
                "generation_config": "inputs/generation-config.json",
                "dyads": dyads,
                "spares": spares,
                **common,
            },
            {
                "name": "confirmatory",
                "kind": "confirmatory",
                "plan": {
                    "campaign_id": f"{job_id}-C",
                    "generation_config": "inputs/generation-config.json",
                    "freeze_manifest": "inputs/freeze-manifest.json",
                    "units": "inputs/confirmatory-units",
                    "pilot_stages": ["pilot"],
                },
                "only": list(campaign_bank_ids(demo=True)[:banks]),
                "register": False,
                **common,
            },
        ],
    }
    path = base / "job.json"
    write_document(path, job)
    return path
