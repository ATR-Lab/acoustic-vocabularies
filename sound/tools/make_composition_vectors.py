"""Write the message-composition test vectors (synthetic books and patterns only).

Usage (from the repository root):

    uv run --project sound python sound/tools/make_composition_vectors.py
    uv run --project sound python sound/tools/make_composition_vectors.py --check
    uv run --project sound python sound/tools/make_composition_vectors.py --wav-dir OUT

The first form rewrites sound/testvectors/composition/vectors.json. `--check` exits
with status 1 if the file differs from a fresh build. `--wav-dir` also writes the
atom WAVs and the trained-message WAVs of each synthetic book under OUT (for the
Unity composer tests, #64). Never commit WAV files.

A change in any hash means rendered or composed bytes changed: bump
RENDERER_VERSION or the composition contract in the same pull request.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from av_sound import (
    GAP_SAMPLES,
    RENDERER_VERSION,
    AtomAudio,
    Profile,
    compose_message,
    composite_hash,
    file_sha256,
    message_length,
    render,
    write_message_wav,
    write_wav,
)
from av_sound.grammar import MESSAGES
from av_sound.synthetic import synthetic_book_id, synthetic_recipes

OUT = Path(__file__).resolve().parents[1] / "testvectors" / "composition" / "vectors.json"
FORMAT_VERSION = "1.0.0"

# Renderer-independent vectors: sample i of a part is (mul * i + add) mod 65535 - 32767,
# int16 LE. A consumer can generate them in a few lines and check its concatenation
# and hashing without any audio file. Lengths cover the shortest and longest messages.
PATTERNS: list[tuple[str, tuple[int, int, int], tuple[int, int, int]]] = [
    ("pattern-shortest", (21_600, 7, 0), (21_600, 13, 1_000)),
    ("pattern-longest", (43_200, 3, 32_767), (43_200, 11, 5)),
    ("pattern-short-long", (21_600, 1, 0), (43_200, 2, 1)),
    ("pattern-long-short", (43_200, 257, 99), (21_600, 65_534, 0)),
]


def pattern_pcm(n_samples: int, mul: int, add: int) -> bytes:
    i = np.arange(n_samples, dtype=np.int64)
    return ((i * mul + add) % 65_535 - 32_767).astype("<i2").tobytes()


def book_atoms(profile: Profile) -> tuple[dict[str, AtomAudio], list[dict[str, object]]]:
    atoms: dict[str, AtomAudio] = {}
    rows: list[dict[str, object]] = []
    for atom_id, recipe in synthetic_recipes(profile).items():
        rendered = render(recipe, profile)
        atoms[atom_id] = AtomAudio.from_rendered(atom_id, rendered)
        rows.append(
            {
                "atom_id": atom_id,
                "recipe": recipe.to_dict(),
                "recipe_sha256": recipe.sha256(),
                "n_samples": rendered.n_samples,
                "pcm_sha256": rendered.pcm_sha256,
                "file_sha256": file_sha256(rendered),
            }
        )
    return atoms, rows


def book_vectors(profile: Profile) -> dict[str, object]:
    atoms, atom_rows = book_atoms(profile)
    messages = []
    for m in MESSAGES:
        action, referent = atoms[m.action.atom_id], atoms[m.referent.atom_id]
        n_samples = message_length(action, referent)
        digest = composite_hash(action, referent)
        if not m.is_heldout:
            assert compose_message(action, referent).pcm_sha256 == digest
        messages.append(
            {
                "message_id": m.message_id,
                "action_id": m.action.atom_id,
                "referent_id": m.referent.atom_id,
                "matrix_status": m.status,
                "heldout": m.is_heldout,
                "heldout_set": m.heldout_set,
                "training_wave": m.training_wave,
                "n_samples": n_samples,
                "duration_ms": n_samples // 48,
                "composite_sha256": digest,
            }
        )
    return {
        "book_id": synthetic_book_id(profile),
        "profile": profile.value,
        "atoms": atom_rows,
        "messages": messages,
    }


def pattern_vectors() -> list[dict[str, object]]:
    rows = []
    for name, (a_n, a_mul, a_add), (r_n, r_mul, r_add) in PATTERNS:
        action = AtomAudio("K-a1", Profile.P1, pattern_pcm(a_n, a_mul, a_add))
        referent = AtomAudio("K-r1", Profile.P1, pattern_pcm(r_n, r_mul, r_add))
        rows.append(
            {
                "name": name,
                "action": {"n_samples": a_n, "mul": a_mul, "add": a_add},
                "referent": {"n_samples": r_n, "mul": r_mul, "add": r_add},
                "action_pcm_sha256": action.pcm_sha256,
                "referent_pcm_sha256": referent.pcm_sha256,
                "n_samples": message_length(action, referent),
                "composite_sha256": composite_hash(action, referent),
            }
        )
    return rows


def build() -> dict[str, object]:
    return {
        "format": "av-sound composition test vectors",
        "format_version": FORMAT_VERSION,
        "synthetic": True,
        "renderer_version": RENDERER_VERSION,
        "sample_rate": 48_000,
        "channels": 1,
        "sample_format": "int16 little-endian",
        "gap_samples": GAP_SAMPLES,
        "composite": "sha256(pcm(action) || 2 * gap_samples zero bytes || pcm(referent))",
        "pattern_rule": "sample[i] = (mul * i + add) mod 65535 - 32767",
        "books": [book_vectors(p) for p in Profile],
        "patterns": pattern_vectors(),
    }


def render_text() -> str:
    return json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_wavs(out_dir: Path) -> int:
    count = 0
    for profile in Profile:
        atoms, _ = book_atoms(profile)
        book_dir = out_dir / synthetic_book_id(profile)
        (book_dir / "atoms").mkdir(parents=True, exist_ok=True)
        (book_dir / "messages").mkdir(parents=True, exist_ok=True)
        for atom_id, atom in atoms.items():
            write_wav(atom.pcm, book_dir / "atoms" / f"{atom_id}.wav")
            count += 1
        for m in MESSAGES:
            if m.is_heldout:
                continue
            message = compose_message(atoms[m.action.atom_id], atoms[m.referent.atom_id])
            write_message_wav(message, book_dir / "messages" / f"{m.message_id}.wav")
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    parser.add_argument("--wav-dir", type=Path, help="also write synthetic WAVs here")
    args = parser.parse_args(argv)
    text = render_text()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT} is out of date; rerun without --check", file=sys.stderr)
            return 1
        print(f"{OUT} is up to date")
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"wrote {OUT}")
    if args.wav_dir is not None:
        print(f"wrote {write_wavs(args.wav_dir)} WAV files under {args.wav_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
