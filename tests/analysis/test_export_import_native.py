"""Native mapping trust boundaries with explicitly synthetic journal/manifest fixtures.

These fixtures exercise the offline adapter; actual Unity native-export validation is
separate. No private schedule, allocation, operator record or template is committed.
"""

from __future__ import annotations

import copy
import json
import shutil

import pytest

from av_analysis import export_import as ei
from av_analysis import native_protocol as np
from av_analysis.cli import main
from av_analysis.fileio import csv_bytes, json_bytes, parse_csv, sha256_bytes
from av_analysis.loaders import value_problems
from av_analysis.paths import DataRoot
from av_analysis.references import input_path, load_references, revealed_persons, schedule_items
from av_analysis.templates import DEVIATIONS_COLUMNS, TEMPLATES


@pytest.fixture(scope="module")
def native_template(tmp_path_factory):
    path = tmp_path_factory.mktemp("native-synthetic") / "root"
    assert (
        main(
            [
                "synth-logs",
                "--demo-seed",
                "DEMO-native-import",
                "--out",
                str(path),
                "--study",
                "A",
                "--set",
                "pilot",
                "--units",
                "1",
                "--max-persons",
                "1",
            ]
        )
        == 0
    )
    root = DataRoot.open(path)
    ((person, coded),) = revealed_persons(root).items()
    sheet = (root.raw_visit_dir(f"{person}-D7") / "visit-run-sheet.csv").read_bytes()
    shutil.rmtree(root.raw_visit_dir(f"{person}-D7"))
    return path, person, coded, sheet


class NativeCase:
    def __init__(self, tmp_path, template):
        source, self.person, self.coded, self.sheet = template
        shutil.copytree(source, tmp_path / "root")
        self.root = DataRoot.open(tmp_path / "root")
        self.visit = f"{self.person}-D7"
        self.refs = refs = load_references(self.root, self.visit)
        self.block, self.item = schedule_items(refs.schedules["D7"])[0]
        self.attempt = self.item["trial_id"]
        self.request = "b" * 32
        self.identity = {
            "session_id": "a" * 32,
            "coded_id": self.coded,
            "visit_id": "D7",
            "station_id": "SYNTHETIC-native",
            "protocol_version": "native-test-v1",
            "build_sha256": "c" * 64,
        }
        path = input_path(
            "schedule",
            study=refs.study,
            unit_id=refs.unit_id,
            person_id=refs.person_id,
            visit=refs.visit,
        )
        self.schedule_sha = refs.inputs[path]
        self.epoch = "d" * 32
        self.rows = []
        self.add(
            "session",
            {
                "event": "state_after",
                "state": "Loaded",
                "clock_epoch": "e" * 32,
                "schedule_sha256": self.schedule_sha,
                "scheduled_onset_mono_ms": 1000,
                "audio_request_ids": [self.request],
            },
        )
        self.add(
            "slot_context",
            {
                "schema_version": "native-slot-context-1",
                "schedule_sha256": self.schedule_sha,
                "package_sha256": refs.package_sha256,
                "session_clock_epoch": "e" * 32,
                "observation_clock_epoch": self.epoch,
                "clock_basis": "shared_host_monotonic",
                "loaded_event_sha256": None,
                "person_slot": self.person,
                "trial_type": self.item["trial_type"],
                "content_id": self.item["message_id"]
                or self.item["atom_id"]
                or self.item["speech_id"],
                "role": None,
                "presentation": self.item["presentation"],
                "phase": self.block["phase"],
                "heldout": self.item["trained_status"] == "heldout",
                "slot_seconds": self.item["slot_s"],
                "plays": self.item["plays"],
                "pass": self.item["pass"],
                "scheduled_onset_mono_ms": 1000,
                "audio_request_ids": [self.request],
                "dictionary_policy": "prohibited",
                "feedback_policy": "prohibited",
            },
        )
        waveform = next(iter(refs.expected_hashes.values()))
        self.add(
            "audio_request",
            {
                "pcm_sha256": waveform.pcm_sha256,
                "waveform_sha256": waveform.file_sha256,
                "request_mono_ms": 500,
                "scheduled_mono_ms": 1000,
            },
            self.request,
        )
        self.add(
            "audio_observation",
            {
                "code": "AUDIO_ONSET_ESTIMATED",
                "onset_estimate_mono_ms": 1010.25,
                "onset_uncertainty_ms": 10,
            },
            self.request,
        )
        self.add(
            "audio_observation",
            {"code": "AUDIO_PLAYBACK_COMPLETED", "observed_mono_ms": 2010.25},
            self.request,
        )
        self.add("panel_process", {"kind": "panel_open", "observed_mono_ms": 1010.25})
        target = self.item["intended"]
        self.add(
            "panel_response",
            {
                "kind": "response",
                "observed_mono_ms": 2210.75,
                "response_code": "commit",
                "response_action": target["semantic_action"],
                "response_target": target["semantic_referent"],
            },
        )
        self.add("session", {"event": "visit_complete"})
        self.trial = dict.fromkeys(ei.PROVISIONAL_TRIAL_COLUMNS, "")
        self.trial.update(
            self.base(),
            opportunity_id=self.attempt,
            attempt_id=self.attempt,
            method_masked="masked",
            waveform_sha256=waveform.file_sha256,
            audio_request_mono_ms="500",
            audio_onset_estimate_mono_ms="1010.25",
            onset_uncertainty_ms="10",
            playback_status="observed_complete",
            reset_ok="true",
            focus_ok="true",
            response_code="commit",
            exposure_consumed="true",
            interrupted="false",
            audio_request_ids=json.dumps([self.request]),
            response_action=target["semantic_action"],
            response_target=target["semantic_referent"],
        )
        self.play = dict.fromkeys(ei.PROVISIONAL_EXPOSURE_COLUMNS, "")
        self.play.update(
            self.base(),
            opportunity_id=self.attempt,
            attempt_id=self.attempt,
            audio_request_id=self.request,
            audio_id="native-test-cue",
            waveform_sha256=waveform.file_sha256,
            audio_request_mono_ms="500",
            scheduled_onset_mono_ms="1000",
            audio_onset_estimate_mono_ms="1010.25",
            onset_uncertainty_ms="10",
            playback_status="callback_complete",
            audible_status="estimated",
            callback_observed="true",
            pause_ms="0",
            exposure_consumed="true",
            accepted_or_rejected="pending",
        )
        self.deviations = csv_bytes(DEVIATIONS_COLUMNS, [])
        self.directory = tmp_path / "export"

    def base(self):
        return {
            "schema_version": ei.CSV_FORMAT,
            **{k: self.identity[k] for k in ("session_id", "coded_id", "visit_id", "station_id")},
        }

    def add(self, kind, payload, audio=None):
        self.rows.append(
            {
                "schema_version": ei.EVENTS_FORMAT,
                "sequence": len(self.rows),
                "event_id": f"{len(self.rows):032x}",
                "clock_epoch": self.epoch,
                "host_mono_ms": len(self.rows) * 100,
                "identity": self.identity,
                "event_type": kind,
                "opportunity_id": self.attempt,
                "attempt_id": self.attempt,
                "audio_request_id": audio,
                "payload": payload,
            }
        )

    def bundle(self, projection_change=None):
        previous = "0" * 64
        lines, chained = [], []
        for row in self.rows:
            current = copy.deepcopy(row)
            current["sequence"] = len(chained)
            current["event_id"] = f"{len(chained):032x}"
            if current["event_type"] == "slot_context":
                loaded = next(
                    r
                    for r in reversed(chained)
                    if r["event_type"] == "session" and r["payload"].get("state") == "Loaded"
                )
                current["payload"]["loaded_event_sha256"] = loaded["sha256"]
            current["previous_sha256"] = previous
            data = json.dumps(current, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
            previous = sha256_bytes(data)
            current["sha256"] = previous
            chained.append(current)
            lines.append(
                json.dumps(current, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
            )
        projection = {
            "format": "av-native-observations",
            "format_version": 1,
            "evidence_level": "software_event_calls",
            "acoustic_authority": False,
            "identity": self.identity,
            "events": [np._project(r) for r in chained],
        }
        if projection_change:
            projection_change(projection)
        files = {
            "raw/events-0000.local.jsonl": b"".join(lines),
            "trial-log.csv": csv_bytes(
                ei.PROVISIONAL_TRIAL_COLUMNS,
                [[self.trial[c] for c in ei.PROVISIONAL_TRIAL_COLUMNS]],
            ),
            "exposure-ledger.csv": csv_bytes(
                ei.PROVISIONAL_EXPOSURE_COLUMNS,
                [[self.play[c] for c in ei.PROVISIONAL_EXPOSURE_COLUMNS]],
            ),
            "header-contract.json": json_bytes(
                {
                    "schema_version": ei.HEADER_CONTRACT_FORMAT,
                    "qualified": False,
                    "review_evidence_sha256": None,
                    "trial_template_sha256": None,
                    "exposure_template_sha256": None,
                    "trial_headers": list(ei.PROVISIONAL_TRIAL_COLUMNS),
                    "exposure_headers": list(ei.PROVISIONAL_EXPOSURE_COLUMNS),
                }
            ),
            ei.NATIVE_OBSERVATIONS: json_bytes(projection),
        }
        self.directory.mkdir(exist_ok=True)
        for name, data in files.items():
            target = self.directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        manifest = {
            "schema_version": ei.NATIVE_EXPORT_FORMAT,
            "export_id": "f" * 32,
            "identity": self.identity,
            "headers_qualified": False,
            "unacknowledged_torn_tail": False,
            "record_count": len(chained),
            "last_record_sha256": previous,
            "trial_rows": 1,
            "exposure_rows": 1,
            "files": [
                {"path": n, "bytes": len(d), "sha256": sha256_bytes(d)} for n, d in files.items()
            ],
        }
        data = json_bytes(manifest)
        (self.directory / "manifest.json").write_bytes(data)
        return ei.read_bundle(self.directory, sha256_bytes(data))

    def plan(self, **change):
        return ei.plan_import(
            self.root,
            self.visit,
            self.bundle(),
            self.sheet,
            change.get("deviations", self.deviations),
        )


@pytest.fixture
def native_case(tmp_path, native_template):
    return NativeCase(tmp_path, native_template)


def rows(data):
    header, values = parse_csv(data)
    return [dict(zip(header, r, strict=True)) for r in values]


def test_native_mapping_joins_private_schedule_preserves_observations_and_uncertainty(native_case):
    case = native_case
    plan = case.plan()
    (trial,) = rows(plan.files["trial-log.csv"])
    (play,) = rows(plan.files["exposure-ledger.csv"])
    assert trial["codebook_id"] == case.refs.package_id
    assert trial["trial_type"] == case.item["trial_type"]
    assert trial["target_action"] == case.item["intended"]["semantic_action"]
    assert trial["exact_correct"] == "true"
    assert trial["feedback_shown"] == trial["dictionary_available"] == "false"
    assert trial["commit_mono_ms"] == "2211" and trial["response_time_ms"] == "1201"
    assert trial["audio_offset_mono_ms"] == play["audio_offset_mono_ms"] == ""
    assert trial["frame_freeze_ms"] == ""  # Unknown never becomes zero.
    assert play["accepted_or_rejected"] == ""  # Non-menu inapplicability, raw pending retained.
    assert play["retrieval_opportunity"] == "true" and play["audible_status"] == "estimated"
    assert not value_problems(TEMPLATES["trial-log"], trial)
    assert not value_problems(TEMPLATES["exposure-ledger"], play)
    report = json.loads(plan.files["mapping/native-protocol.json"])
    assert report["producer_headers_qualified"] is False
    assert report["prior_history_inputs"]
    assert report["attempts"][0]["software_callback_ends"][0]["acoustic_authority"] is False
    assert plan.exit_manifest["source"]["format"] == ei.NATIVE_EXPORT_FORMAT
    assert plan.exit_manifest["source"]["headers_qualified"] is False


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["events"].pop(),
        lambda p: p["events"][1]["payload"].update(dictionary_policy="prohibited-by-plan-only"),
        lambda p: p["events"][2].update(source_event_sha256="0" * 64),
    ],
)
def test_native_projection_must_match_all_actual_source_events(native_case, change):
    with pytest.raises(ei.ExportRefused, match="EXPORT_NATIVE_SOURCE"):
        native_case.bundle(change)
    assert not native_case.root.raw_visit_dir(native_case.visit).exists()


def test_native_events_cannot_be_rehashed_and_downgraded_to_legacy(native_case):
    native_case.bundle()
    path = native_case.directory / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["schema_version"] = ei.EXPORT_FORMAT
    manifest["files"] = [e for e in manifest["files"] if e["path"] != ei.NATIVE_OBSERVATIONS]
    (native_case.directory / ei.NATIVE_OBSERVATIONS).unlink()
    data = json_bytes(manifest)
    path.write_bytes(data)
    with pytest.raises(ei.ExportRefused, match="EXPORT_NATIVE_VERSION"):
        ei.read_bundle(native_case.directory, sha256_bytes(data))


@pytest.mark.parametrize(
    "column,value",
    [
        ("schedule_sha256", "0" * 64),
        ("package_sha256", "0" * 64),
        ("person_slot", "A-P99-L01"),
        ("content_id", "K-a4-r4"),
        ("trial_type", "novel"),
        ("observation_clock_epoch", "0" * 32),
    ],
)
def test_native_executed_context_must_match_pinned_private_inputs(native_case, column, value):
    native_case.rows[1]["payload"][column] = value
    with pytest.raises(ei.ExportRefused, match="EXPORT_NATIVE_(PIN|SCHEDULE)"):
        native_case.plan()


def test_native_unreported_policy_is_not_fabricated_false(native_case):
    native_case.rows[1]["payload"]["dictionary_policy"] = "unreported"
    with pytest.raises(ei.ExportRefused, match="EXPORT_NATIVE_POLICY"):
        native_case.plan()


def test_native_operator_records_require_full_pinned_template_columns(native_case):
    with pytest.raises(ei.ExportRefused, match="EXPORT_COLUMNS"):
        native_case.plan(deviations=csv_bytes(ei.CONSOLE_DEVIATION_COLUMNS, []))


def test_native_history_integrity_is_required_for_prior_counts(native_case):
    path = native_case.root.raw_visit_dir(f"{native_case.person}-D0") / "exposure-ledger.csv"
    path.write_bytes(path.read_bytes().replace(b"confirmed_audible", b"estimated"))
    with pytest.raises(ei.ExportRefused, match="EXPORT_NATIVE_HISTORY"):
        native_case.plan()


def test_native_uncertain_onset_is_preserved_without_precise_reaction_time(native_case):
    native_case.trial.update(
        audio_onset_estimate_mono_ms="", onset_uncertainty_ms="", playback_status="uncertain"
    )
    native_case.play.update(
        audio_onset_estimate_mono_ms="",
        onset_uncertainty_ms="",
        audible_status="uncertain",
        playback_status="uncertain",
    )
    plan = native_case.plan()
    (trial,) = rows(plan.files["trial-log.csv"])
    (play,) = rows(plan.files["exposure-ledger.csv"])
    assert trial["response_time_ms"] == "" and trial["commit_mono_ms"] == "2211"
    assert trial["playback_status"] == play["audible_status"] == "uncertain"
    assert trial["exposure_consumed"] == "true"


def test_native_same_clock_guard_refuses_cross_epoch_response(native_case):
    native_case.rows[6]["clock_epoch"] = "0" * 32
    with pytest.raises(ei.ExportRefused, match="EXPORT_NATIVE_TIME"):
        native_case.plan()


def test_native_pre_cue_replacement_preserves_both_real_binds(native_case):
    case = native_case
    abandoned_loaded = copy.deepcopy(case.rows[0])
    abandoned_loaded["payload"]["audio_request_ids"] = ["9" * 32]
    abandoned_context = copy.deepcopy(case.rows[1])
    abandoned_context["payload"]["audio_request_ids"] = ["9" * 32]
    case.rows[:0] = [abandoned_loaded, abandoned_context]
    plan = case.plan()
    report = json.loads(plan.files["mapping/native-protocol.json"])
    assert len(report["attempts"][0]["all_slot_sources"]) == 2
    assert report["attempts"][0]["slot_source"] == report["attempts"][0]["all_slot_sources"][-1]
    assert len(rows(plan.files["exposure-ledger.csv"])) == 1


def lesson_event(kind, sequence, **payload):
    return {
        "kind": "lesson",
        "sequence": sequence,
        "source_event_sha256": f"{sequence:064x}",
        "clock_epoch": "d" * 32,
        "payload": {
            "kind": kind,
            "observed_mono_ms": sequence * 100,
            "session_clock_epoch": "e" * 32,
            "meaning_display_id": "meaning",
            "feedback_content_id": None,
            **payload,
        },
    }


def test_lesson_mapping_uses_observed_spans_and_one_actual_retrieval():
    events = [
        lesson_event("display_start", 1),
        lesson_event("play_request", 2, audio_request_id="play-1", presentation_index=1),
        lesson_event("display_end", 3),
        lesson_event("retrieval_opportunity", 4),
        lesson_event("play_request", 5, audio_request_id="play-2", presentation_index=2),
        lesson_event("retrieval_result", 6),
        lesson_event("display_start", 7, feedback_content_id="corrective-content"),
        lesson_event(
            "play_request",
            8,
            audio_request_id="play-3",
            presentation_index=3,
        ),
        lesson_event("display_end", 9, feedback_content_id="corrective-content"),
    ]
    guided, _ = np._lesson_play(events, "play-1")
    retrieval, _ = np._lesson_play(events, "play-2")
    feedback, _ = np._lesson_play(events, "play-3")
    assert guided["display_start_mono_ms"] == "100" and guided["display_end_mono_ms"] == "300"
    assert guided["retrieval_opportunity"] == feedback["retrieval_opportunity"] == "false"
    assert retrieval["retrieval_opportunity"] == "true" and retrieval["meaning_display_id"] == ""
    assert not retrieval.get("display_start_mono_ms") and not retrieval.get("display_end_mono_ms")
    assert feedback["feedback_content_id"] == "corrective-content"
    assert feedback["display_start_mono_ms"] == "700" and feedback["display_end_mono_ms"] == "900"
    _, sources = np._lesson_play(events, "play-3")
    assert sources[0]["clock_epoch"] == "d" * 32
    assert sources[0]["session_clock_epoch"] == "e" * 32
    crossed = copy.deepcopy(events)
    crossed[-1]["clock_epoch"] = "f" * 32
    with pytest.raises(ei.ExportRefused, match="EXPORT_NATIVE_TIME"):
        np._lesson_play(crossed, "play-3")
    with pytest.raises(ei.ExportRefused, match="EXPORT_NATIVE_LESSON"):
        np._lesson_play(
            [e for e in events if e["payload"]["kind"] != "retrieval_opportunity"], "play-2"
        )


@pytest.mark.parametrize("defaulted,expected", [(False, "active_choice"), (True, "default")])
def test_menu_mapping_uses_final_observed_decision_and_display_boundaries(defaulted, expected):
    def event(kind, sequence, **payload):
        return {
            "kind": "menu_observation",
            "sequence": sequence,
            "audio_request_id": payload.pop("audio", None),
            "source_event_sha256": f"{sequence:064x}",
            "payload": {
                "kind": kind,
                "observed_mono_ms": sequence * 100,
                "meaning_display_id": "atom-meaning",
                "yoked_source_event_id": None,
                **payload,
            },
        }

    events = [
        event("display_changed", 1, phase="Instructions", observed_mono_ms=0),
        event(
            "play_request",
            2,
            audio="request-1",
            presentation_index=1,
            candidate_id="P1",
            observed_mono_ms=5750,
            expected_mono_ms=6500,
        ),
        event("display_changed", 3, phase="Audition", observed_mono_ms=6000),
        event(
            "onset_authority", 4, audio="request-1", observed_mono_ms=6500, expected_mono_ms=6500
        ),
        event(
            "play_request",
            5,
            audio="request-3",
            presentation_index=3,
            candidate_id="P2",
            observed_mono_ms=13750,
            expected_mono_ms=14500,
        ),
        event(
            "onset_authority", 6, audio="request-3", observed_mono_ms=14500, expected_mono_ms=14500
        ),
        event("display_changed", 7, phase="Choice", observed_mono_ms=30000),
        event("choice_revised", 8, selected_index=1, observed_mono_ms=35000),
        event("choice_final", 9, selected_index=2, defaulted=defaulted, observed_mono_ms=45000),
        event("display_changed", 10, phase="Selected", observed_mono_ms=45000),
        event(
            "selection_verified",
            11,
            selected_index=2,
            defaulted=defaulted,
            receipt_sha256="c" * 64,
            observed_mono_ms=45000,
        ),
    ]
    rejected, sources = np._menu_play(events, "request-1")
    accepted, _ = np._menu_play(events, "request-3")
    assert rejected["accepted_or_rejected"] == "rejected"
    assert accepted["accepted_or_rejected"] == "accepted"
    assert accepted["active_choice_or_default"] == expected
    assert accepted["display_start_mono_ms"] == "6000"
    assert accepted["display_end_mono_ms"] == "30000"
    assert sources[0]["source"] == f"{9:064x}"
    assert rejected["display_start_mono_ms"] == "6000"  # Request was before this transition.
    assert sources[0]["onset_authority_source"] == f"{4:064x}"
    unknown, _ = np._menu_play(
        [e for e in events if e["payload"]["kind"] != "onset_authority"], "request-1"
    )
    assert unknown["display_start_mono_ms"] == unknown["display_end_mono_ms"] == ""
    with pytest.raises(ei.ExportRefused, match="EXPORT_NATIVE_MENU"):
        np._menu_play(events[:4], "request-1")
