"""Golden set for the cross-machine determinism tests (#12). Synthetic inputs only.

The golden set locks the bytes that the renderer, the composer, the nonlexical
assets and the vocabulary store produce. Its manifest (`tests/golden/manifest.json`,
written by `sound/tools/make_goldens.py`) records, per item, the inputs and the
expected hashes. CI recomputes every item on Linux, macOS and Windows (x86_64 and
arm64) and fails on any difference (`sound/docs/golden.md`).

Categories (item ID prefix):

- `recipe/<name>/<profile>`: `GOLDEN_RECIPES` (domain corners) rendered for P1, P2
  and P3.
- `atom/<book>/<atom_id>`: the 16 atoms of each synthetic book (`av_sound.synthetic`).
- `message/<book>/<message_id>`: all 32 messages of each book. Trained messages are
  composed (`compose_message`); held-out messages get only `composite_hash` and no
  audio, as in a package (#13).
- `nonlexical/<asset_id>`: the seven reserved assets (#14).
- `store/<book>`: a round trip through `VocabularyStore` with a fixed clock: create,
  commit the 16 atoms, freeze, read back, re-hash, verify and compose from the
  stored entries.

Every item has `inputs` (what is computed) and `outputs` (what must not change).
`compute_items()` computes outputs from inputs, so a manifest can be re-checked from
its own recorded inputs (`verify_manifest()`), independently of how it was built.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import stat
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from av_sound.composer import AtomAudio, compose_message, composite_hash, message_length
from av_sound.grammar import ATOM_IDS, MESSAGES, parse_atom_id, parse_message_id
from av_sound.nonlexical import ASSET_IDS, ASSET_SPEC_VERSION, nonlexical_asset
from av_sound.recipe import AMPLITUDES, GAPS_MS, TOTAL_MS, Profile, Recipe
from av_sound.renderer import RENDERER_VERSION, render
from av_sound.store import (
    RECORD_VERSION,
    SEMANTIC_LABELS,
    StoreError,
    VocabularyStore,
    snapshot_digest,
)
from av_sound.synthetic import synthetic_book_id, synthetic_recipes
from av_sound.validate import VALIDATOR_VERSION
from av_sound.version import renderer_hash
from av_sound.wav import file_sha256, pcm_from_wav, pcm_sha256, write_wav

FORMAT: Final = "av-sound golden manifest"
FORMAT_VERSION: Final = "1.0.0"

CATEGORIES: Final[tuple[str, ...]] = ("recipe", "atom", "message", "nonlexical", "store")
"""Computation order: messages need atoms, the store needs atoms and messages."""

VERSION_RULES: Final[Mapping[str, tuple[str, ...]]] = {
    "recipe": ("renderer_version",),
    "atom": ("renderer_version",),
    "message": ("renderer_version",),
    "nonlexical": ("renderer_version", "asset_spec_version"),
    "store": ("renderer_version", "validator_version", "store_record_version"),
}
"""Manifest version fields that may justify a change to an existing item of a category.

A renderer change bumps `renderer_version` (renderer spec D10); an asset change bumps
`asset_spec_version` (`sound/docs/nonlexical.md` section 7); a store record or
validator change bumps its own version (`sound/docs/store.md`).
`sound/tools/check_golden_bump.py` enforces this with the rules of the base manifest.
"""

STORE_THRESHOLD: Final = "0.10"
"""Explicit book threshold, so the config file (#9) does not move the store goldens."""

STORE_CLOCK_START: Final = datetime(2026, 1, 1, tzinfo=UTC)
STORE_CLOCK: Final = "2026-01-01T00:00:00.000Z, +1 s per record"

WAV_SUFFIX: Final = ".wav"
_LFS_POINTER_PREFIX: Final = b"version https://git-lfs.github.com/spec/v1"


# ---------------------------------------------------------------------------
# The golden recipe set (synthetic domain corners, not study motifs)


_PITCH_SHAPES: Final[tuple[tuple[str, tuple[int, int, int]], ...]] = (
    ("low", (-6, -6, -6)),
    ("zero", (0, 0, 0)),
    ("high", (6, 6, 6)),
    ("rise", (-6, 0, 6)),
    ("fall", (6, 0, -6)),
)


def _amp_name(amplitudes: tuple[float, float, float]) -> str:
    return "amps-" + "-".join(f"{a:.1f}" for a in amplitudes)


def _golden_recipes() -> tuple[tuple[str, Recipe], ...]:
    out: list[tuple[str, Recipe]] = []
    # Every total_ms x pitch -6 / 0 / +6, uniform and mixed (20).
    for total_ms in TOTAL_MS:
        for shape, pitches in _PITCH_SHAPES:
            out.append(
                (
                    f"t{total_ms}-pitch-{shape}",
                    Recipe(total_ms, pitches, (1, 2, 3), (40, 40), (1.0, 0.8, 0.6)),
                )
            )
    # Every gap pair (9); weights 2/2/3 give rounded event lengths.
    for g1, g2 in itertools.product(GAPS_MS, repeat=2):
        out.append(
            (f"gaps-{g1}-{g2}", Recipe(600, (-3, 2, 5), (2, 2, 3), (g1, g2), (0.8, 0.6, 1.0)))
        )
    # Amplitudes (13): the three uniform triples (one waveform, spec D5), every
    # permutation of 0.6 / 0.8 / 1.0 and four triples with two equal values.
    uniform = [(a, a, a) for a in AMPLITUDES]
    distinct = [(a, b, c) for a, b, c in itertools.permutations(AMPLITUDES)]
    two_equal = [(0.6, 0.6, 1.0), (1.0, 1.0, 0.8), (0.8, 0.6, 0.8), (1.0, 0.8, 1.0)]
    for amps in uniform + distinct + two_equal:
        out.append((_amp_name(amps), Recipe(750, (4, -2, 1), (3, 1, 2), (20, 60), amps)))
    # Rhythm weights and event lengths (13).
    timing_cases: tuple[tuple[str, int, tuple[int, int, int], tuple[int, int]], ...] = (
        ("weights-1-1-1", 900, (1, 1, 1), (60, 20)),  # same layout as 4-4-4: one waveform
        ("weights-4-4-4", 900, (4, 4, 4), (60, 20)),
        ("weights-4-1-1", 900, (4, 1, 1), (40, 20)),
        ("weights-1-4-1", 900, (1, 4, 1), (40, 20)),
        ("weights-1-1-4", 900, (1, 1, 4), (40, 20)),
        ("shortest-event-first", 600, (1, 4, 4), (20, 40)),  # 2,880 samples: shortest legal
        ("shortest-event-middle", 600, (4, 1, 4), (40, 20)),
        ("shortest-event-last", 600, (4, 4, 1), (20, 40)),
        ("rounding-4-2-1", 750, (4, 2, 1), (40, 60)),  # event 1 rounds up, event 3 remainder
        ("rounding-3-4-4", 900, (3, 4, 4), (20, 20)),  # event 1 rounds down, event 2 up
        ("short-event-first", 450, (1, 4, 4), (60, 60)),  # 1,760 samples: flagged
        ("short-event-middle", 450, (4, 1, 4), (60, 60)),
        ("longest-rejected-event", 450, (3, 3, 1), (20, 20)),  # 2,812 samples: flagged
    )
    for name, total_ms, weights, gaps in timing_cases:
        out.append((name, Recipe(total_ms, (2, -4, 0), weights, gaps, (0.6, 1.0, 0.8))))
    # The remaining pitch values -5..-1 and 1..5, and wide jumps (4).
    sweeps: tuple[tuple[str, int, tuple[int, int, int]], ...] = (
        ("pitch-sweep-down", 450, (-5, -4, -3)),
        ("pitch-sweep-mid", 600, (-2, -1, 1)),
        ("pitch-sweep-up", 750, (2, 3, 4)),
        ("pitch-wide-jumps", 900, (6, -6, 5)),
    )
    for name, total_ms, pitches in sweeps:
        out.append((name, Recipe(total_ms, pitches, (2, 3, 2), (60, 40), (0.8, 1.0, 0.6))))
    # Reference cases from the renderer spec (2).
    out.append(
        ("spec-worked-example", Recipe(600, (-3, 0, 4), (2, 1, 3), (40, 20), (1.0, 0.6, 0.8)))
    )
    out.append(
        ("worst-crest-factor", Recipe(450, (-2, -2, -2), (1, 1, 3), (60, 60), (0.6, 1.0, 0.6)))
    )
    names = [name for name, _ in out]
    assert len(set(names)) == len(names), "golden recipe names must be unique"
    return tuple(out)


GOLDEN_RECIPES: Final[tuple[tuple[str, Recipe], ...]] = _golden_recipes()
"""(name, recipe) pairs: 61 synthetic recipes covering the corners of the domain."""


# ---------------------------------------------------------------------------
# Items


@dataclass(frozen=True, slots=True)
class GoldenItem:
    """One golden item. `pcm` is the audio (int16 LE) when the item has a WAV file."""

    id: str
    category: str
    inputs: Mapping[str, Any]
    outputs: Mapping[str, Any]
    pcm: bytes | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """The manifest entry: `id`, `category`, `inputs`, `outputs`."""
        return {
            "id": self.id,
            "category": self.category,
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
        }


@dataclass(frozen=True, slots=True)
class Mismatch:
    """One difference between expected and actual goldens.

    `item_id` is an item ID or `"<manifest>"`; `field` names what differs, for example
    `outputs.pcm_sha256`, `item`, `inputs`, `file` or `digests.recipe`.
    """

    item_id: str
    field: str
    expected: Any
    actual: Any

    def __str__(self) -> str:
        return f"{self.item_id}: {self.field}: expected {self.expected!r}, got {self.actual!r}"


def golden_specs() -> list[dict[str, Any]]:
    """The golden set as defined in code: one `{id, category, inputs}` per item."""
    specs: list[dict[str, Any]] = []
    for name, recipe in GOLDEN_RECIPES:
        for profile in Profile:
            specs.append(
                {
                    "id": f"recipe/{name}/{profile.value}",
                    "category": "recipe",
                    "inputs": {"name": name, "profile": profile.value, "recipe": recipe.to_dict()},
                }
            )
    for profile in Profile:
        book = synthetic_book_id(profile)
        for atom_id, recipe in synthetic_recipes(profile).items():
            specs.append(
                {
                    "id": f"atom/{book}/{atom_id}",
                    "category": "atom",
                    "inputs": {
                        "book_id": book,
                        "atom_id": atom_id,
                        "profile": profile.value,
                        "recipe": recipe.to_dict(),
                    },
                }
            )
        for m in MESSAGES:
            specs.append(
                {
                    "id": f"message/{book}/{m.message_id}",
                    "category": "message",
                    "inputs": {
                        "book_id": book,
                        "message_id": m.message_id,
                        "action_id": m.action.atom_id,
                        "referent_id": m.referent.atom_id,
                        "heldout": m.is_heldout,
                    },
                }
            )
        specs.append(
            {
                "id": f"store/{book}",
                "category": "store",
                "inputs": {
                    "book_id": book,
                    "profile": profile.value,
                    "kind": "synthetic",
                    "threshold": STORE_THRESHOLD,
                    "clock": STORE_CLOCK,
                    "reserved": "none",
                    "commit_order": list(ATOM_IDS),
                    "labels": "matrix index i -> i-th ontology label",
                    "source": "golden-<atom_id>",
                },
            }
        )
    for asset_id in ASSET_IDS:
        specs.append(
            {
                "id": f"nonlexical/{asset_id}",
                "category": "nonlexical",
                "inputs": {"asset_id": asset_id},
            }
        )
    return sorted(specs, key=lambda s: str(s["id"]))


def _audio_outputs(pcm: bytes) -> dict[str, Any]:
    return {
        "n_samples": len(pcm) // 2,
        "pcm_sha256": pcm_sha256(pcm),
        "file_sha256": file_sha256(pcm),
    }


def _recipe_item(spec: Mapping[str, Any]) -> GoldenItem:
    inputs = spec["inputs"]
    recipe = Recipe.from_dict(inputs["recipe"])
    rendered = render(recipe, inputs["profile"])
    outputs: dict[str, Any] = {
        "recipe_sha256": recipe.sha256(),
        "event_samples": list(rendered.event_samples),
        "short_event": rendered.short_event,
        "overflow": rendered.overflow,
        "peak": rendered.peak,
    }
    pcm = rendered.pcm  # OverflowError outside the domain; never for a valid recipe (spec D6)
    outputs |= _audio_outputs(pcm)
    return GoldenItem(spec["id"], spec["category"], inputs, outputs, pcm)


def _atom_item(spec: Mapping[str, Any]) -> GoldenItem:
    inputs = spec["inputs"]
    parse_atom_id(inputs["atom_id"])
    recipe = Recipe.from_dict(inputs["recipe"])
    pcm = render(recipe, inputs["profile"]).pcm
    outputs = {"recipe_sha256": recipe.sha256(), **_audio_outputs(pcm)}
    return GoldenItem(spec["id"], spec["category"], inputs, outputs, pcm)


def _message_item(
    spec: Mapping[str, Any], atoms: Mapping[tuple[str, str], GoldenItem]
) -> GoldenItem:
    inputs = spec["inputs"]
    book = inputs["book_id"]
    ref = parse_message_id(inputs["message_id"])
    action = _atom_audio(atoms[(book, inputs["action_id"])])
    referent = _atom_audio(atoms[(book, inputs["referent_id"])])
    if (ref.action.atom_id, ref.referent.atom_id, ref.is_heldout) != (
        action.atom_id,
        referent.atom_id,
        inputs["heldout"],
    ):
        raise ValueError(f"{spec['id']}: atoms or held-out flag do not match the message ID")
    digest = composite_hash(action, referent)
    if ref.is_heldout:
        # Held-out messages never exist as complete audio (composition contract section 4).
        outputs: dict[str, Any] = {
            "n_samples": message_length(action, referent),
            "pcm_sha256": digest,
            "file_sha256": None,
        }
        return GoldenItem(spec["id"], spec["category"], inputs, outputs, None)
    message = compose_message(action, referent)
    outputs = _audio_outputs(message.pcm)
    outputs["composite_matches"] = message.pcm_sha256 == digest
    return GoldenItem(spec["id"], spec["category"], inputs, outputs, message.pcm)


def _atom_audio(item: GoldenItem) -> AtomAudio:
    assert item.pcm is not None
    return AtomAudio(item.inputs["atom_id"], item.inputs["profile"], item.pcm)


def _nonlexical_item(spec: Mapping[str, Any]) -> GoldenItem:
    inputs = spec["inputs"]
    asset = nonlexical_asset(inputs["asset_id"])
    pcm = asset.pcm
    outputs: dict[str, Any] = {
        "kind": asset.kind,
        "profile": None if asset.profile is None else asset.profile.value,
        "peak": asset.peak,
        **_audio_outputs(pcm),
    }
    return GoldenItem(spec["id"], spec["category"], inputs, outputs, pcm)


def fixed_clock(start: datetime = STORE_CLOCK_START) -> Callable[[], datetime]:
    """A store clock that starts at `start` and advances one second per call."""
    calls = itertools.count()
    return lambda: start + timedelta(seconds=next(calls))


def demo_label(atom_id: str) -> str:
    """Synthetic meaning of an atom: matrix index i -> the i-th ontology label."""
    atom = parse_atom_id(atom_id)
    return SEMANTIC_LABELS[(atom.family, atom.role)][atom.index - 1]


def _make_writable(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)


def store_round_trip(
    spec: Mapping[str, Any],
    atoms: Mapping[str, GoldenItem],
    messages: Sequence[GoldenItem] = (),
) -> dict[str, Any]:
    """Commit a book's atoms into a fresh store in a temporary directory and read it back.

    `atoms` maps atom ID to the book's atom items (recipes and expected waveforms);
    `messages` are the book's message items, recomposed from the stored entries. The
    result records the chain heads, the snapshot digest and whether the read-back
    entries, the blobs, the verification and the recomposed messages all agree.
    """
    inputs = spec["inputs"]
    book = inputs["book_id"]
    order: list[str] = list(inputs["commit_order"])
    with tempfile.TemporaryDirectory(prefix="av-golden-store-") as tmp:
        root = Path(tmp)
        try:
            return _round_trip(root, inputs, book, order, atoms, messages)
        except StoreError as err:
            return {"error": f"{type(err).__name__} {err.code}: {err}"}
        finally:
            _make_writable(root)  # read-only blobs: Windows needs this before cleanup


def _round_trip(
    root: Path,
    inputs: Mapping[str, Any],
    book: str,
    order: Sequence[str],
    atoms: Mapping[str, GoldenItem],
    messages: Sequence[GoldenItem],
) -> dict[str, Any]:
    if inputs["reserved"] != "none" or inputs["kind"] != "synthetic":
        raise ValueError("golden store round trips use synthetic books without reserved signals")
    store = VocabularyStore(root, clock=fixed_clock(), reserved=())
    create_head = store.create_book(
        book, inputs["profile"], kind=inputs["kind"], threshold=inputs["threshold"]
    )
    recipes = {a: Recipe.from_dict(atoms[a].inputs["recipe"]) for a in order}
    for atom_id in order:
        store.commit(
            book, atom_id, demo_label(atom_id), recipes[atom_id], source=f"golden-{atom_id}"
        )
    commit_head = store.head(book)
    freeze_head = store.freeze(book)

    entries = store.list(book)
    readback_ok = [e.atom_id for e in entries] == list(order)
    for entry in entries:
        blob = store.blob_path(entry.pcm_sha256).read_bytes()
        readback_ok &= (
            pcm_sha256(entry.pcm) == entry.pcm_sha256
            and hashlib.sha256(blob).hexdigest() == entry.file_sha256
            and pcm_from_wav(blob) == entry.pcm
            and entry.recipe == recipes[entry.atom_id]
            and store.get(book, entry.atom_id).pcm == entry.pcm
        )
    snapshot = store.snapshot_hashes(book)
    expected = {a: atoms[a].outputs.get("pcm_sha256") for a in order}
    report = store.verify(book, rerender=True, expected_head=freeze_head)
    by_atom = {e.atom_id: e for e in entries}
    messages_match = all(
        composite_hash(by_atom[m.inputs["action_id"]], by_atom[m.inputs["referent_id"]])
        == m.outputs.get("pcm_sha256")
        for m in messages
    )
    return {
        "create_head": create_head,
        "commit_head": commit_head,
        "freeze_head": freeze_head,
        "snapshot_sha256": snapshot_digest(snapshot),
        "n_entries": len(entries),
        "n_records": report.n_records,
        "readback_ok": readback_ok,
        "atoms_match": snapshot == expected,
        "messages_checked": len(messages),
        "messages_match": messages_match,
        "verify_ok": report.ok,
        "verify_codes": list(report.codes),
    }


def compute_items(specs: Iterable[Mapping[str, Any]]) -> list[GoldenItem]:
    """Compute the outputs of every spec (`{id, category, inputs}`); sorted by ID.

    Message specs need the atom specs of their book, store specs need the atom specs
    (and use the message specs) of their book.
    """
    by_category: dict[str, list[Mapping[str, Any]]] = {c: [] for c in CATEGORIES}
    seen: set[str] = set()
    for spec in specs:
        if spec["category"] not in by_category:
            raise ValueError(f"{spec['id']}: unknown category {spec['category']!r}")
        if spec["id"] in seen:
            raise ValueError(f"duplicate golden item {spec['id']}")
        seen.add(spec["id"])
        by_category[spec["category"]].append(spec)
    items: list[GoldenItem] = [_recipe_item(s) for s in by_category["recipe"]]
    atom_items = [_atom_item(s) for s in by_category["atom"]]
    atoms = {(a.inputs["book_id"], a.inputs["atom_id"]): a for a in atom_items}
    message_items = [_message_item(s, atoms) for s in by_category["message"]]
    items += atom_items + message_items
    items += [_nonlexical_item(s) for s in by_category["nonlexical"]]
    for spec in by_category["store"]:
        book = spec["inputs"]["book_id"]
        book_atoms = {a: item for (b, a), item in atoms.items() if b == book}
        book_messages = [m for m in message_items if m.inputs["book_id"] == book]
        outputs = store_round_trip(spec, book_atoms, book_messages)
        items.append(GoldenItem(spec["id"], spec["category"], spec["inputs"], outputs))
    return sorted(items, key=lambda item: item.id)


# ---------------------------------------------------------------------------
# Manifest


def _canonical(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _as_dicts(items: Iterable[GoldenItem | Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = [i.to_dict() if isinstance(i, GoldenItem) else dict(i) for i in items]
    return sorted(out, key=lambda d: str(d["id"]))


def digests(items: Iterable[GoldenItem | Mapping[str, Any]]) -> dict[str, str]:
    """SHA-256 of the compact canonical JSON of the items, overall (`all`) and per category."""
    dicts = _as_dicts(items)
    out = {"all": hashlib.sha256(_canonical(dicts)).hexdigest()}
    for category in CATEGORIES:
        chosen = [d for d in dicts if d["category"] == category]
        out[category] = hashlib.sha256(_canonical(chosen)).hexdigest()
    return out


def counts(items: Iterable[GoldenItem | Mapping[str, Any]]) -> dict[str, int]:
    """Number of items per category."""
    dicts = _as_dicts(items)
    return {c: sum(d["category"] == c for d in dicts) for c in CATEGORIES}


def engine_versions() -> dict[str, Any]:
    """The header fields a manifest must match: versions and the renderer hash."""
    return {
        "renderer_version": RENDERER_VERSION,
        "renderer_hash": renderer_hash(),
        "validator_version": VALIDATOR_VERSION,
        "asset_spec_version": ASSET_SPEC_VERSION,
        "store_record_version": RECORD_VERSION,
    }


def manifest_from_items(items: Iterable[GoldenItem | Mapping[str, Any]]) -> dict[str, Any]:
    """A complete manifest (header, counts, digests, items) for `items`."""
    dicts = _as_dicts(items)
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "synthetic": True,
        "description": (
            "Golden hashes for the cross-machine determinism tests (#12): synthetic recipes, "
            "synthetic DEMO books, nonlexical assets and a store round trip. Written by "
            "sound/tools/make_goldens.py; not study material."
        ),
        **engine_versions(),
        "version_rules": {c: list(VERSION_RULES[c]) for c in CATEGORIES},
        "counts": counts(dicts),
        "digests": digests(dicts),
        "items": dicts,
    }


def build_manifest() -> dict[str, Any]:
    """Compute the golden set defined in code and return its manifest."""
    return manifest_from_items(compute_items(golden_specs()))


def manifest_text(manifest: Mapping[str, Any]) -> str:
    """The manifest file text: indented JSON with sorted keys and a final LF."""
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def load_manifest(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a manifest file (UTF-8 JSON)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(f"{path} is not an {FORMAT}")
    return data


def specs_of(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The recorded inputs of a manifest's items (`{id, category, inputs}`)."""
    return [
        {"id": i["id"], "category": i["category"], "inputs": i["inputs"]} for i in manifest["items"]
    ]


def compare_items(
    expected: Iterable[GoldenItem | Mapping[str, Any]],
    actual: Iterable[GoldenItem | Mapping[str, Any]],
) -> list[Mismatch]:
    """Every difference between two item lists, in ID order."""
    exp = {d["id"]: d for d in _as_dicts(expected)}
    act = {d["id"]: d for d in _as_dicts(actual)}
    out: list[Mismatch] = []
    for item_id in sorted(exp.keys() | act.keys()):
        if item_id not in act:
            out.append(Mismatch(item_id, "item", "present", "missing"))
            continue
        if item_id not in exp:
            out.append(Mismatch(item_id, "item", "absent", "present"))
            continue
        e, a = exp[item_id], act[item_id]
        for key in ("category", "inputs"):
            if e[key] != a[key]:
                out.append(Mismatch(item_id, key, e[key], a[key]))
        e_out, a_out = e["outputs"], a["outputs"]
        for key in sorted(e_out.keys() | a_out.keys()):
            if e_out.get(key, "<absent>") != a_out.get(key, "<absent>"):
                out.append(
                    Mismatch(
                        item_id,
                        f"outputs.{key}",
                        e_out.get(key, "<absent>"),
                        a_out.get(key, "<absent>"),
                    )
                )
    return out


def header_mismatches(manifest: Mapping[str, Any]) -> list[Mismatch]:
    """Header fields that differ from this engine, and digests or counts that do not
    match the manifest's own items."""
    out: list[Mismatch] = []
    expected_header = {
        "format_version": FORMAT_VERSION,
        **engine_versions(),
        "counts": counts(manifest["items"]),
        "digests": digests(manifest["items"]),
    }
    for key, value in expected_header.items():
        if manifest.get(key) != value:
            out.append(Mismatch("<manifest>", key, manifest.get(key), value))
    return out


def verify_manifest(
    manifest: Mapping[str, Any], items: Sequence[GoldenItem] | None = None
) -> list[Mismatch]:
    """Recompute every item of `manifest` from its recorded inputs and compare.

    Returns header mismatches (`header_mismatches`) followed by item mismatches; an
    empty list means every golden hash reproduces on this machine. Pass `items` to
    compare an already computed set.
    """
    actual = compute_items(specs_of(manifest)) if items is None else items
    return header_mismatches(manifest) + compare_items(manifest["items"], actual)


# ---------------------------------------------------------------------------
# WAV files


def wav_path(root: str | os.PathLike[str], item_id: str) -> Path:
    """`<root>/<category>/.../<last part>.wav` for an item ID."""
    return Path(root, *item_id.split("/")).with_suffix(WAV_SUFFIX)


def write_wavs(items: Iterable[GoldenItem], out_dir: str | os.PathLike[str]) -> int:
    """Write the canonical WAV of every item that has audio; return the file count.

    Held-out messages and store items have no WAV.
    """
    count = 0
    for item in items:
        if item.pcm is None:
            continue
        target = wav_path(out_dir, item.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_wav(item.pcm, target)
        count += 1
    return count


def check_wav_dir(
    items: Iterable[GoldenItem | Mapping[str, Any]],
    wav_dir: str | os.PathLike[str],
    *,
    require_all: bool = False,
) -> list[Mismatch]:
    """Compare every `*.wav` under `wav_dir` with the golden items.

    A file must belong to an item with a `file_sha256`, hash to it, be a canonical
    WAV and, for a `GoldenItem` with audio, equal its samples byte for byte. With
    `require_all`, a missing file is also a mismatch. Git LFS pointers that were not
    fetched are reported as such.
    """
    root = Path(wav_dir)
    expected: dict[str, tuple[str, bytes | None]] = {}
    for item in items:
        entry = item.to_dict() if isinstance(item, GoldenItem) else item
        digest = entry["outputs"].get("file_sha256")
        if digest is not None:
            pcm = item.pcm if isinstance(item, GoldenItem) else None
            expected[entry["id"]] = (digest, pcm)
    out: list[Mismatch] = []
    found: set[str] = set()
    for path in sorted(root.rglob(f"*{WAV_SUFFIX}")):
        item_id = path.relative_to(root).with_suffix("").as_posix()
        found.add(item_id)
        if item_id not in expected:
            out.append(Mismatch(item_id, "file", "a golden item", path.name))
            continue
        digest, pcm = expected[item_id]
        blob = path.read_bytes()
        if blob.startswith(_LFS_POINTER_PREFIX):
            out.append(
                Mismatch(item_id, "file", "WAV bytes", "Git LFS pointer (fetch LFS objects)")
            )
            continue
        actual = hashlib.sha256(blob).hexdigest()
        if actual != digest:
            out.append(Mismatch(item_id, "file_sha256", digest, actual))
        try:
            samples = pcm_from_wav(blob)
        except ValueError as err:
            out.append(Mismatch(item_id, "file", "canonical WAV", str(err)))
            continue
        if pcm is not None and samples != pcm:
            out.append(Mismatch(item_id, "pcm", "rendered samples", _difference(pcm, samples)))
    if require_all:
        for item_id in sorted(expected.keys() - found):
            out.append(Mismatch(item_id, "file", "present", "missing"))
    return out


def _difference(expected: bytes, actual: bytes) -> str:
    """Where two int16 sample buffers first differ, for a readable mismatch."""
    if len(expected) != len(actual):
        return f"{len(actual) // 2} samples instead of {len(expected) // 2}"
    first = next(i for i in range(0, len(expected), 2) if expected[i : i + 2] != actual[i : i + 2])
    return f"differs from sample {first // 2}"
