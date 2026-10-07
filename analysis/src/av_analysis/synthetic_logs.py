"""Synthetic raw logs from the schedules and run sheets, and fault injection (#33).

``av-analysis synth-logs --demo-seed DEMO-... --out DIR [--study A|B|both]
[--set pilot|confirmatory] [--units N] [--max-persons N]`` creates a SYNTHETIC data root
(``paths.DataRoot.create``): ``inputs/`` from ``av_schedules`` DEMO sets
(``checks.build_set`` gives schedules, allocation lists and run sheets; the run sheets'
``hash_check`` comes from the synthetic package-hash mapping) plus synthetic package JSON,
Study B store snapshots and receipts and a reveal log (``synthetic_inputs``), and
``raw/<visit_id>/`` with the four template CSVs and an exit manifest for every visit type
(A D0, D7; B V1, V2, V3, W1, W4, both dyad members, the second session of each
acquisition visit within 24 h of the first). Clean logs reconcile with zero
discrepancies. Responses are synthetic placeholders drawn from ``seeds.rng`` (no learning
model; #34's ``simulate`` owns outcome models); session IDs carry a ``SYNTHETIC`` marker
and coded participant IDs a ``DEMO-`` marker. Values follow ``vocab`` (the provisional
producer's values, canonical fault codes, times with a UTC offset, coded staff IDs);
exposure event IDs are opaque (no person slot, so C6 rows reveal no role); exit manifests
have ``source`` null.

Every file in ``raw/``, ``inputs/`` and ``keys/`` is written through
``paths.write_synthetic_input`` (SYNTHETIC roots only), never with ``fileio`` directly.

Fault injection: :func:`inject_fault` applies one fault of ``codes.FAULT_INJECTIONS`` to a
visit of a synthetic root, rewriting the raw files and their exit manifest consistently
(``--fault NAME --visit ID [--documented]``); reconciliation must report the mapped code.
With ``documented`` the fault also gets the deviation record (or row link) an operator
would write, so the code is reported as resolved and C8 stays clean.
``unlinked_discrepancy`` is always undocumented. :func:`fault_suite`
(``--fault-suite``) runs every fault on every visit type it applies to, undocumented and
documented, and writes ``fault-suite.csv`` and the reports.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import numpy as np
from av_schedules.assign_output import assign_files
from av_schedules.checks import SetRun, build_set
from av_schedules.design import SetName, Unit, build_units
from av_schedules.matrix import LABELS, cells
from av_schedules.matrix import Study as StudyName
from av_schedules.orders import study_visits, visit_schedule_json, visit_wave
from av_schedules.run_sheets import parse_package_hashes, read_run_sheet, run_sheet_path
from av_schedules.seeds import demo_seed

from . import seeds
from .codes import FAULT_INJECTIONS
from .fileio import csv_bytes, json_bytes, parse_csv, read_bytes, sha256_bytes
from .ledger import components, is_message
from .loaders import raw_visit_ids
from .paths import (
    DEVIATIONS_LOG,
    EXIT_MANIFEST,
    DataRoot,
    WatermarkError,
    parse_visit_id,
    write_synthetic_input,
)
from .reconcile import partner_slot, reconcile_visit, report_bytes
from .references import (
    canonical_sha256,
    combination_key,
    committed,
    heldout_visits,
    input_path,
    load_references,
)
from .schemas import EXIT_MANIFEST_FORMAT, EXIT_MANIFEST_FORMAT_VERSION
from .synthetic_inputs import (
    STAFF,
    Played,
    Store,
    SyntheticPackage,
    h,
    nonsemantic_hash,
    package_a,
    package_b,
    package_hashes_bytes,
    reveal_log,
    speech_hash,
    store_history,
)
from .templates import EXTENSION_COLUMNS, TEMPLATES

TZ: Final = timezone(timedelta(hours=1))
BASE_DATE: Final = date(2027, 3, 1)
DAY_OF_VISIT: Final[dict[str, int]] = {
    "D0": 0,
    "D7": 7,
    "V1": 0,
    "V2": 2,
    "V3": 4,
    "W1": 11,
    "W4": 32,
}
FIRST_HOUR: Final = 9  # Study A sessions and the first session of a Study B dyad visit
SECOND_HOUR: Final = 14  # the second (replayed) Study B acquisition session
STATION: Final = "SYNTHETIC-ST1"
PROTOCOL_VERSION: Final = "v0.1"
MONO_BASE_MS: Final = 1_000_000
SETUP_S: Final = 600
BREAK_S: Final = 60
# Onsets (ms from the slot start) of the scheduled plays of each trial type: profile menu
# and atom menus as in Study B protocol section 5, lessons as in Common procedures
# section 5, test trials one cue 1 s into the slot.
PLAY_OFFSETS_MS: Final[dict[str, tuple[int, ...]]] = {
    "profile_menu": (6500, 10500, 14500, 18500, 22500, 26500, 50000, 54000),
    "atom_menu": (5000, 8000, 11000, 14000, 17000, 20000, 35000, 38000),
    "atomic_lesson": (500, 6000, 14000),
    "message_lesson": (500, 8000, 18000),
    "pre_old": (1000,),
    "trained": (1000,),
    "novel": (1000,),
    "atomic": (1000,),
    "speech": (1000,),
    "no_cue": (),
}
TEST_TYPES: Final = ("pre_old", "trained", "novel", "atomic", "no_cue", "speech")
LESSON_TYPES: Final = ("atomic_lesson", "message_lesson")
TRIAL_HEADER: Final = (*TEMPLATES["trial-log"].columns, *EXTENSION_COLUMNS["trial-log"])
LEDGER_HEADER: Final = (
    *TEMPLATES["exposure-ledger"].columns,
    *EXTENSION_COLUMNS["exposure-ledger"],
)
SHEET_HEADER: Final = TEMPLATES["visit-run-sheet"].columns
DEVIATION_HEADER: Final = TEMPLATES["deviations"].columns
ALL_VISIT_TYPES: Final = ("D0", "D7", "V1", "V2", "V3", "W1", "W4")
# Visit types each injected fault applies to.
FAULT_VISITS: Final[dict[str, tuple[str, ...]]] = {
    "missing_trial": ALL_VISIT_TYPES,
    "extra_play": ALL_VISIT_TYPES,
    "wrong_hash": ALL_VISIT_TYPES,
    "holdout_in_lesson": ("D0", "V1", "V2", "V3"),
    "changed_old_atom": ("V2", "V3", "W1", "W4"),
    "yoked_mismatch": ("V1", "V2", "V3"),
    "late_visit": ("D7", "V2", "V3", "W1", "W4"),
    "broken_retry_of": ALL_VISIT_TYPES,
    "unlinked_discrepancy": ALL_VISIT_TYPES,
}
DEFAULT_SEED: Final = "DEMO-o4.5.1-example"


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------
# One study and set


@dataclass(frozen=True)
class Person:
    """A revealed person slot of the synthetic root."""

    study: str
    set_name: str
    unit_id: str
    person_id: str
    coded_id: str
    order: int  # list order of the slot (A) or dyad (B): the day offset
    package: SyntheticPackage
    role: str | None = None  # Study B only (generator side; never written to outputs)
    member: int | None = None


@dataclass
class _History:
    """Running state of one person's synthetic exposures (mirrors ``ledger.fold``)."""

    phrase: dict[str, int] = field(default_factory=dict)
    atom: dict[str, int] = field(default_factory=dict)
    last: dict[str, datetime] = field(default_factory=dict)

    def add(self, item: str) -> None:
        if is_message(item):
            self.phrase[item] = self.phrase.get(item, 0) + 1
        elif item and components(item):
            self.atom[item] = self.atom.get(item, 0) + 1

    def prior(self, cue: str) -> tuple[int, int]:
        phrase = self.phrase.get(cue, 0) if is_message(cue) else 0
        return phrase, sum(self.atom.get(a, 0) for a in components(cue))


class StudySet:
    """Everything generated for one study and set from a DEMO label."""

    def __init__(self, label: str, study: str, set_name: str) -> None:
        self.label, self.study, self.set_name = label, study, set_name
        self.master = demo_seed(label)
        st: StudyName = "A" if study == "A" else "B"
        sn: SetName = "pilot" if set_name == "pilot" else "confirmatory"
        lists = assign_files(self.master, st, sn)
        self.list_name = f"{set_name}-{'slots' if study == 'A' else 'dyads'}.json"
        self.list_bytes = lists[self.list_name]
        self.list_doc: dict[str, Any] = json.loads(self.list_bytes)
        self.packages: dict[str, SyntheticPackage] = {}  # mapping key -> package
        if study == "A":
            for slot in self.list_doc["slots"]:
                if slot["book_id"] not in self.packages:
                    self.packages[slot["book_id"]] = package_a(
                        label, slot["book_id"], slot["profile"]
                    )
        else:
            for dyad in self.list_doc["dyads"]:
                if dyad["kind"] == "dyad":
                    self.packages[dyad["unit_id"]] = package_b(label, dyad["bank_id"])
        self.mapping_bytes = package_hashes_bytes(
            study, set_name, {k: p.package_sha256 for k, p in self.packages.items()}
        )
        self.run: SetRun = build_set(
            self.master, st, sn, package_hashes=parse_package_hashes(self.mapping_bytes)
        )
        self.units: dict[str, Unit] = {u.unit_id: u for u in build_units(self.master, st, sn)}

    # -- persons -----------------------------------------------------------------------

    def persons(self, units: int = 1, max_persons: int | None = None) -> list[Person]:
        """The person slots revealed in a root: the first ``units`` units in reveal order
        (Study A capped at ``max_persons`` slots)."""
        out: list[Person] = []
        if self.study == "A":
            slots = sorted(self.list_doc["slots"], key=lambda s: s["order"])
            chosen: list[str] = []
            for s in slots:
                if s["unit_id"] not in chosen:
                    chosen.append(s["unit_id"])
            keep = set(chosen[:units])
            for s in slots:
                if s["unit_id"] in keep:
                    out.append(
                        Person(
                            "A",
                            self.set_name,
                            s["unit_id"],
                            s["slot_id"],
                            f"DEMO-A{s['order']:04d}",
                            s["order"],
                            self.packages[s["book_id"]],
                        )
                    )
            return out[:max_persons] if max_persons else out
        dyads = sorted(
            (d for d in self.list_doc["dyads"] if d["kind"] == "dyad"), key=lambda d: d["order"]
        )
        for d in dyads[:units]:
            for m in d["members"]:
                out.append(
                    Person(
                        "B",
                        self.set_name,
                        d["unit_id"],
                        m["slot_id"],
                        f"DEMO-B{d['order']:03d}M{m['member']}",
                        d["order"],
                        self.packages[d["unit_id"]],
                        m["role"],
                        m["member"],
                    )
                )
        return out

    def dyad(self, unit_id: str) -> Mapping[str, Any]:
        return next(d for d in self.list_doc["dyads"] if d["unit_id"] == unit_id)

    def store(self, unit_id: str) -> Store:
        dyad = self.dyad(unit_id)
        rng = seeds.rng(self.label, "synth", unit_id, "choices")

        def choose() -> Iterator[int]:
            while True:  # 0 = no valid choice (default) 5% of the time, else 1..3
                yield 0 if rng.random() < 0.05 else int(rng.integers(1, 4))

        return store_history(
            self.label,
            unit_id,
            dyad["bank_id"],
            self.packages[unit_id],
            dyad["profile_menu_order"],
            self.units[unit_id].wave_orders,
            choose(),
        )

    # -- inputs ------------------------------------------------------------------------

    def input_files(self, persons: Sequence[Person]) -> dict[str, bytes]:
        """``inputs/``-relative files for the revealed persons."""
        s, out = self.study, {}
        out[f"schedules/{s}/{self.list_name}"] = self.list_bytes
        out[f"schedules/{s}/{self.set_name}-package-hashes.json"] = self.mapping_bytes
        for p in persons:
            for visit in study_visits(s):  # type: ignore[arg-type]
                doc = self.run.docs[p.person_id][visit]
                out[f"schedules/{s}/{p.unit_id}/schedules/{p.person_id}/{visit}.json"] = (
                    visit_schedule_json(doc)
                )
                out[f"schedules/{s}/{run_sheet_path(p.unit_id, p.person_id, visit)}"] = (
                    self.run.run_sheets[p.person_id][visit]
                )
            for name, data in p.package.files().items():
                out[f"packages/{p.package.folder}/{name}"] = data
        people: list[list[str]] = []
        for p in persons:
            if s == "A":
                people.append([p.coded_id])
            elif p.member == 1:
                partner = next(q for q in persons if q.unit_id == p.unit_id and q.member == 2)
                people.append([p.coded_id, partner.coded_id])
        first_day = (BASE_DATE - timedelta(days=7)).isoformat()
        out[f"reveal/{s}-{self.set_name}.jsonl"] = reveal_log(self.list_bytes, s, people, first_day)
        if s == "B":
            for unit_id in sorted({p.unit_id for p in persons}):
                store = self.store(unit_id)
                for visit, data in store.snapshots.items():
                    out[f"store-snapshots/{unit_id}/{visit}.json"] = data
                out[f"store-snapshots/{unit_id}/receipts.jsonl"] = store.receipts
        return out

    # -- raw logs ----------------------------------------------------------------------

    def unit_files(self, persons: Sequence[Person]) -> dict[str, dict[str, bytes]]:
        """visit_id -> raw files for the given persons of one unit (Study B: both
        members, the active one generated first)."""
        out: dict[str, dict[str, bytes]] = {}
        if self.study == "A":
            for p in persons:
                history = _History()
                for visit in study_visits("A"):
                    files, _ = self._visit(p, visit, history, None, None)
                    out[f"{p.person_id}-{visit}"] = files
            return out
        unit_id = persons[0].unit_id
        store = self.store(unit_id)
        ordered = sorted(persons, key=lambda p: p.role != "active")
        histories = {p.person_id: _History() for p in ordered}
        for visit in study_visits("B"):
            selection: dict[int, list[dict[str, str]]] | None = None
            for p in ordered:
                files, rows = self._visit(p, visit, histories[p.person_id], store, selection)
                out[f"{p.person_id}-{visit}"] = files
                if p.role == "active":
                    selection = rows
        return out

    def _visit(
        self,
        p: Person,
        visit: str,
        history: _History,
        store: Store | None,
        source: dict[int, list[dict[str, str]]] | None,
    ) -> tuple[dict[str, bytes], dict[int, list[dict[str, str]]]]:
        """Raw files of one visit and its selection plays by run index (for the replay)."""
        study, visit_id = p.study, f"{p.person_id}-{visit}"
        doc = self.run.docs[p.person_id][visit]
        second = p.study == "B" and p.role == "yoked" and visit in ("V1", "V2", "V3")
        day = BASE_DATE + timedelta(days=p.order - 1 + DAY_OF_VISIT[visit])
        hour = SECOND_HOUR if second else FIRST_HOUR
        session_start = datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=TZ)
        session = f"SYNTHETIC-{h(self.label, visit_id, 'session')[:16]}"
        rng = seeds.rng(self.label, "synth", visit_id, "responses")
        wave = visit_wave(study, visit)  # type: ignore[arg-type]
        if study == "A":
            played = Played(p.package)
            profile_order: Sequence[str] = ()
            selected_profile = None
            ranks: Mapping[str, int] = {}
        else:
            assert store is not None
            dyad = self.dyad(p.unit_id)
            profile_order = dyad["profile_menu_order"]
            selected_profile = store.profile
            ranks = {a: r for a, r in store.ranks.items()}
            played = Played(p.package, store.profile, ranks)
        identity = {
            "participant_id": p.coded_id,
            "dyad_id": p.unit_id if study == "B" else "",
            "session_id": session,
        }
        trial_rows: list[dict[str, str]] = []
        play_rows: list[dict[str, str]] = []
        selection: dict[int, list[dict[str, str]]] = {}
        sheet_rows: list[dict[str, str]] = []
        _, ref_sheet = read_run_sheet(self.run.run_sheets[p.person_id][visit])
        t = session_start + timedelta(seconds=SETUP_S)
        run_index = 0
        event_n = 0
        for block, ref_row in zip(doc["blocks"], ref_sheet, strict=True):
            block_start = t
            block_mono = MONO_BASE_MS + int((t - session_start).total_seconds() * 1000)
            for k, item in enumerate(block["items"]):
                slot_mono = block_mono + k * item["slot_s"] * 1000
                slot_time = block_start + timedelta(seconds=k * item["slot_s"])
                trial_type = item["trial_type"]
                cue = item["message_id"] or item["atom_id"] or item["speech_id"] or ""
                offsets = PLAY_OFFSETS_MS[trial_type]
                plays: list[dict[str, str]] = []
                if source is not None and trial_type in ("profile_menu", "atom_menu"):
                    for src in source.get(run_index, []):
                        event_n += 1
                        plays.append(
                            self._replayed(src, visit_id, event_n, slot_mono, item, identity, wave)
                        )
                else:
                    for n, offset in enumerate(offsets, start=1):
                        event_n += 1
                        plays.append(
                            self._play(
                                visit_id,
                                event_n,
                                item,
                                n,
                                offset,
                                slot_mono,
                                identity,
                                wave,
                                played,
                                profile_order,
                                selected_profile,
                                store,
                                cue,
                            )
                        )
                if trial_type in ("profile_menu", "atom_menu"):
                    selection[run_index] = plays
                phrase, atom = history.prior(cue)
                delay = ""
                if cue in history.last and trial_type not in ("profile_menu", "atom_menu"):
                    hours = (slot_time - history.last[cue]).total_seconds() / 3600
                    delay = f"{hours:.2f}"
                trial_rows.append(
                    self._trial(
                        p,
                        visit,
                        item,
                        cue,
                        slot_mono,
                        plays,
                        phrase,
                        atom,
                        delay,
                        identity,
                        rng,
                        played,
                    )
                )
                for play in plays:
                    history.add(play["atom_or_message_id"])
                if trial_type in (*LESSON_TYPES, *TEST_TYPES) and cue:
                    history.last[cue] = slot_time
                play_rows.extend(plays)
                run_index += 1
            t = block_start + timedelta(seconds=block["seconds"])
            row = dict(ref_row)
            row.update(
                actual_count=row["expected_count"],
                start_time=_iso(block_start),
                end_time=_iso(t),
                comfort_check="ok",
                phone_locked="true",
                operator_signoff=STAFF,
            )
            sheet_rows.append(row)
            t += timedelta(seconds=BREAK_S)
        files = {
            "trial-log.csv": _csv(TRIAL_HEADER, trial_rows),
            "exposure-ledger.csv": _csv(LEDGER_HEADER, play_rows),
            "visit-run-sheet.csv": _csv(SHEET_HEADER, sheet_rows),
            "deviations.csv": _csv(DEVIATION_HEADER, []),
        }
        files[EXIT_MANIFEST] = exit_manifest_bytes(visit_id, session, files)
        return files, selection

    def _play(
        self,
        visit_id: str,
        event_n: int,
        item: Mapping[str, Any],
        n: int,
        offset: int,
        slot_mono: int,
        identity: Mapping[str, str],
        wave: int,
        played: Played,
        profile_order: Sequence[str],
        selected_profile: str | None,
        store: Store | None,
        cue: str,
    ) -> dict[str, str]:
        trial_type = item["trial_type"]
        row = dict.fromkeys(LEDGER_HEADER, "")
        row.update(identity)
        onset = slot_mono + offset
        candidate = accepted = choice = meaning = feedback = pcm = ""
        retrieval = "false"
        if trial_type in ("profile_menu", "atom_menu"):
            assert store is not None
        if trial_type == "profile_menu":
            assert store is not None
            preset = profile_order[(n - 1) // 2] if n <= 6 else str(selected_profile)
            waveform, ms = nonsemantic_hash(self.label, preset), 900
            candidate = preset
            accepted = "accepted" if preset == selected_profile else "rejected"
            choice = "default" if store.profile_default else "active_choice"
            item_id = ""
        elif trial_type == "atom_menu":
            assert store is not None
            rank = (n + 1) // 2 if n <= 6 else store.ranks[cue]
            waveform, pcm, ms = played.lookup(cue, rank)
            candidate = f"{cue}-{rank}"
            accepted = "accepted" if rank == store.ranks[cue] else "rejected"
            choice = "default" if cue in store.defaults else "active_choice"
            meaning = f"MD-{cue}"
            item_id = cue
        elif trial_type == "speech":
            waveform, ms, item_id = speech_hash(self.label, cue), 1500, cue
            retrieval = "true"
        else:
            waveform, pcm, ms = played.lookup(cue)
            item_id = cue
            if trial_type in LESSON_TYPES:
                meaning = f"MD-{cue}" if n != 2 else ""
                retrieval = "true" if n == 2 else "false"
                feedback = f"FB-{cue}" if n == 3 else ""
            else:
                retrieval = "true"
        row.update(
            wave=str(wave),
            event_id=f"E{h(self.label, visit_id, 'event', event_n)[:20]}",
            stage=trial_type,
            atom_or_message_id=item_id,
            candidate_id=candidate,
            accepted_or_rejected=accepted,
            waveform_sha256=waveform,
            whole_phrase="true" if is_message(item_id) else "false",
            presentation_index=str(n),
            meaning_display_id=meaning,
            display_start_mono_ms=str(slot_mono),
            display_end_mono_ms=str(slot_mono + item["slot_s"] * 1000),
            audio_onset_mono_ms=str(onset),
            audio_offset_mono_ms=str(onset + ms),
            audible_status="confirmed_audible",
            retrieval_opportunity=retrieval,
            feedback_content_id=feedback,
            active_choice_or_default=choice,
            pause_ms="0",
            pcm_sha256=pcm,
            trial_ref=item["trial_id"],
        )
        return row

    def _replayed(
        self,
        src: Mapping[str, str],
        visit_id: str,
        event_n: int,
        slot_mono: int,
        item: Mapping[str, Any],
        identity: Mapping[str, str],
        wave: int,
    ) -> dict[str, str]:
        row = dict(src)
        row.update(identity)
        rel_onset = int(src["audio_onset_mono_ms"]) - int(src["display_start_mono_ms"])
        length = int(src["audio_offset_mono_ms"]) - int(src["audio_onset_mono_ms"])
        row.update(
            wave=str(wave),
            event_id=f"E{h(self.label, visit_id, 'event', event_n)[:20]}",
            yoked_source_event_id=src["event_id"],
            display_start_mono_ms=str(slot_mono),
            display_end_mono_ms=str(slot_mono + item["slot_s"] * 1000),
            audio_onset_mono_ms=str(slot_mono + rel_onset),
            audio_offset_mono_ms=str(slot_mono + rel_onset + length),
            trial_ref=item["trial_id"],
        )
        return row

    def _trial(
        self,
        p: Person,
        visit: str,
        item: Mapping[str, Any],
        cue: str,
        slot_mono: int,
        plays: Sequence[Mapping[str, str]],
        phrase: int,
        atom: int,
        delay: str,
        identity: Mapping[str, str],
        rng: np.random.Generator,
        played: Played,
    ) -> dict[str, str]:
        trial_type = item["trial_type"]
        intended = item["intended"] or {}
        row = dict.fromkeys(TRIAL_HEADER, "")
        dyad = self.dyad(p.unit_id) if p.study == "B" else None
        row.update(
            study=p.study,
            protocol_version=PROTOCOL_VERSION,
            participant_id=identity["participant_id"],
            dyad_id=identity["dyad_id"],
            batch_id=p.unit_id if p.study == "A" else "",
            codebook_id=p.package.folder,
            session_id=identity["session_id"],
            visit=visit,
            trial_id=item["trial_id"],
            role=p.role or "",
            scaffold_family=dyad["structured_family"] if dyad is not None else "",
            semantic_family=intended.get("family", "") if trial_type != "no_cue" else "",
            trial_type=trial_type,
            message_id=cue,
            trained_status=item["trained_status"],
            prior_complete_phrase_exposures=str(phrase),
            prior_atom_exposures=str(atom),
            feedback_shown="true" if trial_type in LESSON_TYPES else "false",
            dictionary_available="false",
            actual_delay_hours=delay,
            frame_freeze_ms="0",
            reset_ok="true",
            focus_ok="true",
        )
        if intended.get("kind") == "message":
            row.update(
                target_action=intended["semantic_action"],
                target_referent=intended["semantic_referent"],
            )
        elif intended.get("kind") == "atom":
            column = "target_action" if intended["role"] == "action" else "target_referent"
            row[column] = intended["semantic_label"]
        scheduled = slot_mono + (PLAY_OFFSETS_MS[trial_type] or (1000,))[0]
        row["scheduled_onset_mono_ms"] = str(scheduled)
        if plays:
            first = plays[0]
            row.update(
                waveform_sha256=first["waveform_sha256"],
                pcm_sha256=first["pcm_sha256"],
                audio_request_mono_ms=str(int(first["audio_onset_mono_ms"]) - 40),
                audio_onset_estimate_mono_ms=first["audio_onset_mono_ms"],
                onset_uncertainty_ms="5",
                audio_offset_mono_ms=first["audio_offset_mono_ms"],
                playback_status="observed_complete",
                exposure_consumed="true",
            )
        else:
            row.update(playback_status="not_requested", exposure_consumed="false")
        if trial_type in (*TEST_TYPES, *LESSON_TYPES):
            origin = int(row["audio_onset_estimate_mono_ms"] or scheduled)
            row.update(_response(rng, intended, trial_type, origin))
        return row


def _response(
    rng: np.random.Generator, intended: Mapping[str, Any], trial_type: str, origin: int
) -> dict[str, str]:
    """A synthetic placeholder response (no learning model)."""
    u = float(rng.random())
    code = "commit" if u < 0.85 else "dont_know" if u < 0.92 else "timeout"
    out = {"response_code": code}
    lesson = trial_type in LESSON_TYPES
    kind = intended.get("kind")
    family = intended.get("family", "K")
    if code == "commit":
        atom_window = trial_type in ("atomic", "atomic_lesson")
        rt = int(rng.integers(800, 6000)) if atom_window else int(rng.integers(1500, 9000))
        out.update(commit_mono_ms=str(origin + rt), response_time_ms=str(rt))
    want: dict[str, str] = {}
    if kind == "message":
        want = {"action": intended["semantic_action"], "referent": intended["semantic_referent"]}
    elif kind == "atom":
        want = {intended["role"]: intended["semantic_label"]}
    correct = {}
    for role, label in want.items():
        if code == "commit":
            choice = (
                label
                if rng.random() < 0.6
                else str(
                    rng.choice(LABELS[family][role])  # type: ignore[index]
                )
            )
            out[f"response_{'action' if role == 'action' else 'target'}"] = choice
            correct[role] = choice == label
        else:
            correct[role] = False
    if not lesson and want:
        out["exact_correct"] = "true" if correct and all(correct.values()) else "false"
        for role, ok in correct.items():
            out[f"{role}_correct"] = "true" if ok else "false"
    return out


def _csv(header: Sequence[str], rows: Sequence[Mapping[str, str]]) -> bytes:
    return csv_bytes(header, ([row.get(c, "") for c in header] for row in rows))


def exit_manifest_bytes(
    visit_id: str, session_id: str, files: Mapping[str, bytes], *, closed: str = "complete"
) -> bytes:
    """A synthetic exit manifest (``source`` null) listing ``files`` with their hashes."""
    doc = {
        "format": EXIT_MANIFEST_FORMAT,
        "format_version": EXIT_MANIFEST_FORMAT_VERSION,
        "data_kind": "SYNTHETIC",
        "visit_id": visit_id,
        "session_id": session_id,
        "station_id": STATION,
        "closed": closed,
        "source": None,
        "files": [
            {"path": name, "bytes": len(data), "sha256": sha256_bytes(data)}
            for name, data in sorted(files.items())
            if name != EXIT_MANIFEST
        ],
    }
    return json_bytes(doc)


# ---------------------------------------------------------------------------------------
# Roots


def _studies(study: str) -> tuple[str, ...]:
    return ("A", "B") if study == "both" else (study,)


def build_synthetic_root(
    path: Path,
    *,
    seed_label: str,
    study: str = "both",
    set_name: str = "pilot",
    units: int = 1,
    max_persons: int | None = None,
) -> DataRoot:
    """Create a SYNTHETIC data root with inputs and clean raw logs for every visit of the
    persons revealed in it: the first ``units`` units in reveal order of each study
    (Study A capped at ``max_persons`` learner slots)."""
    if path.exists() and any(path.iterdir()):
        raise WatermarkError(f"{path} is not empty: synthetic roots are built in a new folder")
    root = DataRoot.create(path, "SYNTHETIC", study=study, set_name=set_name, label=seed_label)
    write_synthetic_input(root, "raw", DEVIATIONS_LOG, _csv(DEVIATION_HEADER, []))
    for s in _studies(study):
        gen = StudySet(seed_label, s, set_name)
        persons = gen.persons(units, max_persons)
        for rel, data in sorted(gen.input_files(persons).items()):
            write_synthetic_input(root, "inputs", rel, data)
        for unit_id in sorted({p.unit_id for p in persons}):
            unit_persons = [p for p in persons if p.unit_id == unit_id]
            for visit_id, files in sorted(gen.unit_files(unit_persons).items()):
                for name, data in sorted(files.items()):
                    write_synthetic_input(root, "raw", f"{visit_id}/{name}", data)
    return root


def synthetic_visit_files(root: DataRoot, visit_id: str) -> dict[str, bytes]:
    """The raw files of one clean synthetic visit (file name -> bytes), regenerated from
    the root's DEMO label."""
    if not root.synthetic:
        raise WatermarkError("synthetic visit files exist only for SYNTHETIC roots")
    person, _ = parse_visit_id(visit_id)
    unit_id = person[:5]
    set_name = {"P": "pilot"}.get(unit_id[2], "confirmatory")
    gen = StudySet(root.label, person[0], set_name)
    persons = [p for p in gen.persons(units=10**6) if p.unit_id == unit_id]
    if person not in {p.person_id for p in persons}:
        raise ValueError(f"{person} is not a person slot of the {set_name} list")
    if person[0] == "A":
        persons = [p for p in persons if p.person_id == person]
    return gen.unit_files(persons)[visit_id]


# ---------------------------------------------------------------------------------------
# Fault injection


def _read(root: DataRoot, visit_id: str, name: str) -> tuple[list[str], list[dict[str, str]]]:
    data = read_bytes(root.input_path("raw", f"{visit_id}/{name}"))
    header, records = parse_csv(data)
    return list(header), [dict(zip(header, r, strict=True)) for r in records]


def _write(
    root: DataRoot,
    visit_id: str,
    name: str,
    header: Sequence[str],
    rows: Sequence[Mapping[str, str]],
) -> None:
    write_synthetic_input(root, "raw", f"{visit_id}/{name}", _csv(header, rows))


def refresh_exit_manifest(root: DataRoot, visit_id: str) -> None:
    """Rewrite a synthetic visit's exit manifest so it lists the folder's current files."""
    folder = root.raw_visit_dir(visit_id)
    manifest = json.loads(read_bytes(folder / EXIT_MANIFEST))
    files = {
        p.name: read_bytes(p)
        for p in sorted(folder.iterdir())
        if p.is_file() and p.name != EXIT_MANIFEST
    }
    data = exit_manifest_bytes(
        visit_id, manifest["session_id"], files, closed=manifest.get("closed", "complete")
    )
    write_synthetic_input(root, "raw", f"{visit_id}/{EXIT_MANIFEST}", data)


def _add_deviation(
    root: DataRoot,
    visit_id: str,
    event_id: str,
    category: str,
    fault: str,
    *,
    prior_audio: str = "",
    endpoint: str = "",
) -> str:
    header, rows = _read(root, visit_id, "deviations.csv")
    _, sheet = _read(root, visit_id, "visit-run-sheet.csv")
    _, trials = _read(root, visit_id, "trial-log.csv")
    dev_id = f"DEMO-DEV-{visit_id}-{len(rows) + 1}"
    rows.append(
        {
            "deviation_id": dev_id,
            "timestamp": sheet[-1]["end_time"],
            "protocol_version": PROTOCOL_VERSION,
            "operator": STAFF,
            "participant_id": trials[0]["participant_id"] if trials else "",
            "dyad_or_batch": visit_id[:5],
            "event_id": event_id,
            "category": category,
            "observed_problem": f"synthetic fault injection: {fault}",
            "action_taken": "recorded at exit",
            "prior_audio_exposure": prior_audio,
            "affected_endpoint": endpoint,
            "resolution": "",
            "reviewer": "",
        }
    )
    _write(root, visit_id, "deviations.csv", header, rows)
    return dev_id


def _first(rows: Sequence[Mapping[str, str]], *types: str) -> int:
    for wanted in types:
        for i, row in enumerate(rows):
            if row["trial_type"] == wanted and not row["retry_of"]:
                return i
    raise ValueError(f"no trial of type {types}")


def _plays_of(plays: Sequence[Mapping[str, str]], trial_id: str) -> list[int]:
    return [i for i, p in enumerate(plays) if p["trial_ref"] == trial_id]


def _yoked_member(root: DataRoot, visit_id: str) -> str:
    person, visit = parse_visit_id(visit_id)
    set_name = {"P": "pilot"}.get(person[2], "confirmatory")
    doc = json.loads(read_bytes(root.input_path("inputs", f"schedules/B/{set_name}-dyads.json")))
    dyad = next(d for d in doc["dyads"] if d["unit_id"] == person[:5])
    yoked = next(m["slot_id"] for m in dyad["members"] if m["role"] == "yoked")
    return f"{yoked}-{visit}"


def inject_fault(root: DataRoot, visit_id: str, fault: str, *, documented: bool = False) -> None:
    """Inject one fault (a key of ``codes.FAULT_INJECTIONS``) into a synthetic visit; with
    ``documented`` also write the deviation record or row link that explains it."""
    if not root.synthetic:
        raise WatermarkError("faults are injected into SYNTHETIC roots only")
    if fault not in FAULT_INJECTIONS:
        raise ValueError(f"unknown fault {fault!r} (one of {', '.join(FAULT_INJECTIONS)})")
    _, visit = parse_visit_id(visit_id)
    if visit not in FAULT_VISITS[fault]:
        raise ValueError(f"{fault} does not apply to {visit} visits")
    if not root.raw_visit_dir(visit_id).is_dir():
        raise ValueError(f"{visit_id} has no raw folder in this root")
    touched = _FAULTS[fault](root, visit_id, documented)
    for vid in touched:
        refresh_exit_manifest(root, vid)


def _missing_trial(root: DataRoot, visit_id: str, documented: bool) -> list[str]:
    th, trials = _read(root, visit_id, "trial-log.csv")
    lh, plays = _read(root, visit_id, "exposure-ledger.csv")
    sh, sheet = _read(root, visit_id, "visit-run-sheet.csv")
    trained = [i for i, r in enumerate(trials) if r["trial_type"] == "trained"]
    victim = trials.pop(trained[-1])
    plays = [p for p in plays if p["trial_ref"] != victim["trial_id"]]
    for row in sheet:
        if row["block"] == "trained":
            row["actual_count"] = str(int(row["actual_count"]) - 1)
    _write(root, visit_id, "trial-log.csv", th, trials)
    _write(root, visit_id, "exposure-ledger.csv", lh, plays)
    _write(root, visit_id, "visit-run-sheet.csv", sh, sheet)
    if documented:
        _add_deviation(
            root,
            visit_id,
            victim["trial_id"],
            "technical",
            "missing_trial",
            prior_audio="none",
            endpoint="trained",
        )
    return [visit_id]


def _extra_play(root: DataRoot, visit_id: str, documented: bool) -> list[str]:
    _, trials = _read(root, visit_id, "trial-log.csv")
    lh, plays = _read(root, visit_id, "exposure-ledger.csv")
    target = trials[_first(trials, "message_lesson", "atomic_lesson", "trained")]["trial_id"]
    mine = _plays_of(plays, target)
    extra = dict(plays[mine[-1]])
    extra.update(
        event_id=f"E{h(visit_id, 'extra-play')[:20]}",
        presentation_index=str(len(mine) + 1),
        audio_onset_mono_ms=str(int(extra["audio_onset_mono_ms"]) + 1500),
        audio_offset_mono_ms=str(int(extra["audio_offset_mono_ms"]) + 1500),
        feedback_content_id="",
    )
    if documented:
        extra["matching_deviation_id"] = _add_deviation(
            root, visit_id, extra["event_id"], "audio", "extra_play"
        )
    plays.insert(mine[-1] + 1, extra)
    _write(root, visit_id, "exposure-ledger.csv", lh, plays)
    return [visit_id]


def _wrong_hash(root: DataRoot, visit_id: str, documented: bool) -> list[str]:
    th, trials = _read(root, visit_id, "trial-log.csv")
    lh, plays = _read(root, visit_id, "exposure-ledger.csv")
    i = _first(trials, "trained")
    wrong = h(visit_id, "wrong-file")
    for row in (trials[i], *[plays[j] for j in _plays_of(plays, trials[i]["trial_id"])]):
        row["waveform_sha256"] = wrong
        if row["pcm_sha256"]:
            row["pcm_sha256"] = wrong
    _write(root, visit_id, "trial-log.csv", th, trials)
    _write(root, visit_id, "exposure-ledger.csv", lh, plays)
    if documented:
        _add_deviation(
            root,
            visit_id,
            trials[i]["trial_id"],
            "technical",
            "wrong_hash",
            prior_audio="audible",
            endpoint="trained",
        )
    return [visit_id]


def _holdout_in_lesson(root: DataRoot, visit_id: str, documented: bool) -> list[str]:
    _, trials = _read(root, visit_id, "trial-log.csv")
    lh, plays = _read(root, visit_id, "exposure-ledger.csv")
    refs = load_references(root, visit_id)
    target = trials[_first(trials, "message_lesson")]["trial_id"]
    mine = _plays_of(plays, target)
    profile, ranks = committed(refs) if refs.study == "B" else (None, {})
    message = waveform = ""
    due = heldout_visits(refs)
    for c in cells():
        if c.heldout_set is None or due.get(c.message_id) == refs.visit:
            continue  # a held-out message tested at this visit would also turn its novel
            # trial into a repeat; the fault is isolated on another held-out message
        if refs.study == "A":
            waveform = refs.expected_hashes[c.message_id].pcm_sha256
        elif profile is not None and c.action_atom in ranks and c.referent_atom in ranks:
            key = combination_key(
                c.message_id, profile, ranks[c.action_atom], ranks[c.referent_atom]
            )
            waveform = refs.expected_hashes[key].pcm_sha256
        else:
            continue
        message = c.message_id
        break
    row = dict(plays[mine[-1]])
    row.update(
        event_id=f"E{h(visit_id, 'holdout-in-lesson')[:20]}",
        atom_or_message_id=message,
        whole_phrase="true",
        waveform_sha256=waveform,
        pcm_sha256=waveform,
        presentation_index=str(len(mine) + 1),
        meaning_display_id="",
        feedback_content_id="",
        audio_onset_mono_ms=str(int(row["audio_onset_mono_ms"]) + 2500),
        audio_offset_mono_ms=str(int(row["audio_offset_mono_ms"]) + 2500),
    )
    if documented:
        row["matching_deviation_id"] = _add_deviation(
            root,
            visit_id,
            row["event_id"],
            "technical",
            "holdout_in_lesson",
            prior_audio="audible",
            endpoint="novel",
        )
    plays.insert(mine[-1] + 1, row)
    _write(root, visit_id, "exposure-ledger.csv", lh, plays)
    return [visit_id]


def _changed_old_atom(root: DataRoot, visit_id: str, documented: bool) -> list[str]:
    person, visit = parse_visit_id(visit_id)
    rel = input_path("store_snapshot", unit_id=person[:5], visit=visit).removeprefix("inputs/")
    snap = json.loads(read_bytes(root.input_path("inputs", rel)))
    first_wave = sorted(e["atom_id"] for e in snap["entries"] if e["atom_id"][3] in "12")
    entry = next(e for e in snap["entries"] if e["atom_id"] == first_wave[0])
    entry["pcm_sha256"] = h(visit_id, "changed-old-atom")
    snap["manifest_sha256"] = canonical_sha256(snap, "manifest_sha256")
    write_synthetic_input(root, "inputs", rel, json_bytes(snap))
    if documented:
        _add_deviation(root, visit_id, visit_id, "technical", "changed_old_atom")
    return [visit_id]


def _yoked_mismatch(root: DataRoot, visit_id: str, documented: bool) -> list[str]:
    target = _yoked_member(root, visit_id)
    lh, plays = _read(root, target, "exposure-ledger.csv")
    i = next(
        j
        for j, p in enumerate(plays)
        if p["stage"] == "atom_menu" and p["accepted_or_rejected"] == "rejected"
    )
    plays[i]["accepted_or_rejected"] = "accepted"
    if documented:
        plays[i]["matching_deviation_id"] = _add_deviation(
            root, target, plays[i]["event_id"], "matching", "yoked_mismatch"
        )
    _write(root, target, "exposure-ledger.csv", lh, plays)
    return [target]


def _late_visit(root: DataRoot, visit_id: str, documented: bool) -> list[str]:
    person, visit = parse_visit_id(visit_id)
    targets = [visit_id]
    if visit in ("V2", "V3"):  # a dyad's acquisition visit moves for both members
        partner = f"{partner_slot(person)}-{visit}"
        if root.raw_visit_dir(partner).is_dir():
            targets.append(partner)
    for vid in targets:
        sh, sheet = _read(root, vid, "visit-run-sheet.csv")
        for row in sheet:
            for column in ("start_time", "end_time"):
                t = datetime.fromisoformat(row[column]) + timedelta(days=3)
                row[column] = _iso(t)
        _write(root, vid, "visit-run-sheet.csv", sh, sheet)
        if documented:
            _add_deviation(root, vid, vid, "window", "late_visit")
    return targets


def _broken_retry_of(root: DataRoot, visit_id: str, documented: bool) -> list[str]:
    th, trials = _read(root, visit_id, "trial-log.csv")
    lh, plays = _read(root, visit_id, "exposure-ledger.csv")
    i = _first(trials, "trained")
    original = trials[i]
    last = max(j for j, r in enumerate(trials) if r["trial_type"] == "trained")
    retry = dict(original)
    retry.update(trial_id=f"{original['trial_id']}-R1", retry_of=original["trial_id"])
    play_index = _plays_of(plays, original["trial_id"])[-1]
    play = dict(plays[play_index])
    play.update(event_id=f"E{h(visit_id, 'retry-play')[:20]}", trial_ref=retry["trial_id"])
    if documented:
        _add_deviation(root, visit_id, retry["trial_id"], "procedure", "broken_retry_of")
    trials.insert(last + 1, retry)
    last_play = max(_plays_of(plays, trials[last]["trial_id"]) or [len(plays) - 1])
    plays.insert(last_play + 1, play)
    _write(root, visit_id, "trial-log.csv", th, trials)
    _write(root, visit_id, "exposure-ledger.csv", lh, plays)
    return [visit_id]


def _unlinked_discrepancy(root: DataRoot, visit_id: str, documented: bool) -> list[str]:
    sh, sheet = _read(root, visit_id, "visit-run-sheet.csv")
    sheet[0]["actual_count"] = str(int(sheet[0]["actual_count"]) + 1)
    _write(root, visit_id, "visit-run-sheet.csv", sh, sheet)
    return [visit_id]


_FAULTS: Final = {
    "missing_trial": _missing_trial,
    "extra_play": _extra_play,
    "wrong_hash": _wrong_hash,
    "holdout_in_lesson": _holdout_in_lesson,
    "changed_old_atom": _changed_old_atom,
    "yoked_mismatch": _yoked_mismatch,
    "late_visit": _late_visit,
    "broken_retry_of": _broken_retry_of,
    "unlinked_discrepancy": _unlinked_discrepancy,
}


# ---------------------------------------------------------------------------------------
# Fault-injection suite


@dataclass(frozen=True)
class FaultCase:
    """One fault injected into one visit type and the reconciliation outcome."""

    fault: str
    visit_type: str
    visit_id: str
    documented: bool
    expected: str
    detected: bool  # the expected code is reported
    resolved: bool  # every discrepancy with the expected code is resolved
    deviation_missing: bool  # C8 reported DEVIATION_MISSING
    status: str  # summary status of the visit's report
    codes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Detected with the expected code; documented faults resolved and C8 clean."""
        if not self.detected:
            return False
        if self.documented:
            return self.resolved and not self.deviation_missing and self.status == "pass"
        return self.deviation_missing and self.status == "fail"


FAULT_SUITE_HEADER: Final = (
    "fault",
    "visit_type",
    "visit_id",
    "documented",
    "expected_code",
    "detected",
    "resolved",
    "deviation_missing",
    "status",
    "ok",
    "codes",
)


def suite_visits(root: DataRoot) -> dict[str, str]:
    """Visit type -> the visit ID the suite injects into (first revealed person)."""
    out: dict[str, str] = {}
    for vid in raw_visit_ids(root):
        _, visit = parse_visit_id(vid)
        out.setdefault(visit, vid)
    return out


def fault_suite(
    workdir: Path,
    *,
    seed_label: str = DEFAULT_SEED,
    documented_modes: Sequence[bool] = (False, True),
) -> list[FaultCase]:
    """Run every fault on every visit type it applies to; reports go to
    ``workdir/reports/``. ``workdir`` must not exist or be empty."""
    base = workdir / "base"
    build_synthetic_root(base, seed_label=seed_label, max_persons=2)
    visits = suite_visits(DataRoot.open(base))
    (workdir / "reports").mkdir(parents=True, exist_ok=True)
    out: list[FaultCase] = []
    for fault, expected in FAULT_INJECTIONS.items():
        for visit_type in FAULT_VISITS[fault]:
            for documented in documented_modes:
                if documented and fault == "unlinked_discrepancy":
                    continue
                name = f"{fault}-{visit_type}-{'documented' if documented else 'undocumented'}"
                case_dir = workdir / "cases" / name
                shutil.copytree(base, case_dir)
                root = DataRoot.open(case_dir)
                vid = visits[visit_type]
                inject_fault(root, vid, fault, documented=documented)
                report = reconcile_visit(root, vid)
                data = report_bytes(report)
                (workdir / "reports" / f"{name}.json").write_bytes(data)
                shutil.rmtree(case_dir)
                found = [d for c in report.checks for d in c.discrepancies]
                hits = [d for d in found if d.code == expected]
                out.append(
                    FaultCase(
                        fault=fault,
                        visit_type=visit_type,
                        visit_id=vid,
                        documented=documented,
                        expected=expected,
                        detected=bool(hits),
                        resolved=bool(hits) and all(d.resolved for d in hits),
                        deviation_missing=any(d.code == "DEVIATION_MISSING" for d in found),
                        status="pass" if report.passed else "fail",
                        codes=tuple(sorted({d.code for d in found})),
                    )
                )
    shutil.rmtree(workdir / "cases", ignore_errors=True)
    return out


def fault_suite_csv(cases: Sequence[FaultCase]) -> bytes:
    """``fault-suite.csv`` bytes."""
    rows = [
        [
            c.fault,
            c.visit_type,
            c.visit_id,
            str(c.documented).lower(),
            c.expected,
            str(c.detected).lower(),
            str(c.resolved).lower(),
            str(c.deviation_missing).lower(),
            c.status,
            str(c.ok).lower(),
            "|".join(c.codes),
        ]
        for c in cases
    ]
    return csv_bytes(FAULT_SUITE_HEADER, rows)


# ---------------------------------------------------------------------------------------
# Committed examples (analysis/examples/reconciliation-demo/)

# Fault examples next to the clean reports: (fault, visit type, documented).
EXAMPLE_FAULTS: Final = (("wrong_hash", "D0", False), ("missing_trial", "W1", True))


def example_reports(workdir: Path, *, seed_label: str = DEFAULT_SEED) -> dict[str, bytes]:
    """Sample reconciliation reports (file name -> bytes): one clean report per visit
    type, plus the fault examples of :data:`EXAMPLE_FAULTS`. ``workdir`` is scratch space."""
    base = workdir / "root"
    root = build_synthetic_root(base, seed_label=seed_label, max_persons=1)
    visits = suite_visits(root)
    out: dict[str, bytes] = {}
    for visit_type in ALL_VISIT_TYPES:
        vid = visits[visit_type]
        out[f"{vid[0]}-{visit_type}-clean.json"] = report_bytes(reconcile_visit(root, vid))
    for fault, visit_type, documented in EXAMPLE_FAULTS:
        case = workdir / f"{fault}-{visit_type}"
        shutil.copytree(base, case)
        case_root = DataRoot.open(case)
        vid = visits[visit_type]
        inject_fault(case_root, vid, fault, documented=documented)
        mode = "documented" if documented else "undocumented"
        name = f"{vid[0]}-{visit_type}-{fault}-{mode}.json"
        out[name] = report_bytes(reconcile_visit(case_root, vid))
    return out


def write_examples(directory: Path, *, seed_label: str = DEFAULT_SEED) -> list[str]:
    """Write the committed example set (sample reports and ``fault-suite.csv``) into
    ``directory`` (not a data root); returns the file names written."""
    import tempfile

    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="av-examples-") as tmp:
        files = example_reports(Path(tmp) / "reports", seed_label=seed_label)
        cases = fault_suite(Path(tmp) / "suite", seed_label=seed_label)
    files["fault-suite.csv"] = fault_suite_csv(cases)
    for name, data in sorted(files.items()):
        (directory / name).write_bytes(data)
    return sorted(files)


# ---------------------------------------------------------------------------------------
# Command line


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis synth-logs``."""
    parser.add_argument("--demo-seed", required=True, help="public DEMO- seed label")
    parser.add_argument("--out", required=True, help="new SYNTHETIC data root")
    parser.add_argument("--study", choices=("A", "B", "both"), default="both")
    parser.add_argument("--set", choices=("pilot", "confirmatory"), default="pilot")
    parser.add_argument("--fault", help="inject one fault (codes.FAULT_INJECTIONS)")
    parser.add_argument("--visit", help="visit ID for --fault")
    parser.add_argument(
        "--documented",
        action="store_true",
        help="with --fault: also write the deviation record that explains it",
    )
    parser.add_argument("--units", type=int, default=1, help="units revealed per study (default 1)")
    parser.add_argument(
        "--max-persons", type=int, default=None, help="cap on revealed Study A learner slots"
    )
    parser.add_argument(
        "--fault-suite",
        action="store_true",
        help="run every fault on every visit type into --out and write "
        "fault-suite.csv (exit 1 if a fault is not detected)",
    )
    parser.add_argument(
        "--examples",
        action="store_true",
        help="write the committed example reports and fault-suite.csv into --out "
        "(a plain folder; analysis/examples/reconciliation-demo/)",
    )


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis synth-logs``."""
    out = Path(args.out)
    try:
        seeds_ok = args.demo_seed.startswith("DEMO-")
        if not seeds_ok:
            raise ValueError("--demo-seed must start with DEMO-")
        if args.examples:
            for name in write_examples(out, seed_label=args.demo_seed):
                print(f"wrote {name}")
            return 0
        if args.fault_suite:
            if out.exists() and any(out.iterdir()):
                raise WatermarkError(f"{out} is not empty")
            cases = fault_suite(out, seed_label=args.demo_seed)
            out.mkdir(parents=True, exist_ok=True)
            (out / "fault-suite.csv").write_bytes(fault_suite_csv(cases))
            bad = [c for c in cases if not c.ok]
            print(f"fault suite: {len(cases) - len(bad)}/{len(cases)} cases as expected")
            for c in bad:
                print(
                    f"  NOT OK: {c.fault} {c.visit_id} documented={c.documented}", file=sys.stderr
                )
            return 1 if bad else 0
        if args.fault and not args.visit:
            raise ValueError("--fault needs --visit")
        if args.fault and (out / "av-data-root.json").is_file():
            root = DataRoot.open(out)
        else:
            root = build_synthetic_root(
                out,
                seed_label=args.demo_seed,
                study=args.study,
                set_name=args.set,
                units=args.units,
                max_persons=args.max_persons,
            )
            print(f"SYNTHETIC data root {root.path} ({root.label})")
        if args.fault:
            inject_fault(root, args.visit, args.fault, documented=args.documented)
            print(
                f"injected {args.fault} into {args.visit}"
                f"{' (documented)' if args.documented else ''}"
            )
    except (ValueError, WatermarkError, OSError, KeyError) as exc:
        print(f"synth-logs: refusing: {exc}", file=sys.stderr)
        return 2
    return 0
