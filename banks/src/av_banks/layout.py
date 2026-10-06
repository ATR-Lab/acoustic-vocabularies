"""Bank directory layout (`rundir.RunLayout.bank_dir(bank_id)`; `bank_manifest` docstring).

```
<run>/banks/<bank_id>/
  manifest.json              the built bank (bank-manifest.schema.json; immutable)
  bank-sha256.txt            the bank hash (bank_manifest.bank_sha256 of manifest.json)
  generation-config.json     the GenerationConfig the bank was built under
  permutation.json           byte copy of the unit's package-safe permutation.json
  timing.jsonl               bank, attempt and cell timing events
  amendments.jsonl           reserve-rule amendments (bank-amendment; absent until `amend`)
  attempts/<n>/slots.jsonl   every slot record of attempt n (the attempt's ledger)
  attempts/<n>/slot-refusals.jsonl
  attempts/<n>/attempt.json  attempt summary: status, reason, timing, throughput
  options/P<k>/<atom>-<rank>.wav   the 192 retained options of the attempt used
```

Failed attempts keep their `attempts/<n>/` directory (slots, refusals, summary); only
the attempt used has option WAVs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from av_generation.bank_manifest import AMENDMENTS_NAME, MANIFEST_NAME
from av_generation.rundir import GENERATION_CONFIG_NAME

BANK_HASH_NAME: Final = "bank-sha256.txt"
PERMUTATION_NAME: Final = "permutation.json"
TIMING_NAME: Final = "timing.jsonl"
ATTEMPT_NAME: Final = "attempt.json"
SLOTS_NAME: Final = "slots.jsonl"
REFUSALS_NAME: Final = "slot-refusals.jsonl"


def option_wav(profile: str, atom_id: str, rank: int) -> str:
    """Relative POSIX path of an option WAV, e.g. `options/P1/K-a1-2.wav`."""
    return f"options/{profile}/{atom_id}-{rank}.wav"


def option_id(bank_id: str, profile: str, atom_id: str, rank: int) -> str:
    """Option ID, unique across banks: `<bank_id>.<profile>.<atom>.<rank>`."""
    return f"{bank_id}.{profile}.{atom_id}.{rank}"


@dataclass(frozen=True, slots=True)
class BankLayout:
    """Paths of one bank directory (nothing is created by this class)."""

    root: Path

    @classmethod
    def at(cls, path: str | os.PathLike[str]) -> BankLayout:
        return cls(Path(path))

    @property
    def bank_id(self) -> str:
        return self.root.name

    @property
    def manifest(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def bank_hash(self) -> Path:
        return self.root / BANK_HASH_NAME

    @property
    def generation_config(self) -> Path:
        return self.root / GENERATION_CONFIG_NAME

    @property
    def permutation(self) -> Path:
        return self.root / PERMUTATION_NAME

    @property
    def timing(self) -> Path:
        return self.root / TIMING_NAME

    @property
    def amendments(self) -> Path:
        return self.root / AMENDMENTS_NAME

    def attempt_dir(self, attempt: int) -> Path:
        return self.root / "attempts" / str(attempt)

    def slots(self, attempt: int) -> Path:
        return self.attempt_dir(attempt) / SLOTS_NAME

    def refusals(self, attempt: int) -> Path:
        return self.attempt_dir(attempt) / REFUSALS_NAME

    def attempt_summary(self, attempt: int) -> Path:
        return self.attempt_dir(attempt) / ATTEMPT_NAME

    def option(self, relative: str) -> Path:
        """Absolute path of an option WAV given its manifest `wav` path."""
        return self.root.joinpath(*relative.split("/"))

    def attempts(self) -> tuple[int, ...]:
        """Attempt numbers present on disk, ascending."""
        base = self.root / "attempts"
        if not base.is_dir():
            return ()
        return tuple(sorted(int(p.name) for p in base.iterdir() if p.is_dir() and p.name.isdigit()))
