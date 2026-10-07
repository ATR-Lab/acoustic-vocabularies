"""Shared pieces of the confirmatory campaign: errors, IDs, the campaign layout, schemas.

Campaign directory (restricted storage; `CampaignLayout`):

```
<campaign>/
  plan.json                     bank IDs, dyad slots, seed namespaces, hashes (plan.py)
  freeze-manifest.json          byte copy of the G4 freeze manifest
  generation-config.json        the config every bank is built under
  units/<unit_id>/permutation.json   byte copies of the 72 unit permutations
  seed-check.json               seeds unique and disjoint from the pilot (seed_check.py)
  rebuilds.jsonl                crashed banks rebuilt under a new bank version (runner.py)
  events.jsonl, progress.jsonl  runner events and progress snapshots
  runs/<run_id>/banks/<bank_id>/   one #26 run per bank and version
  verify/<bank_id>-v<version>.json, verification-log.txt, verification.json
  used-seeds.json               the seeds the slot records used, rechecked
  timing.csv, slot-timing.csv   run timing log
  register.csv, register.json   the register (public: hashes and counts only)
  escalation.json               advisor escalation (role, link, date), when needed
  g5b-report.md                 complete and unavailable counts for the G5B owner
  archive/<campaign_id>-banks.tar   deterministic archive of everything above
```
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal

from av_generation.ids import CONFIRMATORY_BANK_RE, DEMO_PREFIX, bank_set
from av_generation.jsonio import read_json
from jsonschema import Draft202012Validator

from av_banks._paths import schema_path
from av_banks.builder import FIRST_VERSION

N_MAIN: Final = 64
"""Main confirmatory dyad slots (Study B protocol §2)."""
N_SPARES: Final = 8
N_BANKS: Final = N_MAIN + N_SPARES
MIN_COMPLETE: Final = N_MAIN
"""Fewer complete banks than this: stop and escalate to the advisor before G5B."""
DEMO_BANK_RE: Final = re.compile(r"DEMO-C[0-9]{3}")
"""Rehearsal bank IDs: `DEMO-C001`..`DEMO-C072`, numbered like the confirmatory IDs."""
CAMPAIGN_ID_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{1,38}[A-Za-z0-9]")
"""Campaign IDs prefix the per-bank run IDs, so they are at most 40 characters."""

E_INPUT: Final = "E_INPUT"
E_FREEZE: Final = "E_FREEZE"
E_UNITS: Final = "E_UNITS"
E_SEEDS: Final = "E_SEEDS"
E_EXISTS: Final = "E_EXISTS"
E_STATE: Final = "E_STATE"
E_LOCKED: Final = "E_LOCKED"
E_VERIFY: Final = "E_VERIFY"
E_COMMIT: Final = "E_COMMIT"

Role = Literal["main", "spare"]


class CampaignError(RuntimeError):
    """The campaign refused a step; `.code` names the rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def campaign_bank_ids(*, demo: bool = False) -> tuple[str, ...]:
    """The 72 bank IDs in dyad-slot sequence: `bank-C001`..`bank-C072` (rehearsal:
    `DEMO-C001`..`DEMO-C072`). 1-64 are the main slots, 65-72 the spares."""
    prefix = "DEMO-C" if demo else "bank-C"
    return tuple(f"{prefix}{n:03d}" for n in range(1, N_BANKS + 1))


def bank_sequence(bank_id: str) -> int:
    """The dyad-slot sequence number 1..72 of a campaign bank ID."""
    if not (CONFIRMATORY_BANK_RE.fullmatch(bank_id) or DEMO_BANK_RE.fullmatch(bank_id)):
        raise CampaignError(E_INPUT, f"{bank_id!r} is not a campaign bank ID (bank-C001..)")
    seq = int(bank_id[6:])
    if not 1 <= seq <= N_BANKS:
        raise CampaignError(E_INPUT, f"{bank_id}: campaign banks are numbered 001..{N_BANKS:03d}")
    return seq


def bank_role(bank_id: str) -> Role:
    """`main` for sequence 1..64, `spare` for 65..72."""
    return "main" if bank_sequence(bank_id) <= N_MAIN else "spare"


def dyad_slot(bank_id: str) -> str:
    """The unit (dyad slot) of a campaign bank: `B-C01`..`B-C64`, spares `B-S01`..`B-S08`
    (the #26 sequence rule, `permutation.expected_unit_id`; rehearsal IDs use the same)."""
    seq = bank_sequence(bank_id)
    return f"B-C{seq:02d}" if seq <= N_MAIN else f"B-S{seq - N_MAIN:02d}"


def campaign_set(bank_id: str) -> str:
    """`confirmatory` for `bank-C...`, `demo` for `DEMO-C...` (anything else raises)."""
    bank_sequence(bank_id)
    return bank_set(bank_id)


def version_slug(bank_version: str) -> str:
    """`1.0.1` -> `1-0-1` (run IDs hold no dots)."""
    return bank_version.replace(".", "-")


def run_id_for(campaign_id: str, bank_id: str, bank_version: str) -> str:
    """The #26 run ID of one bank build: `<campaign>-<bank>` for the first version,
    `<campaign>-<bank>-v<x-y-z>` for a rebuild."""
    base = f"{campaign_id}-{bank_id}"
    return base if bank_version == FIRST_VERSION else f"{base}-v{version_slug(bank_version)}"


def is_demo_campaign(campaign_id: str) -> bool:
    return campaign_id.startswith(DEMO_PREFIX)


@dataclass(frozen=True, slots=True)
class CampaignLayout:
    """Paths of one campaign directory (nothing is created by this class)."""

    root: Path

    @classmethod
    def at(cls, path: str | os.PathLike[str]) -> CampaignLayout:
        return cls(Path(path))

    @property
    def plan(self) -> Path:
        return self.root / "plan.json"

    @property
    def freeze_manifest(self) -> Path:
        return self.root / "freeze-manifest.json"

    @property
    def generation_config(self) -> Path:
        return self.root / "generation-config.json"

    def unit(self, unit_id: str) -> Path:
        return self.root / "units" / unit_id / "permutation.json"

    @property
    def seed_check(self) -> Path:
        return self.root / "seed-check.json"

    @property
    def rebuilds(self) -> Path:
        return self.root / "rebuilds.jsonl"

    @property
    def events(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def progress(self) -> Path:
        return self.root / "progress.jsonl"

    @property
    def lock(self) -> Path:
        return self.root / "runner.lock"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    def run_dir(self, run_id: str) -> Path:
        return self.runs / run_id

    def bank_dir(self, run_id: str, bank_id: str) -> Path:
        return self.runs / run_id / "banks" / bank_id

    @property
    def verify_dir(self) -> Path:
        return self.root / "verify"

    def verify_report(self, bank_id: str, bank_version: str) -> Path:
        return self.verify_dir / f"{bank_id}-v{version_slug(bank_version)}.json"

    @property
    def verification_log(self) -> Path:
        return self.root / "verification-log.txt"

    @property
    def verification(self) -> Path:
        return self.root / "verification.json"

    @property
    def used_seeds(self) -> Path:
        return self.root / "used-seeds.json"

    @property
    def timing(self) -> Path:
        return self.root / "timing.csv"

    @property
    def slot_timing(self) -> Path:
        return self.root / "slot-timing.csv"

    @property
    def register_csv(self) -> Path:
        return self.root / "register.csv"

    @property
    def register_json(self) -> Path:
        return self.root / "register.json"

    @property
    def escalation(self) -> Path:
        return self.root / "escalation.json"

    @property
    def g5b_report(self) -> Path:
        return self.root / "g5b-report.md"

    @property
    def archive_dir(self) -> Path:
        return self.root / "archive"

    def archive(self, campaign_id: str) -> Path:
        return self.archive_dir / f"{campaign_id}-banks.tar"


@cache
def _validator(name: str) -> Draft202012Validator:
    schema = read_json(schema_path(name))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def schema_errors(name: str, doc: Mapping[str, Any]) -> tuple[str, ...]:
    """Sorted schema errors of `doc` against `banks/schema/<name>` (empty when valid)."""
    errors = []
    for err in _validator(name).iter_errors(dict(doc)):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{where}: {err.message}")
    return tuple(sorted(errors))


def require_schema(name: str, doc: Mapping[str, Any]) -> None:
    errors = schema_errors(name, doc)
    if errors:
        raise CampaignError(E_INPUT, f"document does not match {name}: {list(errors[:3])}")
