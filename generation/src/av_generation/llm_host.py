"""GPU-host bring-up helpers (#16): pins for the scripts, freeze commands, evidence report.

The scripts in `generation/llm/host/` (`bringup.sh`, `evidence.sh`) do the work on the
LLM host; this module gives them the values they must not duplicate and judges what they
collected:

- `pins`: the model ID, revision, pinned vLLM version, served model name, host and port,
  read from `generation/llm/manifest.json` and `server-config.json` (`load_llm_manifest`
  and `load_server_config` check both), printed as shell assignments.
- `update_server_config`: after `llm_manifest record-hardware` rewrites the manifest,
  the server config must name the manifest's new file hash (`manifest_sha256`).
- `freeze_commands`: the `python -m av_generation.freeze record` commands that fill the
  draft's GPU-host items (`GPU_HOST_ITEMS`) with the values `freeze build` compares with
  the committed manifest (`llm_manifest.freeze_values`).
- `outbound_connections`: established TCP connections that this host opened to a peer
  outside loopback and the allowed station networks (from `ss` output).
- `report`: the acceptance criteria of #16 with PASS, FAIL or PENDING from the files
  `evidence.sh` writes (`EVIDENCE_FILES`).

Command line: `python -m av_generation.llm_host pins|update-config|freeze-commands|
outbound|report` (`main`).
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import shlex
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from av_generation.constants import SLOT_CAP_MS
from av_generation.jsonio import CodecError, document_text, read_json
from av_generation.llm_manifest import (
    MIN_VRAM_MIB,
    LlmManifest,
    freeze_values,
    load_llm_manifest,
    manifest_path,
    manifest_sha256,
)
from av_generation.llm_server import load_server_config, server_config_path

GPU_HOST_ITEMS: Final[tuple[str, ...]] = (
    "runtime.vllm_version",
    "runtime.cuda_version",
    "runtime.driver_version",
    "runtime.gpu",
    "runtime.precision",
    "runtime.max_model_len",
    "runtime.chat_template_sha256",
    "decoding.implementation",
)
"""Items of the G4 freeze draft that wait for the GPU host (`PENDING (GPU host)`)."""

BENCH_CALLS: Final = 300
"""Calls per prompt profile the acceptance criterion names."""
EVIDENCE_FILES: Final[Mapping[str, str]] = {
    "bench/llm_latency_summary.json": "llm_bench summary (300 calls per profile, --repeat)",
    "bench/llm_repeatability.csv": "same-seed repeat test",
    "refusal-revision.exit": "llm_server check on a copy whose metadata names another revision",
    "refusal-weights.exit": "llm_server check on a copy with one altered weight shard",
    "slow-cap.exit": "the 40.5-s slowed-server test (pytest -k slow)",
    "seed.exit": "the seed tests (10,000 keys, A and B key spaces)",
    "ss-established.txt": "`ss -Htn state established` samples taken during the benchmark",
    "ss-listening.txt": "`ss -Htln`: the ports this host listens on",
    "allowed.txt": "station networks (CIDR, one per line) the host may talk to",
}
"""What `evidence.sh` leaves in its output directory (`.exit` files hold an exit code
and have a `.log` next to them)."""

Status = Literal["PASS", "FAIL", "PENDING"]


class HostError(RuntimeError):
    """A bring-up step cannot go on (for example, the hardware is not recorded yet)."""


# ---------------------------------------------------------------------------
# Pins


def weights_bytes(manifest: LlmManifest) -> int:
    """Total size of the pinned weight shards (bf16: about 15.2 GB)."""
    return sum(f.size for f in manifest.model.files if f.kind == "weights")


def pins(
    manifest: str | os.PathLike[str] | None = None,
    config: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """The values the host scripts use, from the committed (checked) manifest and server
    config: they are never written into the scripts."""
    m = load_llm_manifest(manifest)
    c = load_server_config(config)
    shard = min((f for f in m.model.files if f.kind == "weights"), key=lambda f: f.size)
    return {
        "MODEL_ID": m.model.id,
        "MODEL_REVISION": m.model.revision,
        "VLLM_VERSION": m.runtime.version,
        "SERVED_MODEL_NAME": c.served_model_name,
        "DEFAULT_HOST": c.host,
        "DEFAULT_PORT": str(c.port),
        "MAX_MODEL_LEN": str(m.runtime.max_model_len),
        "PRECISION": m.runtime.precision,
        "HARDWARE_STATUS": m.hardware.status,
        "WEIGHTS_BYTES": str(weights_bytes(m)),
        "MIN_VRAM_MIB": str(MIN_VRAM_MIB),
        "SMALLEST_SHARD": shard.path,
        "SMALLEST_SHARD_BYTES": str(shard.size),
        "HARDWARE_LABEL": hardware_label(m),
    }


def hardware_label(manifest: LlmManifest) -> str:
    """The benchmark's `--environment` label from the recorded hardware ("" while
    pending)."""
    hw = manifest.hardware
    if hw.status != "recorded":
        return ""
    return (
        f"{hw.gpu} {hw.vram_mib} MiB, driver {hw.driver_version}, CUDA {hw.cuda_version}, "
        f"{manifest.runtime_label()}, torch {hw.torch_version}"
    )


def shell_assignments(values: Mapping[str, str]) -> str:
    """`KEY='value'` lines for `eval` in bash (every value quoted)."""
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items())


def update_server_config(
    config: str | os.PathLike[str] | None = None,
    manifest: str | os.PathLike[str] | None = None,
) -> tuple[str, str]:
    """Point the server config at the manifest's current file hash; returns (old, new).
    The file keeps its committed format (`jsonio.document_text`)."""
    path = Path(config) if config is not None else server_config_path()
    data = read_json(path)
    if not isinstance(data, dict):
        raise HostError(f"{path.name} is not a JSON object")
    old = str(data.get("manifest_sha256"))
    new = manifest_sha256(manifest)
    if old != new:
        data["manifest_sha256"] = new
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(document_text(data))
    load_server_config(path)  # still valid
    return old, new


def freeze_commands(manifest: LlmManifest, *, manifest_file_sha256: str, draft: str) -> list[str]:
    """`freeze record` commands for every GPU-host item of the draft at `draft`, with the
    values of the committed manifest (what `freeze build` compares). Refused while the
    manifest's hardware section is pending."""
    if manifest.hardware.status != "recorded":
        raise HostError("record the hardware first: python -m av_generation.llm_manifest "
                        "record-hardware --vllm <vllm-env>/bin/vllm")  # fmt: skip
    values = freeze_values(manifest, manifest_file_sha256=manifest_file_sha256)
    lines = []
    for key in GPU_HOST_ITEMS:
        value = values[key]
        option = (
            f"--value {value}" if isinstance(value, int) else f"--text {shlex.quote(str(value))}"
        )
        source = f"GPU host: generation/llm/manifest.json ({key}; llm_manifest show)"
        lines.append(
            f"python -m av_generation.freeze record {key} {option} "
            f"--source {shlex.quote(source)} --manifest {shlex.quote(draft)}"
        )
    return lines


# ---------------------------------------------------------------------------
# Outbound connections (ss)


def _endpoint(text: str) -> tuple[str, int] | None:
    """`10.0.0.5:8000`, `[::1]:8000`, `[fe80::1%eth0]:22`, `*:22` -> (address, port)."""
    host, sep, port = text.rpartition(":")
    if not sep or not port.isdigit():
        return None
    host = host.strip("[]").split("%", 1)[0]
    return host, int(port)


def _columns(line: str) -> tuple[str, str] | None:
    """Local and peer endpoint of one `ss -Htn` line (with or without the state column)."""
    parts = line.split()
    ends = [p for p in parts if _endpoint(p) is not None or p.endswith(":*")]
    return (ends[0], ends[1]) if len(ends) >= 2 else None


def listening_ports(listening: str) -> set[int]:
    ports = set()
    for line in listening.splitlines():
        cols = _columns(line)
        end = _endpoint(cols[0]) if cols else None
        if end is not None:
            ports.add(end[1])
    return ports


def _allowed(
    address: str, networks: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network]
) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or any(ip in net for net in networks if ip.version == net.version)


def outbound_connections(established: str, listening: str, allowed: Iterable[str]) -> list[str]:
    """Lines of `ss -Htn state established` output for connections this host opened
    (the local port is not a listening port) to a peer outside loopback and `allowed`
    (CIDR strings). Connections to the host's listening ports are inbound and skipped."""
    networks = [
        ipaddress.ip_network(cidr.strip(), strict=False) for cidr in allowed if cidr.strip()
    ]
    ports = listening_ports(listening)
    found = []
    for line in established.splitlines():
        if line.lstrip().startswith("#"):
            continue  # a sample's time stamp (evidence.sh)
        cols = _columns(line)
        if cols is None:
            continue
        local, peer = _endpoint(cols[0]), _endpoint(cols[1])
        if local is None or peer is None or local[1] in ports:
            continue
        if not _allowed(peer[0], networks):
            found.append(line.strip())
    return sorted(set(found))


# ---------------------------------------------------------------------------
# Acceptance report


@dataclass(frozen=True, slots=True)
class Criterion:
    text: str
    status: Status
    evidence: str


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _exit_check(
    out: Path, name: str, *, expect: int, needle: str | None = None
) -> tuple[Status, str]:
    code_text = _read_text(out / f"{name}.exit")
    if code_text is None:
        return "PENDING", f"{name}.exit not found (run evidence.sh)"
    log = _read_text(out / f"{name}.log") or ""
    code = int(code_text.strip() or -1)
    ok = code == expect and (needle is None or needle in log)
    detail = f"exit {code}" + (
        f", {needle} {'in' if needle in log else 'not in'} the log" if needle else ""
    )
    return ("PASS" if ok else "FAIL"), f"{name}: {detail}"


C_REFUSAL: Final = (
    "Server start fails with a clear error when the weights checksum or revision differs "
    "from the manifest"
)
C_LOGGED: Final = (
    f"{BENCH_CALLS} of {BENCH_CALLS} benchmark calls log temperature=0.7, top_p=0.9, "
    "top_k=50, repetition_penalty=1.0, max_tokens=512 and a derived seed"
)
C_CAP: Final = "A call exceeding 40 s returns timeout within 40.5 s (slowed-server test)"
C_SEED: Final = (
    "The seed function gives identical output for 10,000 sample keys, with 0 collisions "
    "across the A and B key spaces"
)
C_P95: Final = "p95 slot latency at the worst-case prompt size is under 40 s on the LLM GPU"
C_OUTBOUND: Final = "No outbound network connection occurs during a session"
C_HARDWARE: Final = "Checklist: vLLM, CUDA and driver versions pinned; GPU and VRAM recorded"
C_REPEAT: Final = "Checklist: same-seed repeat test run; byte-identical outputs recorded"
NOT_GPU: Final = "; MOCK run, not GPU evidence"


def _summary(out: Path) -> dict[str, Any] | None:
    try:
        doc = read_json(out / "bench" / "llm_latency_summary.json")
    except (OSError, CodecError):
        return None
    return doc if isinstance(doc, dict) else None


def _refusal(out: Path) -> Criterion:
    rev, rev_note = _exit_check(out, "refusal-revision", expect=2, needle="E_REVISION")
    wts, wts_note = _exit_check(out, "refusal-weights", expect=2, needle="E_WEIGHTS_SHA256")
    status: Status = "PENDING"
    if "FAIL" in (rev, wts):
        status = "FAIL"
    elif rev == wts == "PASS":
        status = "PASS"
    return Criterion(C_REFUSAL, status, f"{rev_note}; {wts_note}")


def _logged(summary: Mapping[str, Any], gpu: bool) -> Criterion:
    profiles: Mapping[str, Any] = summary.get("profiles") or {}
    counts = {
        name: (p.get("calls"), p.get("frozen_values_and_seed_logged"), p.get("distinct_seeds"))
        for name, p in profiles.items()
    }
    full = (BENCH_CALLS, BENCH_CALLS, BENCH_CALLS)
    ok = gpu and set(counts) == {"realistic", "worst_case"}
    ok = ok and all(c == full for c in counts.values())
    note = "; ".join(
        f"{n}: {c[1]} of {c[0]} logged, {c[2]} distinct seeds" for n, c in sorted(counts.items())
    )
    return Criterion(C_LOGGED, "PASS" if ok else "FAIL", note + ("" if gpu else NOT_GPU))


def _p95(summary: Mapping[str, Any], gpu: bool) -> Criterion:
    worst = (summary.get("profiles") or {}).get("worst_case")
    if not isinstance(worst, Mapping):
        return Criterion(C_P95, "PENDING", "the summary has no worst_case profile")
    p95 = (worst.get("latency_ms") or {}).get("p95")
    ok = gpu and bool(worst.get("p95_under_slot_cap"))
    ok = ok and isinstance(p95, int) and p95 < SLOT_CAP_MS
    note = f"p95 {p95} ms at {worst.get('prompt_tokens')} prompt tokens"
    return Criterion(C_P95, "PASS" if ok else "FAIL", note + ("" if gpu else NOT_GPU))


def _outbound(out: Path, summary: Mapping[str, Any] | None) -> Criterion:
    established = _read_text(out / "ss-established.txt")
    listening = _read_text(out / "ss-listening.txt")
    if established is None or listening is None or summary is None:
        return Criterion(C_OUTBOUND, "PENDING", "ss samples or the benchmark summary missing")
    allowed = (_read_text(out / "allowed.txt") or "").split()
    refused = summary.get("outbound_refused")
    found = outbound_connections(established, listening, allowed)
    note = (
        f"{len(found)} connections opened to peers outside loopback and "
        f"{allowed or 'no station network'} {found[:3]}; refused client attempts: {refused}"
    )
    return Criterion(C_OUTBOUND, "PASS" if not found and refused == [] else "FAIL", note)


def _hardware(m: LlmManifest) -> Criterion:
    hw = m.hardware
    status: Status = "PENDING"
    if hw.status == "recorded":
        status = "PASS" if (hw.vram_mib or 0) >= MIN_VRAM_MIB else "FAIL"
    note = (
        f"hardware {hw.status}: {hw.gpu} {hw.vram_mib} MiB (minimum {MIN_VRAM_MIB}), driver "
        f"{hw.driver_version}, CUDA {hw.cuda_version}, vLLM {hw.vllm_installed}, torch "
        f"{hw.torch_version}"
    )
    return Criterion(C_HARDWARE, status, note)


def _repeat(summary: Mapping[str, Any] | None) -> Criterion:
    repeat = summary.get("repeatability") if summary else None
    if not isinstance(repeat, Mapping) or not repeat.get("keys"):
        return Criterion(C_REPEAT, "PENDING", "no --repeat run")
    note = f"{repeat.get('byte_identical')} of {repeat.get('keys')} keys byte-identical"
    return Criterion(C_REPEAT, "PASS", note)


def report(
    out_dir: str | os.PathLike[str], *, manifest: LlmManifest | None = None
) -> list[Criterion]:
    """#16's acceptance criteria and checklist items judged from `evidence.sh`'s files."""
    out = Path(out_dir)
    m = manifest if manifest is not None else load_llm_manifest()
    summary = _summary(out)
    gpu = summary is not None and "MOCK" not in str(summary.get("environment", "")).upper()
    slow, slow_note = _exit_check(out, "slow-cap", expect=0)
    seed, seed_note = _exit_check(out, "seed", expect=0)
    missing = "bench/llm_latency_summary.json not found"
    return [
        _refusal(out),
        _logged(summary, gpu) if summary else Criterion(C_LOGGED, "PENDING", missing),
        Criterion(C_CAP, slow, slow_note),
        Criterion(C_SEED, seed, seed_note + " (macOS, Linux and Windows: CI)"),
        _p95(summary, gpu) if summary else Criterion(C_P95, "PENDING", missing),
        _outbound(out, summary),
        _hardware(m),
        _repeat(summary),
    ]


def report_text(criteria: Sequence[Criterion]) -> str:
    """The criteria as a Markdown table (`acceptance.md`, and for the issue comment)."""
    lines = [
        "# #16 acceptance on the LLM host",
        "",
        "| Status | Criterion | Evidence |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {c.status} | {c.text} | {c.evidence} |" for c in criteria]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m av_generation.llm_host")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("pins", "update-config", "freeze-commands", "report"):
        p = sub.add_parser(name)
        p.add_argument("--manifest", default=None)
        if name in ("pins", "update-config"):
            p.add_argument("--config", default=None)
        if name == "freeze-commands":
            p.add_argument("--draft", required=True, help="the restricted copy of the draft")
        if name == "report":
            p.add_argument("--out-dir", required=True)
    outbound = sub.add_parser("outbound")
    outbound.add_argument("--established", required=True)
    outbound.add_argument("--listening", required=True)
    outbound.add_argument("--allow", action="append", default=[], help="station network CIDR")
    args = parser.parse_args(argv)
    try:
        if args.command == "pins":
            sys.stdout.write(shell_assignments(pins(args.manifest, args.config)))
            return 0
        if args.command == "update-config":
            old, new = update_server_config(args.config, args.manifest)
            print(f"server-config manifest_sha256: {old} -> {new}" if old != new else
                  f"server-config already names manifest {new}")  # fmt: skip
            return 0
        if args.command == "freeze-commands":
            path = args.manifest or manifest_path()
            manifest = load_llm_manifest(path)
            for line in freeze_commands(manifest, manifest_file_sha256=manifest_sha256(path),
                                        draft=args.draft):  # fmt: skip
                print(line)
            return 0
        if args.command == "outbound":
            found = outbound_connections(
                Path(args.established).read_text(encoding="utf-8"),
                Path(args.listening).read_text(encoding="utf-8"),
                args.allow,
            )
            for line in found:
                print(f"OUTBOUND {line}")
            print("no outbound connection" if not found else f"{len(found)} outbound connections")
            return 0 if not found else 1
        manifest = load_llm_manifest(args.manifest)
        criteria = report(args.out_dir, manifest=manifest)
        text = report_text(criteria)
        Path(args.out_dir, "acceptance.md").write_text(text, encoding="utf-8")
        sys.stdout.write(text)
        return 0 if all(c.status == "PASS" for c in criteria) else 1
    except (HostError, OSError, ValueError, RuntimeError) as err:
        print(f"llm_host {args.command}: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
