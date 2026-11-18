"""Input files of Study A batch runs: batch configs, rebuilt configs and the generation
config, built from the schedules files with one command each (#311).

`orchestrator.build_batch_config`, `rebuild_batch_config` and
`genconfig.build_generation_config` exist as Python functions only. This module wires
them to the schedules outputs (#29 batch table and `permutation.json`, #31 book key), so
the pilot-day runbook (`generation/docs/pilot-day-runbook.md`) needs no code.

    uv run --project generation python -m av_generation.batch_setup configs \\
        --batch-table <restricted>/schedules/A/pilot-batch-table.csv \\
        --units <restricted>/schedules/A --book-key <restricted>/schedules/A/pilot-book-key.json \\
        --fallback <restricted>/fallback/fallback-manifest.json \\
        --set-ns-file <restricted>/pilot-set-ns.txt \\
        --seats A-P01=R01@S1,R02@S2,R03@S3 --seats ... --out <restricted>/pilot-inputs
    uv run --project generation python -m av_generation.batch_setup generation-config \\
        --name pilot-1 --meanings <restricted>/meanings \\
        --fallback <restricted>/fallback/fallback-manifest.json \\
        --out <restricted>/pilot-inputs/generation-config.json
    uv run --project generation python -m av_generation.batch_setup rebuild \\
        --config <restricted>/pilot-inputs/A-P01/config.json \\
        --set-ns-file <restricted>/pilot-set-ns.txt --seats R10@S1,R11@S2,R12@S3 \\
        --out <restricted>/pilot-inputs/A-P01-rb1/config.json

Rules:

- `configs` writes `<out>/panel-orders.csv` (the set's panel order schedule) and
  `<out>/<batch_id>/config.json` per batch-table row, in table order. Panel `<batch>-N1`;
  the order index comes from `orchestrator.panel_order_schedule` with every batch's
  profile; the aliases from `panel_aliases`; the threshold is the text of
  `sound/config/validator.json` and must be the fallback set's threshold. Each config
  records the SHA-256 of its batch table, book key, `permutation.json` and panel order
  schedule (`ConfigSources`).
- A real set needs a restricted set namespace from a file (`--set-ns-file`, at least
  16 characters, not `DEMO-`), real schedules files and human seats, and writes outside
  every git work tree (`E_POLICY`). A rehearsal set (`--demo-set-ns DEMO-...`) needs
  DEMO schedules files and prefixes every batch and book ID with `DEMO-`, so the batch
  runner accepts it for `--kind demo` runs; its seats may be `human` (rehearsal with
  people at the stations) or `bot`.
- One rater sits on one panel only: a rater ID in two batches is refused (`E_SEATS`).
- Nothing is overwritten (`E_EXISTS`), and nothing is written when any batch fails.

Exit status: 0 written, 2 refused (`error: <code>: ...` on stderr).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from av_sound._paths import config_path
from av_sound.fallback import FallbackSet, inside_work_tree, load_fallback
from av_sound.features import parse_threshold

from av_generation.config import BatchConfig, BookAssignment, ConfigSources, RaterSeat
from av_generation.genconfig import GenerationConfig, build_generation_config, fallback_pins
from av_generation.ids import DEMO_PREFIX, ID_RE, is_demo
from av_generation.jsonio import file_sha256
from av_generation.llm import decoding_schema_sha256
from av_generation.llm_manifest import load_llm_manifest, manifest_sha256
from av_generation.meanings import load_meanings
from av_generation.orchestrator import (
    PANEL_ORDER_COLUMNS,
    build_batch_config,
    check_permutation,
    panel_order_rows,
    read_batch_table,
    read_book_key,
    rebuild_batch_config,
)
from av_generation.prompts import default_prompt_set_dir, load_prompt_set

__all__ = [
    "MIN_SET_NS_CHARS",
    "PANEL_ORDERS_NAME",
    "BatchSetupError",
    "SetConfigs",
    "build_set_configs",
    "main",
    "next_rebuild_ids",
    "parse_seats",
    "read_set_namespace",
    "threshold_text",
    "write_set_configs",
]

E_INPUTS: Final = "E_INPUTS"
E_POLICY: Final = "E_POLICY"
E_EXISTS: Final = "E_EXISTS"
E_SEATS: Final = "E_SEATS"

MIN_SET_NS_CHARS: Final = 16
"""Shortest restricted set namespace (for example `A-P-` + 16 random hex characters)."""
PANEL_ORDERS_NAME: Final = "panel-orders.csv"
CONFIG_NAME: Final = "config.json"
SeatKind = Literal["human", "bot"]
_SEAT_RE: Final = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]{0,31})@(S[0-9]{1,2})")
_PANEL_RE: Final = re.compile(r"(.+)-N([0-9]+)")


class BatchSetupError(ValueError):
    """An input is refused; `.code` names the rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Small inputs


def threshold_text(path: str | os.PathLike[str] | None = None) -> str:
    """The separation threshold as its exact decimal text (`sound/config/validator.json`)."""
    target = Path(path) if path is not None else config_path("validator.json")
    data = json.loads(target.read_text(encoding="utf-8"))
    value = data.get("separation_threshold") if isinstance(data, dict) else None
    if not isinstance(value, str):
        raise BatchSetupError(E_INPUTS, f"{target}: no separation_threshold text")
    parse_threshold(value)
    return value


def read_set_namespace(path: str | os.PathLike[str]) -> str:
    """The restricted set namespace in `path` (first line): an ID of at least
    `MIN_SET_NS_CHARS` characters, not `DEMO-`, kept outside every git work tree."""
    if inside_work_tree(path):
        raise BatchSetupError(E_POLICY, f"{path}: a set namespace file is restricted storage")
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    value = lines[0].strip() if lines else ""
    if not ID_RE.fullmatch(value) or len(value) < MIN_SET_NS_CHARS or is_demo(value):
        raise BatchSetupError(
            E_INPUTS,
            f"{path}: the set namespace must be an ID of at least {MIN_SET_NS_CHARS} "
            "characters (letters, digits, inner hyphens) and not DEMO-",
        )
    return value


def parse_seats(text: str, *, kind: SeatKind = "human") -> tuple[RaterSeat, ...]:
    """`R01@S1,R02@S2,R03@S3` -> three seats (coded rater ID @ station)."""
    seats = []
    for part in text.split(","):
        match = _SEAT_RE.fullmatch(part.strip())
        if match is None:
            raise BatchSetupError(E_SEATS, f"seat {part.strip()!r} is not <rater>@S<n>")
        seats.append(RaterSeat(match[1], match[2], kind))
    return tuple(seats)


def _seats_by_unit(values: Sequence[str], *, kind: SeatKind) -> dict[str, tuple[RaterSeat, ...]]:
    out: dict[str, tuple[RaterSeat, ...]] = {}
    for value in values:
        unit, sep, seats = value.partition("=")
        if not sep or not unit:
            raise BatchSetupError(E_SEATS, f"--seats {value!r} is not <unit>=<rater>@S<n>,...")
        if unit in out:
            raise BatchSetupError(E_SEATS, f"--seats names {unit} twice")
        out[unit] = parse_seats(seats, kind=kind)
    return out


def _demo_flag(path: str | os.PathLike[str]) -> bool:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return isinstance(data, dict) and data.get("demo") is True


def _check_threshold(text: str, fallback: FallbackSet) -> str:
    if parse_threshold(text) != fallback.threshold:
        raise BatchSetupError(
            E_INPUTS, f"the fallback set was built for another threshold than {text}"
        )
    return text


def _check_out(path: str | os.PathLike[str], *, demo: bool) -> None:
    if not demo and inside_work_tree(path):
        raise BatchSetupError(
            E_POLICY, f"{path} is inside a git work tree; real batch inputs are restricted"
        )


# ---------------------------------------------------------------------------
# Batch configs of a set


@dataclass(frozen=True, slots=True)
class SetConfigs:
    """The batch configs of one set and its panel order schedule (not yet written)."""

    configs: tuple[BatchConfig, ...]
    panel_orders_csv: bytes


def _panel_orders_csv(
    set_ns: str, rows: Sequence[tuple[str, str]], profiles: Mapping[str, str]
) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=PANEL_ORDER_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(panel_order_rows(set_ns, rows, profiles=profiles))
    return buffer.getvalue().encode("utf-8")


def build_set_configs(
    *,
    batch_table: str | os.PathLike[str],
    units: str | os.PathLike[str],
    book_key: str | os.PathLike[str],
    fallback: str | os.PathLike[str],
    set_ns: str,
    seats: Mapping[str, Sequence[RaterSeat]],
    threshold: str | None = None,
) -> SetConfigs:
    """One batch config per batch-table row (see the module docstring).

    `set_ns` decides the kind: a `DEMO-` namespace builds a rehearsal set (DEMO
    schedules files only; batch and book IDs get the `DEMO-` prefix), any other a real
    set (no DEMO files, human seats only). `seats` maps every unit ID to its three seats.
    """
    demo = is_demo(set_ns)
    perm_paths = {}
    definitions = read_batch_table(batch_table)
    for definition in definitions:
        perm_paths[definition.unit_id] = Path(units) / definition.unit_id / "permutation.json"
    flags = {_demo_flag(book_key), *(_demo_flag(p) for p in perm_paths.values())}
    if flags != {demo}:
        raise BatchSetupError(
            E_INPUTS,
            "a DEMO- set namespace needs DEMO schedules files, and a real one real files",
        )
    if set(seats) != {d.unit_id for d in definitions}:
        raise BatchSetupError(
            E_SEATS,
            f"--seats must name exactly the units {sorted(d.unit_id for d in definitions)}",
        )
    raters = [s.rater_id for unit in seats.values() for s in unit]
    repeated = sorted({r for r in raters if raters.count(r) > 1})
    if repeated:
        raise BatchSetupError(E_SEATS, f"a rater sits on one panel only: {repeated}")
    if not demo and any(s.kind != "human" for unit in seats.values() for s in unit):
        raise BatchSetupError(E_SEATS, "bot seats are for DEMO rehearsal sets only")
    fallback_set: FallbackSet = load_fallback(fallback)
    text = _check_threshold(threshold if threshold is not None else threshold_text(), fallback_set)
    prefix = DEMO_PREFIX if demo else ""
    rows = [(f"{prefix}{d.unit_id}-N1", f"{prefix}{d.unit_id}") for d in definitions]
    profiles = {panel: d.profile.value for (panel, _), d in zip(rows, definitions, strict=True)}
    orders_csv = _panel_orders_csv(set_ns, rows, profiles)
    order_rows = panel_order_rows(set_ns, rows, profiles=profiles)
    sources_common = {
        "batch_table_sha256": file_sha256(batch_table),
        "book_key_sha256": file_sha256(book_key),
        "panel_schedule_sha256": hashlib.sha256(orders_csv).hexdigest(),
    }
    configs = []
    for definition, (panel_id, batch_id), order in zip(definitions, rows, order_rows, strict=True):
        permutation = json.loads(perm_paths[definition.unit_id].read_text(encoding="utf-8"))
        check_permutation(definition, permutation)
        books = tuple(
            BookAssignment(f"{prefix}{b.book_id}", b.method, b.designer_id)
            for b in read_book_key(book_key, definition.unit_id)
        )
        configs.append(
            build_batch_config(
                definition,
                books,
                set_ns=set_ns,
                panel_id=panel_id,
                order_index=int(order["order_index"]),
                raters=seats[definition.unit_id],
                threshold=text,
                fallback=fallback_set,
                fallback_manifest_sha256=file_sha256(fallback),
                batch_id=batch_id,
                sources=ConfigSources(
                    permutation_sha256=file_sha256(perm_paths[definition.unit_id]),
                    **sources_common,
                ),
            )
        )
    return SetConfigs(tuple(configs), orders_csv)


def write_set_configs(built: SetConfigs, out: str | os.PathLike[str]) -> dict[str, Path]:
    """Write `panel-orders.csv` and `<batch_id>/config.json`; refuses to overwrite
    anything and, for a real set, to write inside a git work tree."""
    root = Path(out)
    demo = all(c.set == "demo" for c in built.configs)
    _check_out(root, demo=demo)
    targets = {c.batch_id: root / c.batch_id / CONFIG_NAME for c in built.configs}
    existing = [p for p in (root / PANEL_ORDERS_NAME, *targets.values()) if p.exists()]
    if existing:
        raise BatchSetupError(E_EXISTS, f"refusing to overwrite {[str(p) for p in existing]}")
    root.mkdir(parents=True, exist_ok=True)
    with open(root / PANEL_ORDERS_NAME, "xb") as handle:
        handle.write(built.panel_orders_csv)
    for config in built.configs:
        targets[config.batch_id].parent.mkdir(parents=True, exist_ok=True)
        config.write(targets[config.batch_id])
    return {PANEL_ORDERS_NAME: root / PANEL_ORDERS_NAME, **targets}


# ---------------------------------------------------------------------------
# Rebuild and generation config


def next_rebuild_ids(config: BatchConfig) -> tuple[str, str]:
    """`(panel_id, seed_namespace)` of the next rebuild: panel `<batch>-N<k+1>` after
    `<batch>-N<k>`, seed namespace `<batch>-rb<k>`."""
    match = _PANEL_RE.fullmatch(config.panel.panel_id)
    if match is None:
        raise BatchSetupError(
            E_INPUTS, f"panel {config.panel.panel_id!r} is not <batch>-N<k>; give --panel-id"
        )
    k = int(match[2])
    return f"{config.batch_id}-N{k + 1}", f"{config.batch_id}-rb{k}"


def _cmd_configs(args: argparse.Namespace) -> dict[str, Any]:
    set_ns = args.demo_set_ns if args.demo_set_ns else read_set_namespace(args.set_ns_file)
    if args.demo_set_ns and not is_demo(args.demo_set_ns):
        raise BatchSetupError(E_INPUTS, "--demo-set-ns must start with DEMO-")
    built = build_set_configs(
        batch_table=args.batch_table,
        units=args.units,
        book_key=args.book_key,
        fallback=args.fallback,
        set_ns=set_ns,
        seats=_seats_by_unit(args.seats, kind=args.seat_kind),
    )
    paths = write_set_configs(built, args.out)
    return {
        "panel_orders": {
            "path": str(paths[PANEL_ORDERS_NAME]),
            "sha256": hashlib.sha256(built.panel_orders_csv).hexdigest(),
        },
        "batches": [
            {
                "batch_id": c.batch_id,
                "set": c.set,
                "profile": c.profile.value,
                "designer": next(b.designer_id for b in c.books if b.designer_id),
                "panel_id": c.panel.panel_id,
                "order_index": c.panel.order_index,
                "seats": [f"{s.rater_id}@{s.station}" for s in c.panel.raters],
                "config": str(paths[c.batch_id]),
                "config_sha256": c.sha256(),
                "file_sha256": file_sha256(paths[c.batch_id]),
            }
            for c in built.configs
        ],
    }


def _cmd_rebuild(args: argparse.Namespace) -> dict[str, Any]:
    config = BatchConfig.read(args.config).check_consistency()
    demo = config.set == "demo"
    set_ns = args.demo_set_ns if demo else read_set_namespace(args.set_ns_file or "")
    if demo != bool(args.demo_set_ns) or (demo and not is_demo(set_ns)):
        raise BatchSetupError(
            E_INPUTS, "a DEMO config takes --demo-set-ns DEMO-..., a real one --set-ns-file"
        )
    panel_id, seed_ns = next_rebuild_ids(config)
    kind: SeatKind = "bot" if demo and config.panel.raters[0].kind == "bot" else "human"
    rebuilt = rebuild_batch_config(
        config,
        set_ns=set_ns,
        panel_id=args.panel_id or panel_id,
        raters=parse_seats(args.seats, kind=kind),
        seed_namespace=args.seed_namespace or seed_ns,
    )
    out = Path(args.out)
    _check_out(out, demo=demo)
    if out.exists():
        raise BatchSetupError(E_EXISTS, f"refusing to overwrite {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    sha = rebuilt.write(out)
    return {
        "batch_id": rebuilt.batch_id,
        "panel_id": rebuilt.panel.panel_id,
        "seed_namespace": rebuilt.seed_namespace,
        "seats": [f"{s.rater_id}@{s.station}" for s in rebuilt.panel.raters],
        "config": str(out),
        "config_sha256": rebuilt.sha256(),
        "file_sha256": sha,
    }


def _cmd_generation_config(args: argparse.Namespace) -> dict[str, Any]:
    demo = is_demo(args.name)
    out = Path(args.out)
    _check_out(out, demo=demo)
    if out.exists():
        raise BatchSetupError(E_EXISTS, f"refusing to overwrite {out}")
    meanings = load_meanings(args.meanings)
    fallback_set = load_fallback(args.fallback)
    text = _check_threshold(threshold_text(args.threshold_config), fallback_set)
    prompts = load_prompt_set(
        args.prompts if args.prompts else default_prompt_set_dir(), meanings=meanings
    )
    load_llm_manifest(args.llm_manifest)
    config: GenerationConfig = build_generation_config(
        args.name,
        llm_manifest_sha256=manifest_sha256(args.llm_manifest),
        decoding_schema_sha256=decoding_schema_sha256(),
        prompts=prompts.hashes(),
        meanings_sha256=meanings.sha256(),
        separation_threshold=text,
        fallback=fallback_pins(fallback_set),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    sha = config.write(out)
    return {
        "name": config.name,
        "demo": config.demo,
        "config": str(out),
        "frozen_sha256": config.frozen_sha256(),
        "file_sha256": sha,
        "separation_threshold": text,
        "meanings_sha256": config.meanings_sha256,
    }


# ---------------------------------------------------------------------------
# Command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m av_generation.batch_setup",
        description="Build the input files of Study A batch runs (#311).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    cfg = sub.add_parser("configs", help="batch configs and panel orders of a set")
    cfg.add_argument("--batch-table", required=True, help="<set>-batch-table.csv (#29)")
    cfg.add_argument("--units", required=True, help="folder with <unit>/permutation.json")
    cfg.add_argument("--book-key", required=True, help="<set>-book-key.json (#31)")
    cfg.add_argument("--fallback", required=True, help="fallback-manifest.json")
    ns = cfg.add_mutually_exclusive_group(required=True)
    ns.add_argument("--set-ns-file", help="restricted set namespace file (real sets)")
    ns.add_argument("--demo-set-ns", help="DEMO- set namespace (rehearsal sets)")
    cfg.add_argument(
        "--seats",
        action="append",
        required=True,
        metavar="UNIT=R@S,R@S,R@S",
        help="the three seats of one batch, e.g. A-P01=R01@S1,R02@S2,R03@S3 (repeat)",
    )
    cfg.add_argument("--seat-kind", choices=("human", "bot"), default="human")
    cfg.add_argument("--out", required=True, help="output folder (restricted for real sets)")
    reb = sub.add_parser("rebuild", help="config of a rebuilt batch (new panel, aliases, seeds)")
    reb.add_argument("--config", required=True, help="the incomplete batch's config.json")
    rns = reb.add_mutually_exclusive_group(required=True)
    rns.add_argument("--set-ns-file", help="restricted set namespace file (real sets)")
    rns.add_argument("--demo-set-ns", help="DEMO- set namespace (rehearsal sets)")
    reb.add_argument("--seats", required=True, help="the new panel, e.g. R10@S1,R11@S2,R12@S3")
    reb.add_argument("--panel-id", help="default: <batch>-N<k+1>")
    reb.add_argument("--seed-namespace", help="default: <batch>-rb<k>")
    reb.add_argument("--out", required=True, help="the new config.json")
    gen = sub.add_parser("generation-config", help="the generation config of a set")
    gen.add_argument("--name", required=True, help="e.g. pilot-1 (DEMO-... for rehearsals)")
    gen.add_argument("--meanings", required=True, help="meaning-set directory")
    gen.add_argument("--fallback", required=True, help="fallback-manifest.json")
    gen.add_argument("--prompts", help="prompt-set directory (default: generation/prompts)")
    gen.add_argument("--llm-manifest", help="LLM manifest (default: generation/llm)")
    gen.add_argument("--threshold-config", help="default: sound/config/validator.json")
    gen.add_argument("--out", required=True, help="generation-config.json to write")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m av_generation.batch_setup configs|rebuild|generation-config ...`
    (exit 0 written, 2 refused)."""
    args = build_parser().parse_args(argv)
    commands = {
        "configs": _cmd_configs,
        "rebuild": _cmd_rebuild,
        "generation-config": _cmd_generation_config,
    }
    try:
        report = commands[args.command](args)
    except (RuntimeError, ValueError, OSError, KeyError) as err:
        sys.stderr.write(f"error: {err}\n")
        return 2
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
