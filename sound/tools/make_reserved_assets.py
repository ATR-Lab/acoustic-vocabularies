"""Write the reserved-signal registry from the nonlexical assets (#14).

Usage (from the repository root):

    uv run --project sound python sound/tools/make_reserved_assets.py
    uv run --project sound python sound/tools/make_reserved_assets.py --check
    uv run --project sound python sound/tools/make_reserved_assets.py --check --wav-dir OUT
    uv run --project sound python sound/tools/make_reserved_assets.py --table
    uv run --project sound python sound/tools/make_reserved_assets.py --motif-levels

The first form rewrites sound/reserved/registry.json. `--check` exits with status 1
if the file differs from a fresh build. `--wav-dir` also writes one canonical WAV
per asset (`<id>.wav`) and an `index.md` level table under OUT, for listening.
Never commit WAV files. `--table` prints the asset table of sound/docs/nonlexical.md.
`--motif-levels` measures motif event levels over all admissible timing structures
and amplitude triples (pitch 0, P1; about 30 s), for the level comparison in the doc.

A change in any hash means asset bytes changed: bump ASSET_SPEC_VERSION (or
RENDERER_VERSION, if the renderer changed) in the same pull request.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np

from av_sound import RENDERER_VERSION, Profile, Recipe, render, write_wav
from av_sound.nonlexical import (
    ASSET_SPEC_VERSION,
    NonlexicalAsset,
    build_reserved_registry,
    nonlexical_assets,
)
from av_sound.recipe import AMPLITUDES, GAPS_MS, RHYTHM_WEIGHTS, TOTAL_MS
from av_sound.renderer import FULL_SCALE, MIN_EVENT_SAMPLES, RMS_TARGET, event_samples

OUT = Path(__file__).resolve().parents[1] / "reserved" / "registry.json"


def render_text() -> str:
    return json.dumps(build_reserved_registry().to_dict(), indent=2, sort_keys=True) + "\n"


def table(assets: tuple[NonlexicalAsset, ...]) -> str:
    rows = [
        "| ID | Kind | Profile | Samples | ms | Peak dBFS | RMS dBFS | Active RMS dBFS "
        "| `pcm_sha256` |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for a in assets:
        rows.append(
            f"| `{a.id}` | {a.kind} | {a.profile.value if a.profile else 'all'} "
            f"| {a.n_samples:,} | {a.duration_ms:g} | {a.peak_dbfs:.2f} | {a.rms_dbfs:.2f} "
            f"| {a.active_rms_dbfs:.2f} | `{a.pcm_sha256}` |"
        )
    return "\n".join(rows) + "\n"


def write_wavs(out_dir: Path, assets: tuple[NonlexicalAsset, ...]) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for a in assets:
        file_hash = write_wav(a, out_dir / f"{a.id}.wav")
        if file_hash != a.file_sha256:
            raise RuntimeError(f"{a.id}: written file hash differs from the registry")
    index = (
        "# Reserved nonlexical assets (synthetic, #14)\n\n"
        f"Asset spec {ASSET_SPEC_VERSION}, renderer {RENDERER_VERSION}. Mono 48 kHz int16 "
        "canonical WAV. Levels are digital (dBFS re 32,767), not sound pressure.\n"
        "Specification and listening-note template: sound/docs/nonlexical.md.\n\n" + table(assets)
    )
    with (out_dir / "index.md").open("w", encoding="utf-8", newline="\n") as f:
        f.write(index)
    return len(assets)


def _dbfs(sum_sq: int, n: int) -> float:
    return 20 * math.log10(math.sqrt(sum_sq / n) / FULL_SCALE)


def motif_levels() -> str:
    """Active and loudest-event RMS of every admissible structure x amplitude triple."""
    active: list[float] = []
    loudest: list[float] = []
    for total in TOTAL_MS:
        for weights in itertools.product(RHYTHM_WEIGHTS, repeat=3):
            for gaps in itertools.product(GAPS_MS, repeat=2):
                if min(event_samples(total, weights, gaps)) < MIN_EVENT_SAMPLES:
                    continue
                for amps in itertools.product(AMPLITUDES, repeat=3):
                    r = render(Recipe(total, (0, 0, 0), weights, gaps, amps), Profile.P1)
                    t = r.timing
                    events = [
                        r.samples[o : o + n]
                        for o, n in zip(t.event_onsets, t.event_samples, strict=True)
                    ]
                    sums = [int(np.sum(e * e)) for e in events]
                    active.append(_dbfs(sum(sums), sum(t.event_samples)))
                    loudest.append(
                        max(_dbfs(s, n) for s, n in zip(sums, t.event_samples, strict=True))
                    )
    target = 20 * math.log10(RMS_TARGET / FULL_SCALE)
    return (
        f"{len(active)} motifs (pitch 0, P1); RMS target {target:.2f} dBFS\n"
        f"active RMS (events only): min {min(active):.2f}, median {np.median(active):.2f}, "
        f"max {max(active):.2f} dBFS\n"
        f"loudest event RMS: min {min(loudest):.2f}, median {np.median(loudest):.2f}, "
        f"max {max(loudest):.2f} dBFS\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    parser.add_argument("--wav-dir", type=Path, help="also write the asset WAVs here")
    parser.add_argument("--table", action="store_true", help="print the asset table")
    parser.add_argument("--motif-levels", action="store_true", help="print motif levels")
    args = parser.parse_args(argv)
    text = render_text()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT.name} is out of date; rerun without --check", file=sys.stderr)
            return 1
        print(f"{OUT.name} is up to date (renderer {RENDERER_VERSION}).")
    else:
        with OUT.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"wrote {OUT}")
    assets = nonlexical_assets()
    if args.table:
        print()
        print(table(assets))
    if args.wav_dir is not None:
        print(f"wrote {write_wavs(args.wav_dir, assets)} WAV files under {args.wav_dir}")
    if args.motif_levels:
        print(motif_levels())
    return 0


if __name__ == "__main__":
    sys.exit(main())
