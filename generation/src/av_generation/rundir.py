"""Run directories: layout, public/restricted policy and the log file of each record type.

```
<runs_root>/<run_id>/
  run-manifest.json        RunManifest (records the method map: restricted)
  config.json              BatchConfig (A) or the bank-run config (B; #26)
  generation-config.json   GenerationConfig the run uses (genconfig; its hash is the config hash)
  dry-run-plan.json        DryRunPlan of a synthetic dry run (#22)
  logs/<name>.jsonl        one append-only JSONL file per record type (LOG_FILES)
  store/                   VocabularyStore root of the run's books (A)
  audio/                   rendered WAV cache, named <file_sha256>.wav (never in git)
  audit/unmasked/          #24 outputs with method labels (restricted)
  audit/masked/            #24 outputs with anonymous book IDs only
  threshold/               #23 stimulus set, sessions/<session_id>.json and exports
  banks/<bank_id>/         #26 bank outputs (manifest, generation config, amendments, attempts)
```

Policy: only `demo` and `synthetic` runs (IDs starting `DEMO-`) may live inside a git
work tree, and only their small summaries and hashes are committed (`generation/runs/`;
logs, stores and audio go to CI artifacts). `practice`, `pilot` and `confirmatory` runs
must be created outside any git work tree (`E_POLICY`), like store books and fallback
sets (`av_sound.fallback.inside_work_tree`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from av_sound.fallback import inside_work_tree

from av_generation.ids import (
    DEMO_PREFIX,
    ID_RE,
    PUBLIC_RUN_KINDS,
    RunKind,
)

MANIFEST_NAME: Final = "run-manifest.json"
CONFIG_NAME: Final = "config.json"
GENERATION_CONFIG_NAME: Final = "generation-config.json"
DRY_RUN_PLAN_NAME: Final = "dry-run-plan.json"

LOG_FILES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "slot": "logs/slots.jsonl",
        "slot_refusal": "logs/slot-refusals.jsonl",
        "llm_request": "logs/llm-requests.jsonl",
        "rating": "logs/ratings.jsonl",
        "decision": "logs/decisions.jsonl",
        "commit": "logs/commits.jsonl",
        "fallback_scan": "logs/fallback-scans.jsonl",
        "play": "logs/plays.jsonl",
        "timing": "logs/timing.jsonl",
        "threshold_trial": "logs/threshold-trials.jsonl",
    }
)
"""Record type (`record` value) -> path relative to the run directory."""

E_POLICY: Final = "E_POLICY"
E_RUN_ID: Final = "E_RUN_ID"
E_EXISTS: Final = "E_EXISTS"


class RunPolicyError(ValueError):
    """A run directory breaks the public/restricted policy or the naming rules."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def check_run_id(run_id: str, kind: RunKind | str) -> str:
    """`DEMO-` IDs for `demo`/`synthetic` runs, and only for them."""
    kind = RunKind(kind)
    if not isinstance(run_id, str) or not ID_RE.fullmatch(run_id):
        raise RunPolicyError(
            E_RUN_ID, f"run ID {run_id!r} must be 3-64 ASCII letters, digits and inner hyphens"
        )
    public = kind in PUBLIC_RUN_KINDS
    if public != run_id.startswith(DEMO_PREFIX):
        raise RunPolicyError(
            E_RUN_ID,
            f"run ID {run_id!r}: {kind.value} runs "
            + ("must" if public else "must not")
            + f" start with {DEMO_PREFIX!r}",
        )
    return run_id


def check_run_location(path: str | os.PathLike[str], kind: RunKind | str) -> None:
    """Refuse a restricted run inside a git work tree or this repository."""
    if RunKind(kind) not in PUBLIC_RUN_KINDS and inside_work_tree(path):
        raise RunPolicyError(
            E_POLICY,
            f"{RunKind(kind).value} runs hold restricted data and cannot be created inside a "
            f"git work tree ({path}); use restricted storage",
        )


@dataclass(frozen=True, slots=True)
class RunLayout:
    """Paths of one run directory (nothing is created by this class)."""

    root: Path
    run_id: str

    @property
    def manifest(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def config(self) -> Path:
        return self.root / CONFIG_NAME

    @property
    def generation_config(self) -> Path:
        return self.root / GENERATION_CONFIG_NAME

    @property
    def dry_run_plan(self) -> Path:
        return self.root / DRY_RUN_PLAN_NAME

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def log(self, record_type: str) -> Path:
        """The JSONL file of a record type, e.g. `layout.log("slot")`."""
        try:
            return self.root / LOG_FILES[record_type]
        except KeyError:
            raise KeyError(f"no log file for record type {record_type!r}") from None

    @property
    def store_dir(self) -> Path:
        return self.root / "store"

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    def audio(self, file_sha256: str) -> Path:
        """Cached canonical WAV named by its file SHA-256."""
        return self.audio_dir / f"{file_sha256}.wav"

    @property
    def audit_unmasked_dir(self) -> Path:
        return self.root / "audit" / "unmasked"

    @property
    def audit_masked_dir(self) -> Path:
        return self.root / "audit" / "masked"

    @property
    def threshold_dir(self) -> Path:
        return self.root / "threshold"

    def threshold_session(self, session_id: str) -> Path:
        return self.threshold_dir / "sessions" / f"{session_id}.json"

    def bank_dir(self, bank_id: str) -> Path:
        return self.root / "banks" / bank_id


def run_layout(runs_root: str | os.PathLike[str], run_id: str) -> RunLayout:
    """The layout of `<runs_root>/<run_id>` (no checks, nothing created)."""
    return RunLayout(Path(runs_root) / run_id, run_id)


def create_run_dir(
    runs_root: str | os.PathLike[str], run_id: str, kind: RunKind | str
) -> RunLayout:
    """Check the policy and create an empty run directory with `logs/`.

    Raises `RunPolicyError` (`E_RUN_ID`, `E_POLICY`, `E_EXISTS` for a non-empty directory).
    """
    check_run_id(run_id, kind)
    layout = run_layout(runs_root, run_id)
    check_run_location(layout.root, kind)
    if layout.root.exists() and any(layout.root.iterdir()):
        raise RunPolicyError(E_EXISTS, f"run directory {layout.root} already exists")
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    return layout


def relative_files(root: str | os.PathLike[str]) -> list[str]:
    """Every file under `root` as a sorted POSIX relative path (platform independent)."""
    base = Path(root)
    return sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())
