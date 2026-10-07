"""Qualified #26 bank handoff to the dyad package builder (#13) and Study B menus (#70).

`qualified_dyad_bank(bank_dir, expected_bank_sha256=...)` is the only route from a built
bank directory to a package-builder input that records the #26 manifest identity:

1. `verify_bank` passes (schema, manifest derived from the files, bank hash and
   `bank-sha256.txt`, provenance, every option re-rendered against its waveform hash and
   stored WAV, distinct waveforms, all 1,920 different-atom pairs per profile);
2. the bank hash equals the independently provisioned pin (the register value);
3. the bank is `complete` and has no reserve-rule amendments: an amended bank changes
   the effective menu (`bank_manifest.effective_menu`) without changing the bank hash,
   and neither the package format nor the Unity menu applies amendments yet, so it is
   refused rather than packaged with the wrong shown options;
4. `manifest.to_dyad_bank` converts it with `handoff` (format, version, bank hash) and
   each option's `file_sha256`.

The package builder then writes `bank: {format: "av-banks/bank-manifest",
format_version: 1, bank_sha256}` and checks every written WAV against the bank's file
hash. A provisional `av-sound/provisional-bank` input keeps its own format. Building a
package, rendering messages and composition stay outside `av_banks`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final

from av_sound.dyad_bank import DyadBank

from av_banks.layout import BankLayout
from av_banks.manifest import read_amendments, read_manifest, to_dyad_bank
from av_banks.verify import verify_bank

E_HANDOFF: Final = "E_HANDOFF"
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")


class HandoffError(ValueError):
    """A bank directory cannot be handed to the package builder; `.code` is `E_HANDOFF`."""

    code = E_HANDOFF

    def __init__(self, message: str, problems: tuple[str, ...] = ()) -> None:
        super().__init__(f"{E_HANDOFF}: {message}")
        self.problems = problems


def qualified_dyad_bank(
    bank_dir: str | os.PathLike[str],
    *,
    expected_bank_sha256: str,
) -> DyadBank:
    """The verified, unamended #26 bank at `bank_dir` as a qualified `DyadBank`.

    `expected_bank_sha256` is the bank hash provisioned independently of the directory
    (register or run-sheet value). Raises `HandoffError` on any verification problem.
    """
    if not _SHA256_RE.fullmatch(expected_bank_sha256):
        raise HandoffError("expected_bank_sha256 must be a lowercase SHA-256")
    layout = BankLayout(Path(bank_dir))
    report = verify_bank(layout.root)
    if not report.ok:
        raise HandoffError(
            f"bank {layout.bank_id} does not verify ({len(report.problems)} problems)",
            tuple(report.problems),
        )
    manifest = read_manifest(layout.root)
    found = manifest.bank_sha256()
    if found != report.bank_sha256 or found != expected_bank_sha256:
        raise HandoffError(f"bank {manifest.bank_id} hash {found} is not the pinned bank hash")
    if manifest.status != "complete":
        raise HandoffError(f"bank {manifest.bank_id} is {manifest.status}; nothing to package")
    if read_amendments(layout.root):
        raise HandoffError(
            f"bank {manifest.bank_id} has reserve-rule amendments; the package and menu "
            "handoff does not apply effective menus yet"
        )
    bank = to_dyad_bank(manifest)
    if bank.handoff is None or bank.handoff.bank_sha256 != expected_bank_sha256:
        raise HandoffError("conversion lost the bank identity")  # pragma: no cover
    return bank
