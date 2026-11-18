"""G4 freeze assembly (#308): one restricted draft from the repository, the GPU host and the
private inputs, and the status table the owner and the advisor sign off on (#25).

Filling the G4 freeze draft (`freeze`, `generation/docs/freeze.md` section 6) took about
twenty `freeze record` calls typed from the GPU host's output. `fill` does it in one step
and checks every value it records (`generation/docs/freeze-assembly.md`):

- it refreshes the `code` and `file` items of the restricted copy of the draft
  (`freeze.refresh_draft`);
- from the LLM manifest after `llm_manifest record-hardware` (#16) it takes the GPU-host
  items `MANIFEST_ITEMS` with the values `freeze build` compares
  (`llm_manifest.freeze_values`). The evidence directory of `evidence.sh` must back them:
  every acceptance criterion that backs an item (`BACKING`) is PASS, the run is not a MOCK
  run, the start-refusal checks exited 2, and the benchmark's runtime and hardware label
  and the acceptance hardware record name the manifest's values. A value that disagrees
  with the committed LLM manifest (`freeze.committed_values`) is refused, as `build`
  refuses it;
- from the vLLM lock (`pip freeze --all` of the server environment; `lock`, run by
  bringup.sh) it takes `runtime.vllm_lock_sha256` (`lock_sha256`: SHA-256 of the sorted
  package lines) once the lock pins the manifest's vLLM (and PyTorch) version;
- from one restricted inputs file (`g4-inputs.schema.json`) it takes
  `separation.evidence_sha256`, `pilot.timing_review` and `pilot.audit_sha256`, each with
  its source, and checks `meanings.sha256` against the frozen config when one is given (a
  `config` item: `freeze build` copies it from the config).

Every value goes through `freeze.record_value`, so the checks of `freeze record` apply; the
filled draft must pass the CI freeze guard (`freeze.freeze_differences`) and is written
all at once or not at all, never inside a git work tree (restricted data).

`status` prints every item as a Markdown table (key, state, value with shortened hashes,
guard, who fills a pending item by role, source). `freeze build --signoff` stays the
sign-off path.

Command line: `python -m av_generation.freeze_assembly fill|status|lock` (`main`).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from av_sound.fallback import inside_work_tree

from av_generation import constants as C
from av_generation import freeze
from av_generation import llm_manifest as lm
from av_generation._schemas import schema_errors
from av_generation.freeze import FreezeError, FreezeValue
from av_generation.genconfig import GenerationConfig
from av_generation.jsonio import (
    CodecError,
    canonical_sha256,
    file_sha256,
    read_json,
    write_document,
)
from av_generation.meanings import load_meanings
from av_generation.records import RecordError

INPUTS_FORMAT: Final = "av-generation/g4-inputs"
SCHEMA: Final = "g4-inputs.schema.json"
"""Schema of the restricted inputs file."""

LOCK_ITEM: Final = "runtime.vllm_lock_sha256"
LOCK_HEADER: Final = "#av "
"""Lines of a lock file that start with this are notes of `lock`, not package lines; they
are not hashed."""
TORCH_CUDA_NOTE: Final = "#av torch.version.cuda: "
MEANINGS_ITEM: Final = "meanings.sha256"

MANIFEST_ITEMS: Final[Mapping[str, str]] = {
    "model.tokenizer_revision": "model.tokenizer_revision",
    "model.weights_sha256": "model.weights_sha256",
    "model.license": "model.license",
    "runtime.vllm_version": "runtime.version",
    "runtime.cuda_version": "hardware.cuda_version",
    "runtime.driver_version": "hardware.driver_version",
    "runtime.gpu": "hardware.gpu and hardware.vram_mib",
    "runtime.precision": "runtime.precision",
    "runtime.max_model_len": "runtime.max_model_len",
    "runtime.chat_template_sha256": "chat_template.sha256",
    "decoding.implementation": "runtime section (llm_manifest.decoding_implementation)",
}
"""GPU-host items the LLM manifest gives (`llm_manifest.freeze_values`) and where."""
GPU_ITEMS: Final[tuple[str, ...]] = (*MANIFEST_ITEMS, LOCK_ITEM)
INPUT_ITEMS: Final[tuple[str, ...]] = (
    "separation.evidence_sha256",
    "pilot.timing_review",
    "pilot.audit_sha256",
)
"""Recorded items of the inputs file (`meanings.sha256` is checked, not recorded)."""


@dataclass(frozen=True, slots=True)
class Backing:
    """An acceptance criterion of `acceptance.md` (#16) and the items it backs."""

    name: str
    phrase: str
    """Lower-case text that identifies the criterion row."""
    items: tuple[str, ...]


BACKING: Final[tuple[Backing, ...]] = (
    Backing(
        "start refusals",
        "weights checksum or revision",
        ("model.tokenizer_revision", "model.weights_sha256", "runtime.chat_template_sha256"),
    ),
    Backing(
        "hardware record",
        "cuda and driver versions",
        ("runtime.vllm_version", "runtime.cuda_version", "runtime.driver_version", "runtime.gpu"),
    ),
    Backing("frozen decoding logged", "benchmark calls log", ("decoding.implementation",)),
    Backing("worst-case p95", "p95 slot latency", ("runtime.precision", "runtime.max_model_len")),
)
"""`model.license` (Hugging Face metadata) and the lock need no acceptance criterion."""

ACCEPTANCE: Final = "acceptance.md"
SUMMARY: Final = "bench/llm_latency_summary.json"
REFUSALS: Final[tuple[str, ...]] = ("refusal-revision", "refusal-weights")
EVIDENCE_REQUIRED: Final[tuple[str, ...]] = (
    ACCEPTANCE,
    SUMMARY,
    *(f"{name}.{ext}" for name in REFUSALS for ext in ("exit", "log")),
)
"""Files of the `evidence.sh` output directory the assembler reads."""

ROLE_GPU: Final = "GPU host"
ROLE_THRESHOLD: Final = "threshold check"
ROLE_PILOT: Final = "pilot panels"
ROLE_OWNER: Final = "owner at G4"
ROLES: Final[Mapping[str, str]] = {
    **{key: ROLE_GPU for key in GPU_ITEMS},
    "separation.evidence_sha256": ROLE_THRESHOLD,
    "pilot.timing_review": ROLE_PILOT,
    "pilot.audit_sha256": ROLE_PILOT,
}
"""Who fills a pending item, by role (every other item: the owner at G4, through the
frozen config and `freeze build`)."""

E_INPUT: Final = freeze.E_INPUT
E_EVIDENCE: Final = "E_EVIDENCE"
E_MOCK: Final = "E_MOCK"
E_MISMATCH: Final = "E_MISMATCH"
E_POLICY: Final = "E_POLICY"

_HEX64: Final = re.compile(r"[0-9a-f]{64}")
_HEX40: Final = re.compile(r"[0-9a-f]{40}")
_PIN: Final = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*===?\s*(\S+)")
_ROW: Final = re.compile(r"\|\s*(PASS|FAIL|PENDING)\s*\|([^|]*)\|(.*)\|\s*")
_TORCH_CUDA: Final = re.compile(r"[!-~]{1,32}")


def role_of(key: str) -> str:
    return ROLES.get(key, ROLE_OWNER)


def _same(a: object, b: object) -> bool:
    return canonical_sha256(a) == canonical_sha256(b)


def _read_object(path: Path, what: str) -> dict[str, Any]:
    try:
        data = read_json(path)
    except (OSError, CodecError) as err:
        raise FreezeError(E_INPUT, f"cannot read the {what} {path.name}: {err}") from err
    if not isinstance(data, dict):
        raise FreezeError(E_INPUT, f"the {what} {path.name} is not a JSON object")
    return data


# ---------------------------------------------------------------------------
# vLLM environment lock


@dataclass(frozen=True, slots=True)
class VllmLock:
    """A lock of the vLLM server environment: `sha256` over the sorted package lines."""

    sha256: str
    packages: int
    versions: Mapping[str, str]
    """Normalized package name -> pinned version (`name==version` lines)."""
    torch_cuda: str | None
    """`torch.version.cuda` in that environment, from the lock's note (D9), if any."""


def lock_lines(text: str) -> list[str]:
    """The package lines of a lock (`pip freeze --all` output, any order): without blank
    lines and `#av ` notes, trailing spaces removed, CRLF read as LF, sorted by code point
    (the order of `LC_ALL=C sort`)."""
    lines = (line.rstrip() for line in text.replace("\r\n", "\n").split("\n"))
    return sorted(line for line in lines if line and not line.startswith(LOCK_HEADER))


def lock_sha256(text: str) -> str:
    """`runtime.vllm_lock_sha256`: SHA-256 of the sorted package lines, each ended by `\\n`
    (`grep -v '^#av ' <lock> | LC_ALL=C sort | sha256sum` gives the same digest)."""
    return hashlib.sha256("".join(f"{line}\n" for line in lock_lines(text)).encode()).hexdigest()


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(text: str) -> VllmLock:
    lines = lock_lines(text)
    if not lines:
        raise FreezeError(E_INPUT, "the vLLM lock has no package lines")
    versions = {}
    for line in lines:
        match = _PIN.fullmatch(line)
        if match:
            versions[_normalized(match[1])] = match[2]
    torch_cuda = None
    for line in text.splitlines():
        note = line.rstrip()
        if note.startswith(TORCH_CUDA_NOTE) and _TORCH_CUDA.fullmatch(note[len(TORCH_CUDA_NOTE) :]):
            torch_cuda = note[len(TORCH_CUDA_NOTE) :]
    return VllmLock(lock_sha256(text), len(lines), versions, torch_cuda)


def read_lock(path: str | os.PathLike[str]) -> VllmLock:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        raise FreezeError(E_INPUT, f"cannot read the vLLM lock {Path(path).name}: {err}") from err
    return parse_lock(text)


def lock_text(freeze_output: str, torch_cuda: str | None) -> str:
    """The lock file: a note line with `torch.version.cuda` (when known), then the sorted
    package lines."""
    note = f"{TORCH_CUDA_NOTE}{torch_cuda}\n" if torch_cuda else ""
    return (
        f"{LOCK_HEADER}vLLM server environment lock (python -m av_generation.freeze_assembly "
        "lock); hashed lines: the package lines below\n"
        + note
        + "".join(f"{line}\n" for line in lock_lines(freeze_output))
    )


def _check_outside_work_tree(path: Path, what: str) -> None:
    if inside_work_tree(path):
        raise FreezeError(
            E_POLICY,
            f"the {what} {path.name} would be written inside a git work tree; it is "
            "restricted data: use restricted storage",
        )


def write_lock(
    python: str, out: str | os.PathLike[str], *, run: lm.Runner | None = None
) -> tuple[VllmLock, str | None]:
    """Write the lock of the environment of `python` (the vLLM server's interpreter):
    `pip freeze --all` and `torch.version.cuda`. Returns the lock and the previous
    lock's digest at `out` (None when there was none). Refused inside a git work tree:
    `pip freeze` can name local paths."""
    target = Path(out)
    _check_outside_work_tree(target, "vLLM lock")
    run = run if run is not None else lm.run_command
    try:
        packages = run([python, "-m", "pip", "freeze", "--all"])
    except (OSError, subprocess.SubprocessError) as err:
        raise FreezeError(E_INPUT, f"`{python} -m pip freeze --all` failed: {err}") from err
    try:
        cuda: str | None = run([python, "-c", "import torch; print(torch.version.cuda)"]).strip()
    except (OSError, subprocess.SubprocessError):
        cuda = None
    if cuda is not None and not _TORCH_CUDA.fullmatch(cuda):
        cuda = None
    previous = None
    if target.is_file():
        previous = lock_sha256(target.read_text(encoding="utf-8"))
    text = lock_text(packages, cuda)
    lock = parse_lock(text)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return lock, previous


# ---------------------------------------------------------------------------
# Evidence directory (evidence.sh, #16)


@dataclass(frozen=True, slots=True)
class Criterion:
    status: str
    text: str
    evidence: str


@dataclass(frozen=True, slots=True)
class Evidence:
    criteria: tuple[Criterion, ...]
    summary: Mapping[str, Any]
    acceptance_sha256: str
    exits: Mapping[str, str]
    """Refusal check name -> content of its `.exit` file."""


def acceptance_criteria(text: str) -> tuple[Criterion, ...]:
    """The `| Status | Criterion | Evidence |` rows of `acceptance.md`."""
    rows = []
    for line in text.splitlines():
        match = _ROW.fullmatch(line.strip())
        if match:
            rows.append(Criterion(match[1], match[2].strip(), match[3].strip()))
    return tuple(rows)


def read_evidence(directory: str | os.PathLike[str]) -> Evidence:
    """Read the files of an `evidence.sh` output directory (`EVIDENCE_REQUIRED`)."""
    base = Path(directory)
    missing = [name for name in EVIDENCE_REQUIRED if not (base / name).is_file()]
    if missing:
        raise FreezeError(
            E_EVIDENCE,
            f"the evidence directory {base.name} lacks files evidence.sh writes",
            [f"{name}: not found" for name in missing],
        )
    acceptance = base / ACCEPTANCE
    criteria = acceptance_criteria(acceptance.read_text(encoding="utf-8"))
    if not criteria:
        raise FreezeError(E_EVIDENCE, f"{ACCEPTANCE} has no PASS, FAIL or PENDING rows")
    summary = _read_object(base / SUMMARY, "benchmark summary")
    exits = {name: (base / f"{name}.exit").read_text(encoding="utf-8").strip() for name in REFUSALS}
    return Evidence(criteria, summary, file_sha256(acceptance), exits)


def _mock_problems(evidence: Evidence) -> list[str]:
    out = []
    environment = str(evidence.summary.get("environment", ""))
    runtime = str(evidence.summary.get("runtime", ""))
    if "MOCK" in environment.upper() or runtime.lower().startswith("mock"):
        out.append(f"{SUMMARY}: environment {environment!r}, runtime {runtime!r}")
    out += [
        f"{ACCEPTANCE}: {c.text}: {c.evidence}" for c in evidence.criteria if "MOCK" in c.evidence
    ]
    return out


def _backing_rows(evidence: Evidence) -> tuple[dict[str, Criterion], list[str]]:
    rows: dict[str, Criterion] = {}
    problems = []
    for backing in BACKING:
        found = [c for c in evidence.criteria if backing.phrase in c.text.lower()]
        if len(found) != 1:
            problems.append(
                f"{ACCEPTANCE}: {len(found)} rows for the criterion '{backing.name}' "
                f"(text with {backing.phrase!r}); it backs {', '.join(backing.items)}"
            )
        elif found[0].status != "PASS":
            problems.append(
                f"'{backing.name}' is {found[0].status}, so {', '.join(backing.items)} "
                f"cannot be recorded: {found[0].evidence}"
            )
        else:
            rows[backing.name] = found[0]
    for name, code in evidence.exits.items():
        if code != "2":
            problems.append(f"{name}.exit: exit {code or '(empty)'}; the start check must refuse")
    return rows, problems


def _label_parts(text: str) -> set[str]:
    return {part.strip() for part in text.split(", ")}


def _evidence_mismatches(manifest: lm.LlmManifest, evidence: Evidence) -> list[str]:
    """Where the evidence names another runtime or hardware than the LLM manifest."""
    hw = manifest.hardware
    out = []
    runtime = evidence.summary.get("runtime")
    if runtime != manifest.runtime_label():
        out.append(f"{SUMMARY}: runtime {runtime!r}, LLM manifest {manifest.runtime_label()!r}")
    label = str(evidence.summary.get("environment", ""))
    wanted = {
        f"{hw.gpu} {hw.vram_mib} MiB",
        f"driver {hw.driver_version}",
        f"CUDA {hw.cuda_version}",
        manifest.runtime_label(),
    }
    if hw.torch_version is not None:
        wanted.add(f"torch {hw.torch_version}")
    for part in sorted(wanted - _label_parts(label)):
        out.append(
            f"{SUMMARY}: the environment label {label!r} does not name {part!r} of the LLM "
            "manifest (evidence.sh labels the benchmark with the recorded hardware)"
        )
    row = next((c for c in evidence.criteria if BACKING[1].phrase in c.text.lower()), None)
    if row is not None:
        parts = _label_parts(row.evidence)
        wanted = {
            f"driver {hw.driver_version}",
            f"CUDA {hw.cuda_version}",
            f"vLLM {manifest.runtime.version}",
        }
        missing = sorted(wanted - parts)
        if f"{hw.gpu} {hw.vram_mib} MiB" not in row.evidence:
            missing.insert(0, f"{hw.gpu} {hw.vram_mib} MiB")
        out += [
            f"{ACCEPTANCE}: the hardware record {row.evidence!r} does not name {part!r} of the "
            "LLM manifest"
            for part in missing
        ]
    return out


def _lock_mismatches(manifest: lm.LlmManifest, lock: VllmLock) -> list[str]:
    out = []
    vllm = lock.versions.get("vllm")
    if vllm != manifest.runtime.version:
        out.append(
            "vLLM lock: no vllm==<version> line"
            if vllm is None
            else f"vLLM lock: vllm=={vllm}, LLM manifest runtime.version {manifest.runtime.version}"
        )
    torch, recorded = lock.versions.get("torch"), manifest.hardware.torch_version
    # `torch.__version__` carries a local label (2.9.0+cu128) that pip freeze omits.
    if torch is not None and recorded is not None and _public(torch) != _public(recorded):
        out.append(f"vLLM lock: torch=={torch}, LLM manifest hardware.torch_version {recorded}")
    return out


def _public(version: str) -> str:
    return version.split("+", 1)[0]


# ---------------------------------------------------------------------------
# Values


@dataclass(frozen=True, slots=True)
class GpuHost:
    values: dict[str, FreezeValue]
    notes: list[str]


def gpu_host_values(
    llm_manifest: str | os.PathLike[str],
    evidence_dir: str | os.PathLike[str],
    vllm_lock: str | os.PathLike[str],
    *,
    root: str | os.PathLike[str] | None = None,
) -> GpuHost:
    """The GPU-host items (`GPU_ITEMS`) from the LLM manifest with recorded hardware, the
    evidence directory and the vLLM lock, after every check of the module docstring.

    Raises `FreezeError`: `E_INPUT` (unreadable input, hardware not recorded), `E_EVIDENCE`
    (missing file, a backing criterion not PASS, a start refusal that did not exit 2),
    `E_MOCK` (a MOCK benchmark) or `E_MISMATCH` (manifest, evidence, lock and committed
    LLM manifest disagree)."""
    path = Path(llm_manifest)
    try:
        manifest = lm.load_llm_manifest(path)
    except (lm.ModelMismatch, OSError) as err:
        raise FreezeError(E_INPUT, f"cannot load the LLM manifest {path.name}: {err}") from err
    if manifest.hardware.status != "recorded":
        raise FreezeError(
            E_INPUT,
            f"the hardware of {path.name} is {manifest.hardware.status}: run bringup.sh "
            "(llm_manifest record-hardware) on the GPU host first",
        )
    evidence = read_evidence(evidence_dir)
    lock = read_lock(vllm_lock)
    mock = _mock_problems(evidence)
    if mock:
        raise FreezeError(E_MOCK, "the evidence is a MOCK run, not GPU evidence", mock)
    rows, problems = _backing_rows(evidence)
    if problems:
        raise FreezeError(E_EVIDENCE, "the evidence does not back the GPU-host items", problems)
    digest = file_sha256(path)
    found = lm.freeze_values(manifest, manifest_file_sha256=digest)
    values: dict[str, FreezeValue] = {}
    backed = {item: b for b in BACKING for item in b.items}
    for key, where in MANIFEST_ITEMS.items():
        text = f"GPU host: {where} of the LLM manifest (file SHA-256 {digest})"
        backing = backed.get(key)
        if backing is not None:
            text += (
                f"; evidence.sh '{backing.name}' PASS (acceptance.md "
                f"{evidence.acceptance_sha256[:16]}...)"
            )
        else:
            text += "; Hugging Face API metadata at the pinned revision"
        if key == "runtime.cuda_version":
            torch_cuda = lock.torch_cuda or "not in the lock"
            text += (
                "; nvidia-smi CUDA Version (driver CUDA): the value freeze build compares; "
                f"torch.version.cuda in the vLLM environment: {torch_cuda}; decision D9 open"
            )
        values[key] = FreezeValue(found[key], text)
    values[LOCK_ITEM] = FreezeValue(
        lock.sha256,
        f"GPU host: SHA-256 of the {lock.packages} sorted `pip freeze --all` lines of the vLLM "
        f"server environment (vllm=={lock.versions.get('vllm')}, torch=="
        f"{lock.versions.get('torch')}), lock file from bringup.sh kept in restricted storage",
    )
    mismatches = _evidence_mismatches(manifest, evidence) + _lock_mismatches(manifest, lock)
    committed = freeze.committed_values(root)
    for key, value in values.items():
        pinned = committed.get(key)
        if pinned is not None and not _same(value.value, pinned.value):
            mismatches.append(f"{key}: {value.value!r} here, {pinned.value!r} in {pinned.source}")
    if mismatches:
        raise FreezeError(
            E_MISMATCH,
            "the LLM manifest, the evidence, the vLLM lock and the repository disagree",
            mismatches,
        )
    notes = [
        f"acceptance (no freeze item): {c.status} {c.text}"
        for c in evidence.criteria
        if c not in rows.values()
    ]
    committed_file = committed.get("llm.manifest_sha256")
    if committed_file is not None and committed_file.value != digest:
        notes.append(
            f"note: {path.name} is not the committed {freeze.LLM_MANIFEST_PATH}; commit the "
            "hardware record before `freeze build` (bringup.sh step 7)"
        )
    return GpuHost(values, notes)


@dataclass(frozen=True, slots=True)
class Inputs:
    values: dict[str, FreezeValue]
    """The recorded items of the inputs file (`INPUT_ITEMS` that it gives)."""
    meanings: FreezeValue | None
    """`meanings.sha256` (checked against the frozen config, never recorded)."""


def _timing_problems(review: Mapping[str, Any]) -> list[str]:
    budget_minutes = C.ATOM_BUDGET_MS / 60_000
    within = review["max_atom_minutes"] <= budget_minutes
    if review["within_budget"] != within:
        return [
            f"pilot.timing_review: within_budget is {review['within_budget']}, but "
            f"max_atom_minutes {review['max_atom_minutes']} is "
            f"{'within' if within else 'over'} the {budget_minutes:g}-minute atom budget"
        ]
    return []


def read_inputs(path: str | os.PathLike[str]) -> Inputs:
    """Read and check the restricted inputs file (`g4-inputs.schema.json`)."""
    source = Path(path)
    doc = _read_object(source, "inputs file")
    errors = schema_errors(SCHEMA, doc)
    if errors:
        raise FreezeError(E_INPUT, f"{source.name} is not a G4 inputs file", errors)
    items: Mapping[str, Mapping[str, Any]] = doc["items"]
    review = items.get("pilot.timing_review")
    problems = _timing_problems(review["value"]) if review is not None else []
    if problems:
        raise FreezeError(E_INPUT, f"{source.name} is not consistent", problems)
    values = {
        key: FreezeValue(items[key]["value"], items[key]["source"])
        for key in INPUT_ITEMS
        if key in items
    }
    meanings = None
    entry = items.get(MEANINGS_ITEM)
    if entry is not None:
        if "value" in entry:
            meanings = FreezeValue(entry["value"], entry["source"])
        else:
            where = source.parent / entry["meaning_set"]
            try:
                digest = load_meanings(where).sha256()
            except (OSError, RecordError, CodecError, ValueError) as err:
                raise FreezeError(E_INPUT, f"cannot load the meaning set: {err}") from err
            meanings = FreezeValue(digest, entry["source"])
    return Inputs(values, meanings)


def check_config_meanings(meanings: FreezeValue, config_path: str | os.PathLike[str]) -> str:
    """Refuse a frozen config built from another meaning set (`E_MISMATCH`)."""
    try:
        config = GenerationConfig.read(config_path)
    except (OSError, RecordError, CodecError) as err:
        raise FreezeError(E_INPUT, f"cannot read {Path(config_path).name}: {err}") from err
    if config.meanings_sha256 != meanings.value:
        raise FreezeError(
            E_MISMATCH,
            f"{Path(config_path).name} names another meaning set",
            [f"{MEANINGS_ITEM}: config {config.meanings_sha256}, inputs {meanings.value}"],
        )
    return f"{MEANINGS_ITEM}: the frozen config {config.name!r} names the meaning set of the inputs"


# ---------------------------------------------------------------------------
# Filling and writing the draft


def _short(value: object) -> str:
    """A table cell for a value: hashes and revisions shortened, objects summarized."""
    if value is None:
        return "-"
    if isinstance(value, str):
        if _HEX64.fullmatch(value) or _HEX40.fullmatch(value):
            return f"{value[:12]}..."
        return value if len(value) <= 60 else f"{value[:57]}..."
    if isinstance(value, Mapping):
        if value and all(isinstance(v, str) and _HEX64.fullmatch(v) for v in value.values()):
            text = ", ".join(f"{k} {str(v)[:12]}..." for k, v in sorted(value.items()))
            return text if len(text) <= 80 else f"{len(value)} digests: {text[:60]}..."
        return f"object, {len(value)} fields, sha256 {canonical_sha256(value)[:12]}..."
    if isinstance(value, list):
        return f"array, {len(value)} entries, sha256 {canonical_sha256(value)[:12]}..."
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def fill_draft(
    draft: Mapping[str, Any],
    values: Mapping[str, FreezeValue],
    *,
    root: str | os.PathLike[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Refresh a draft and record `values` through `freeze.record_value`; returns the new
    draft and one line per recorded item (new, confirmed or changed)."""
    manifest = freeze.refresh_draft(draft, root=root)
    before = freeze.item_values(manifest)
    lines = []
    for key in freeze.REQUIRED_ITEM_KEYS:
        if key not in values:
            continue
        value = values[key]
        manifest = freeze.record_value(manifest, key, value.value, value.source, root=root)
        old = before.get(key)
        if old is None:
            how = "new"
        elif _same(old, value.value):
            how = "confirmed"
        else:
            how = f"changed from {_short(old)}"
        lines.append(f"recorded {key} = {_short(value.value)} ({how})")
    return manifest, lines


def write_draft(
    path: str | os.PathLike[str],
    manifest: Mapping[str, Any],
    *,
    root: str | os.PathLike[str] | None = None,
) -> str:
    """Write a filled draft once it passes the CI freeze guard; returns its file SHA-256.
    Refused inside a git work tree (`E_POLICY`) and when the guard fails (`E_GUARD`)."""
    target = Path(path)
    _check_outside_work_tree(target, "filled draft")
    temp = target.with_name(f".{target.name}.tmp")
    write_document(temp, manifest)
    differences = freeze.freeze_differences(temp, freeze.current_values(root))
    if differences:
        temp.unlink()
        raise FreezeError(freeze.E_GUARD, "the filled draft fails the freeze guard", differences)
    os.replace(temp, target)
    return file_sha256(target)


# ---------------------------------------------------------------------------
# Status table


@dataclass(frozen=True, slots=True)
class StatusRow:
    key: str
    state: str
    """`repository` (code and file items), `recorded` or `pending`."""
    value: str
    guard: str
    filled_by: str
    """For a pending item: who fills it, by role (`ROLES`); else empty."""
    source: str


def status_rows(manifest: Mapping[str, Any]) -> list[StatusRow]:
    """One row per item of `manifest`, in manifest order."""
    rows = []
    for item in manifest.get("items") or ():
        key, guard, value = str(item.get("key")), str(item.get("guard")), item.get("value")
        if value is None:
            state = "pending"
        elif guard in ("code", "file"):
            state = "repository"
        else:
            state = "recorded"
        rows.append(
            StatusRow(
                key,
                state,
                _short(value),
                guard,
                role_of(key) if value is None else "",
                str(item.get("source")),
            )
        )
    return rows


def status_table(manifest: Mapping[str, Any], *, name: str = "", sha256: str = "") -> str:
    """The sign-off table (#25): every item with its state, and for pending items who
    fills them (roles only; no names and no dates)."""

    def cell(text: str) -> str:
        return text.replace("|", "\\|")

    rows = status_rows(manifest)
    counts = {s: sum(r.state == s for r in rows) for s in ("repository", "recorded", "pending")}
    by_role: dict[str, int] = {}
    for row in rows:
        if row.state == "pending":
            by_role[row.filled_by] = by_role.get(row.filled_by, 0) + 1
    lines = [
        f"# G4 freeze status: {name or 'manifest'} ({manifest.get('status')}, freeze "
        f"v{manifest.get('freeze_version')})",
        "",
        f"File SHA-256 {sha256 or '-'}. {len(rows)} items: {counts['repository']} repository, "
        f"{counts['recorded']} recorded, {counts['pending']} pending"
        + (
            " (" + ", ".join(f"{role}: {n}" for role, n in sorted(by_role.items())) + ")."
            if by_role
            else "."
        ),
        "",
        "| Key | State | Value | Guard | Filled by | Source |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        "| "
        + " | ".join(
            cell(part)
            for part in (
                f"`{r.key}`",
                r.state,
                f"`{r.value}`" if r.state != "pending" else "-",
                r.guard,
                r.filled_by or "-",
                r.source,
            )
        )
        + " |"
        for r in rows
    ]
    lines += [
        "",
        "Sign-off: the owner and the advisor comment on #25 with this table; `python -m "
        "av_generation.freeze build --signoff owner|advisor <date> <link>` records the "
        "comments (generation/docs/freeze.md section 6).",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Command line


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m av_generation.freeze_assembly",
        description="G4 freeze assembly: fill the restricted draft from the GPU host and the "
        "private inputs, print the sign-off status (generation/docs/freeze-assembly.md).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    fill = sub.add_parser("fill", help="refresh a restricted draft and record the G4 values")
    fill.add_argument("--draft", type=Path, required=True, help="restricted copy of the draft")
    fill.add_argument("--out", type=Path, help="write here instead of over --draft")
    fill.add_argument("--llm-manifest", type=Path, help="LLM manifest with recorded hardware")
    fill.add_argument("--evidence", type=Path, help="evidence.sh output directory")
    fill.add_argument("--vllm-lock", type=Path, help="lock of the vLLM server environment")
    fill.add_argument("--inputs", type=Path, help="restricted G4 inputs file")
    fill.add_argument("--config", type=Path, help="frozen generation-config.json (meanings)")
    status = sub.add_parser("status", help="print every item as the sign-off table")
    status.add_argument("--draft", type=Path, help="default: the active manifest")
    lock = sub.add_parser("lock", help="write the lock of the vLLM server environment")
    lock.add_argument("--python", required=True, help="the vLLM environment's python")
    lock.add_argument("--out", type=Path, required=True)
    return parser


def _cmd_fill(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    gpu = (args.llm_manifest, args.evidence, args.vllm_lock)
    if any(part is not None for part in gpu) and None in gpu:
        parser.error("--llm-manifest, --evidence and --vllm-lock go together")
    if args.llm_manifest is None and args.inputs is None:
        parser.error("give the GPU-host files, --inputs, or both")
    if args.config is not None and args.inputs is None:
        parser.error("--config checks the meaning set of --inputs")
    out = args.out or args.draft
    _check_outside_work_tree(out, "filled draft")
    draft = _read_object(args.draft, "draft")
    values: dict[str, FreezeValue] = {}
    notes: list[str] = []
    if args.llm_manifest is not None:
        host = gpu_host_values(args.llm_manifest, args.evidence, args.vllm_lock)
        values.update(host.values)
        notes += host.notes
    if args.inputs is not None:
        inputs = read_inputs(args.inputs)
        values.update(inputs.values)
        if inputs.meanings is not None and args.config is not None:
            notes.append(check_config_meanings(inputs.meanings, args.config))
        elif inputs.meanings is not None:
            notes.append(
                f"{MEANINGS_ITEM} {inputs.meanings.value} (inputs) is a config item: give "
                "--config to check the frozen config; freeze build copies it from there"
            )
    manifest, lines = fill_draft(draft, values)
    digest = write_draft(out, manifest)
    for line in lines + notes:
        print(line)
    pending = [k for k, v in freeze.item_values(manifest).items() if v is None]
    print(f"wrote {out.name}: file SHA-256 {digest}; {len(pending)} items pending")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    path = args.draft or freeze.active_manifest_path()
    manifest = _read_object(path, "manifest")
    sys.stdout.write(status_table(manifest, name=path.name, sha256=file_sha256(path)))
    return 0


def _cmd_lock(args: argparse.Namespace) -> int:
    lock, previous = write_lock(args.python, args.out)
    print(
        f"wrote {args.out.name}: {lock.packages} packages, vllm=={lock.versions.get('vllm')}, "
        f"torch.version.cuda {lock.torch_cuda}, {LOCK_ITEM} {lock.sha256}"
    )
    if previous is not None and previous != lock.sha256:
        print(f"the environment changed: previous lock {previous}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point (`python -m av_generation.freeze_assembly --help`)."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "fill":
            return _cmd_fill(args, parser)
        if args.command == "status":
            return _cmd_status(args)
        return _cmd_lock(args)
    except FreezeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
