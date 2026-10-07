"""Synthetic reference inputs for SYNTHETIC data roots (#33): DEMO package JSON, the
package-hash mapping, Study B store snapshots and selection receipts, and the reveal log.

Everything here is synthetic and derived from a public ``DEMO-`` label by SHA-256: no
audio exists, hashes are ``sha256("|".join(parts))`` of descriptive strings. The
documents follow the shapes of their producers so the reference loader is exercised on
the real formats: ``av-sound/package`` manifests and ``audio.json`` (package format 1,
``docs/interfaces/package-format.md``), ``av-schedules/package-hashes``, the menu-store
bridge's ``verified_snapshot`` and selection receipts (logical IDs in the ``DEMO-``
namespace, ``source_kind: synthetic``), and the ``av-schedules/reveal-log`` written by
``av_schedules.reveal.RevealLog`` itself with a fixed clock. No module of another stack
is imported.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from av_schedules.matrix import atom_wave, atoms, cells
from av_schedules.reveal import RevealLog
from av_schedules.run_sheets import package_hashes_document

from .fileio import json_bytes, read_bytes, sha256_bytes
from .ledger import components, is_message
from .references import canonical_sha256, combination_key, option_key

PROFILES: Final = ("P1", "P2", "P3")
ATOM_SAMPLES: Final = (21600, 28800, 36000, 43200)  # 450-900 ms at 48 kHz
GAP_SAMPLES: Final = 9600  # 200 ms grammar gap
SAMPLES_PER_MS: Final = 48
STAFF: Final = "S01"


def h(*parts: object) -> str:
    """SHA-256 of the ``|``-joined parts (synthetic hash values)."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def demo_id(value: str) -> str:
    """The ``DEMO-`` form of a book, bank or unit ID (synthetic package and store IDs)."""
    return value if value.startswith("DEMO-") else f"DEMO-{value}"


def _samples(label: str, package: str, atom: str, extra: object = "") -> int:
    return ATOM_SAMPLES[int(h(label, package, atom, extra, "n")[:8], 16) % len(ATOM_SAMPLES)]


@dataclass(frozen=True)
class SyntheticPackage:
    """A synthetic package's JSON documents (no WAV files exist)."""

    folder: str  # inputs/packages/<folder>/: book ID (A) or bank ID (B)
    manifest_bytes: bytes
    audio_bytes: bytes
    package_sha256: str
    audio: Mapping[str, Any]

    def files(self) -> dict[str, bytes]:
        return {"manifest.json": self.manifest_bytes, "audio.json": self.audio_bytes}


def _manifest(
    study: str,
    folder: str,
    audio_bytes: bytes,
    wavs: Mapping[str, tuple[int, str]],
    extra: Mapping[str, Any],
    label: str,
) -> tuple[bytes, str]:
    files: dict[str, Any] = {
        "answers.json": {"bytes": 4096, "sha256": h(label, folder, "answers.json")},
        "audio.json": {"bytes": len(audio_bytes), "sha256": sha256_bytes(audio_bytes)},
    }
    for path, (n_samples, digest) in sorted(wavs.items()):
        files[path] = {"bytes": 44 + 2 * n_samples, "sha256": digest}
    manifest: dict[str, Any] = {
        "format": "av-sound/package",
        "format_version": 1,
        "study": study,
        "package_id": demo_id(folder),
        "demo": True,
        "builder": {"name": "av-sound", "version": "0.1.0"},
        "renderer_version": "0.1.0",
        "composition_contract": "1.0.0",
        "files": files,
        **extra,
    }
    digest = canonical_sha256(manifest, "package_sha256")
    manifest["package_sha256"] = digest
    return json_bytes(manifest), digest


def package_a(label: str, book_id: str, profile: str) -> SyntheticPackage:
    """A synthetic Study A book package (16 atom files, 18 trained message files)."""
    atom_rows = []
    wavs: dict[str, tuple[int, str]] = {}
    pcm: dict[str, str] = {}
    n: dict[str, int] = {}
    for atom in atoms():
        n[atom] = _samples(label, book_id, atom)
        pcm[atom] = h(label, book_id, atom, "pcm")
        path = f"atoms/{atom}.wav"
        wavs[path] = (n[atom], h(label, book_id, atom, "file"))
        atom_rows.append(
            {
                "atom_id": atom,
                "path": path,
                "n_samples": n[atom],
                "pcm_sha256": pcm[atom],
                "file_sha256": wavs[path][1],
            }
        )
    messages = []
    for c in cells():
        a, r = c.action_atom, c.referent_atom
        total = n[a] + GAP_SAMPLES + n[r]
        trained = c.heldout_set is None
        msg_path = f"messages/{c.message_id}.wav" if trained else None
        file_sha = h(label, book_id, c.message_id, "file") if trained else None
        if msg_path is not None and file_sha is not None:
            wavs[msg_path] = (total, file_sha)
        messages.append(
            {
                "message_id": c.message_id,
                "action_atom": a,
                "referent_atom": r,
                "status": "trained" if trained else "heldout",
                "n_samples": total,
                "duration_ms": total // SAMPLES_PER_MS,
                "composite_sha256": h("composite", pcm[a], pcm[r]),
                "path": msg_path,
                "file_sha256": file_sha,
            }
        )
    audio = {
        "format": "av-sound/package-audio",
        "format_version": 1,
        "study": "A",
        "package_id": demo_id(book_id),
        "profile": profile,
        "atoms": atom_rows,
        "messages": messages,
    }
    audio_bytes = json_bytes(audio)
    extra = {
        "profile": profile,
        "book": {
            "frozen_head": h(label, book_id, "frozen_head"),
            "snapshot_sha256": h(label, book_id, "snapshot"),
            "renderer_hash": h(label, "renderer"),
            "validator_hash": h(label, "validator"),
        },
    }
    manifest_bytes, digest = _manifest("A", book_id, audio_bytes, wavs, extra, label)
    return SyntheticPackage(book_id, manifest_bytes, audio_bytes, digest, audio)


def package_b(label: str, bank_id: str) -> SyntheticPackage:
    """A synthetic Study B dyad package (192 option files; messages composed only)."""
    options = []
    wavs: dict[str, tuple[int, str]] = {}
    pcm: dict[tuple[str, str, int], str] = {}
    n: dict[tuple[str, str, int], int] = {}
    for profile in PROFILES:
        for atom in atoms():
            for rank in range(1, 5):
                key = (profile, atom, rank)
                n[key] = _samples(label, bank_id, atom, f"{profile}{rank}")
                pcm[key] = h(label, bank_id, profile, atom, rank, "pcm")
                path = f"options/{profile}/{atom}-{rank}.wav"
                wavs[path] = (n[key], h(label, bank_id, profile, atom, rank, "file"))
                options.append(
                    {
                        "profile": profile,
                        "atom_id": atom,
                        "rank": rank,
                        "menu": "shown" if rank < 4 else "reserve",
                        "path": path,
                        "n_samples": n[key],
                        "pcm_sha256": pcm[key],
                        "file_sha256": wavs[path][1],
                    }
                )
    messages = []
    for c in cells():
        combos = []
        for profile in PROFILES:
            for ar in range(1, 5):
                for rr in range(1, 5):
                    ka, kr = (profile, c.action_atom, ar), (profile, c.referent_atom, rr)
                    total = n[ka] + GAP_SAMPLES + n[kr]
                    combos.append(
                        {
                            "profile": profile,
                            "action_rank": ar,
                            "referent_rank": rr,
                            "n_samples": total,
                            "duration_ms": total // SAMPLES_PER_MS,
                            "composite_sha256": h("composite", pcm[ka], pcm[kr]),
                        }
                    )
        messages.append(
            {
                "message_id": c.message_id,
                "action_atom": c.action_atom,
                "referent_atom": c.referent_atom,
                "status": "trained" if c.heldout_set is None else "heldout",
                "combinations": combos,
            }
        )
    audio = {
        "format": "av-sound/package-audio",
        "format_version": 1,
        "study": "B",
        "package_id": demo_id(bank_id),
        "profiles": list(PROFILES),
        "waves": [
            {"wave": w, "visit": f"V{w}", "atoms": [a for a in atoms() if atom_wave(a) == w]}
            for w in (1, 2, 3)
        ],
        "options": options,
        "messages": messages,
    }
    audio_bytes = json_bytes(audio)
    extra = {
        "bank": {
            "format": "av-sound/provisional-bank",
            "format_version": 1,
            "bank_sha256": h(label, bank_id, "bank"),
        }
    }
    manifest_bytes, digest = _manifest("B", bank_id, audio_bytes, wavs, extra, label)
    return SyntheticPackage(bank_id, manifest_bytes, audio_bytes, digest, audio)


def package_hashes_bytes(study: str, set_name: str, packages: Mapping[str, str]) -> bytes:
    """The ``av-schedules/package-hashes`` mapping (DEMO, not a placeholder)."""
    doc = package_hashes_document(study, set_name, packages, demo=True)  # type: ignore[arg-type]
    return json_bytes(doc)


# ---------------------------------------------------------------------------------------
# Expected hashes the synthetic station logs (what it "played")


class Played:
    """Hashes the synthetic station logs for a play: file playback logs the file hash;
    composed audio logs the PCM (composite) hash in ``waveform_sha256`` and ``pcm_sha256``."""

    def __init__(
        self,
        package: SyntheticPackage,
        profile: str | None = None,
        ranks: Mapping[str, int] | None = None,
    ) -> None:
        self.study = package.audio["study"]
        self.profile = profile
        self.ranks = dict(ranks or {})
        self.a: dict[str, tuple[str, str | None, int]] = {}  # item -> (pcm, file, ms)
        self.b: dict[str, tuple[str, str | None, int]] = {}
        audio = package.audio
        if self.study == "A":
            for atom in audio["atoms"]:
                self.a[atom["atom_id"]] = (
                    atom["pcm_sha256"],
                    atom["file_sha256"],
                    atom["n_samples"] // SAMPLES_PER_MS,
                )
            for m in audio["messages"]:
                self.a[m["message_id"]] = (
                    m["composite_sha256"],
                    m["file_sha256"],
                    m["duration_ms"],
                )
        else:
            for o in audio["options"]:
                key = option_key(o["atom_id"], o["profile"], o["rank"])
                self.b[key] = (o["pcm_sha256"], o["file_sha256"], o["n_samples"] // SAMPLES_PER_MS)
            for m in audio["messages"]:
                for c in m["combinations"]:
                    key = combination_key(
                        m["message_id"], c["profile"], c["action_rank"], c["referent_rank"]
                    )
                    self.b[key] = (c["composite_sha256"], None, c["duration_ms"])

    def lookup(self, item: str, rank: int | None = None) -> tuple[str, str, int]:
        """(waveform_sha256, pcm_sha256 column, duration ms) of a play of ``item``; for a
        Study B atom menu, ``rank`` names the candidate."""
        if self.study == "A":
            pcm, fil, ms = self.a[item]
        else:
            assert self.profile is not None
            parts = components(item)
            if is_message(item):
                action, referent = parts
                key = combination_key(item, self.profile, self.ranks[action], self.ranks[referent])
            else:
                key = option_key(item, self.profile, rank or self.ranks[item])
            pcm, fil, ms = self.b[key]
        if fil is not None:
            return fil, "", ms
        return pcm, pcm, ms


def nonsemantic_hash(label: str, profile: str) -> str:
    """Synthetic hash of the nonsemantic profile example (no package entry)."""
    return h(label, "nonsemantic-example", profile)


def speech_hash(label: str, speech_id: str) -> str:
    """Synthetic hash of a speech command recording (no package entry; #71)."""
    return h(label, "speech", speech_id)


# ---------------------------------------------------------------------------------------
# Study B store


@dataclass(frozen=True)
class Store:
    """A dyad's synthetic store history."""

    profile: str
    profile_default: bool
    ranks: Mapping[str, int]  # atom -> committed rank
    defaults: frozenset[str]  # atoms whose rank is the menu default
    snapshots: Mapping[str, bytes]  # visit -> verified_snapshot JSON
    receipts: bytes  # receipts.jsonl


def store_history(
    label: str,
    unit_id: str,
    bank_id: str,
    package: SyntheticPackage,
    menu_order: Sequence[str],
    wave_orders: Sequence[Sequence[str]],
    choose: Iterator[int],
) -> Store:
    """Profile choice, atom choices (``choose`` yields 0..3; 0 = no valid choice: the
    default), selection receipts and the snapshot after every Study B visit."""
    played = Played(package, "P1", {})
    bank_sha = h(label, bank_id, "bank")
    config = h(label, unit_id, "config")
    pick = next(choose)
    profile_default = pick == 0
    profile = menu_order[0] if profile_default else menu_order[pick - 1]
    ids = {"unit_id": demo_id(unit_id), "book_id": demo_id(bank_id)}

    def receipt(
        n: int,
        before: str | None,
        menu_key: str,
        rank: int | None,
        hashes: tuple[str | None, str | None],
    ) -> dict[str, Any]:
        after = h(label, unit_id, "head", n)
        doc: dict[str, Any] = {
            "schema_version": 1,
            **ids,
            "bank_sha256": bank_sha,
            "package_sha256": package.package_sha256,
            "request_id": h(label, unit_id, "request", n)[:32],
            "operation": "profile" if menu_key == "profile" else "atom",
            "profile": profile,
            "menu_key": menu_key,
            "rank": rank,
            "request_sha256": h(label, unit_id, "request-sha", n),
            "config_sha256": config,
            "status": "profile_selected" if menu_key == "profile" else "committed",
            "accepted": True,
            "reason": None,
            "before_head": before,
            "after_head": after,
            "before_snapshot_sha256": h(label, unit_id, "snapshot", n - 1),
            "after_snapshot_sha256": h(label, unit_id, "snapshot", n),
            "pcm_sha256": hashes[0],
            "file_sha256": hashes[1],
            "source_kind": "synthetic",
            "participant_ready": False,
        }
        doc["receipt_sha256"] = canonical_sha256(doc, "receipt_sha256")
        return doc

    receipts: list[dict[str, Any]] = [receipt(1, None, "profile", None, (None, None))]
    ranks: dict[str, int] = {}
    defaults: set[str] = set()
    entries: dict[str, dict[str, Any]] = {}
    snapshots: dict[str, bytes] = {}
    for wave, order in enumerate(wave_orders, start=1):
        for atom in order:
            pick = next(choose)
            rank = 1 if pick == 0 else pick
            if pick == 0:
                defaults.add(atom)
            ranks[atom] = rank
            pcm, fil, _ = played.b[option_key(atom, profile, rank)]
            r = receipt(len(receipts) + 1, receipts[-1]["after_head"], atom, rank, (pcm, fil))
            receipts.append(r)
            entries[atom] = {
                "atom_id": atom,
                "profile": profile,
                "rank": rank,
                "pcm_sha256": pcm,
                "file_sha256": fil,
                "selection_receipt_sha256": r["receipt_sha256"],
            }
        visits = (f"V{wave}",) if wave < 3 else ("V3", "W1", "W4")
        for visit in visits:
            snap: dict[str, Any] = {
                "schema_version": 1,
                **ids,
                "bank_sha256": bank_sha,
                "package_sha256": package.package_sha256,
                "config_sha256": config,
                "profile": profile,
                "book_head": receipts[-1]["after_head"],
                "snapshot_sha256": h(label, unit_id, "snapshot", len(receipts)),
                "journal_head": h(label, unit_id, "journal", len(receipts), visit),
                "source_kind": "synthetic",
                "participant_ready": False,
                "entries": [entries[a] for a in sorted(entries)],
                "profile_selection_receipt_sha256": receipts[0]["receipt_sha256"],
            }
            snap["manifest_sha256"] = canonical_sha256(snap, "manifest_sha256")
            snapshots[visit] = json_bytes(snap)
    lines = b"".join((json_compact(r) + "\n").encode("ascii") for r in receipts)
    return Store(profile, profile_default, ranks, frozenset(defaults), snapshots, lines)


def json_compact(doc: Mapping[str, Any]) -> str:
    """Compact sorted-key ASCII JSON (one receipt line)."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ---------------------------------------------------------------------------------------
# Reveal log


def reveal_log(
    list_bytes: bytes,
    study: str,
    people: Sequence[Sequence[str]],
    first_day: str,
) -> bytes:
    """A reveal log that reveals the first ``len(people)`` list entries, in order, to the
    given coded participant IDs (one per Study A slot, two per Study B dyad), written by
    ``av_schedules.reveal.RevealLog`` with a fixed clock (``first_day``: ``YYYY-MM-DD``)."""
    with tempfile.TemporaryDirectory(prefix="av-reveal-") as tmp:
        list_path = Path(tmp) / "list.json"
        log_path = Path(tmp) / "reveal.jsonl"
        list_path.write_bytes(list_bytes)
        tick = iter(range(10**6))

        def clock() -> str:
            minute = next(tick)
            return f"{first_day}T{9 + minute // 60:02d}:{minute % 60:02d}:00+00:00"

        console = RevealLog(list_path, log_path, clock=clock)
        checks = (
            {"consent": True, "compatibility": True, "orientation": True}
            if study == "A"
            else {"consent": True, "screening": True, "compatibility": True, "scheduling": True}
        )
        for ids in people:
            record = console.log_eligibility(list(ids), staff=STAFF, checks=checks)
            console.reveal_next(record, staff=STAFF)
        return read_bytes(log_path)
