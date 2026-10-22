"""Offline native-export adapter; observed facts and private answers have separate sources.

``data-export-native-1`` preserves the provisional audio tables and adds a projection
of actual journal events. This adapter verifies that projection against the journal,
joins *opportunity IDs* to pinned private schedules, and writes the protocol columns.
It never upgrades uncertain playback, invents operator decisions, or treats software
completion as an acoustic offset. Header qualification remains the producer's value.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Final, cast

from av_schedules.orders import study_visits

from .export_import import (
    NATIVE_OBSERVATIONS,
    Binding,
    Bundle,
    ExportRefused,
    _check_links,
    _exact,
    _require,
    _table,
    strict_json,
)
from .fileio import csv_bytes, json_bytes, sha256_bytes
from .ledger import components, is_message, visit_logs
from .loaders import RawVisit, load_raw_visit, value_problems
from .paths import EXIT_MANIFEST, DataRoot
from .references import (
    References,
    cue_of,
    input_path,
    load_references,
    load_reveal,
    schedule_items,
)
from .schemas import validator
from .templates import (
    DEVIATIONS_COLUMNS,
    EXPOSURE_LEDGER_COLUMNS,
    EXTENSION_COLUMNS,
    TEMPLATES,
    TRIAL_LOG_COLUMNS,
)
from .vocab import CONSUMING_AUDIBLE_STATUS

Event = dict[str, Any]
PROJECTED_KINDS: Final = frozenset(
    {
        "slot_context",
        "panel_response",
        "panel_process",
        "audio_request",
        "audio_observation",
        "audio_evidence",
        "lesson",
        "assessment_stage",
        "menu_observation",
        "device",
        "choice",
        "session",
    }
)
OBSERVATION_FIELDS: Final = (
    "sequence",
    "source_event_sha256",
    "clock_epoch",
    "host_mono_ms",
    "opportunity_id",
    "attempt_id",
    "audio_request_id",
    "kind",
    "payload",
)
SLOT_FIELDS: Final = (
    "schema_version",
    "schedule_sha256",
    "package_sha256",
    "session_clock_epoch",
    "observation_clock_epoch",
    "clock_basis",
    "loaded_event_sha256",
    "person_slot",
    "trial_type",
    "content_id",
    "role",
    "presentation",
    "phase",
    "heldout",
    "slot_seconds",
    "plays",
    "pass",
    "scheduled_onset_mono_ms",
    "audio_request_ids",
    "dictionary_policy",
    "feedback_policy",
)
MENU_FIELDS: Final = (
    "schema_version",
    "schedule_sha256",
    "package_sha256",
    "role",
    "event_id",
    "kind",
    "menu_key",
    "meaning_display_id",
    "slot_start_mono_ms",
    "observed_mono_ms",
    "expected_mono_ms",
    "onset_uncertainty_ms",
    "presentation_index",
    "candidate_id",
    "pcm_sha256",
    "file_sha256",
    "yoked_source_event_id",
    "selected_index",
    "defaulted",
    "phase",
    "receipt_sha256",
    "matching_deviation_id",
)
LESSON_COLUMNS: Final = tuple(
    [
        "schema_version",
        "session_id",
        "coded_id",
        "visit_id",
        "station_id",
        "schedule_sha256",
        "package_sha256",
        "clock_epoch",
        "session_clock_epoch",
        "opportunity_id",
        "attempt_id",
        "context_audio_request_ids",
        "lesson_type",
        "row_kind",
        "presentation_index",
        "audio_request_id",
        "meaning_display_id",
        "feedback_content_id",
        "pcm_sha256",
        "action_pcm_sha256",
        "referent_pcm_sha256",
        "start_event_sha256",
        "end_event_sha256",
        "observed_start_mono_ms",
        "observed_end_mono_ms",
        "expected_start_mono_ms",
        "display_start_mono_ms",
        "display_end_mono_ms",
        "retrieval_opportunity",
        "interval_status",
        "lesson_status",
        "audio_ledger_status",
        "audible_status",
        "exposure_consumed",
        "audio_onset_estimate_mono_ms",
        "onset_uncertainty_ms",
        "evidence_level",
    ]
)
ASSESSMENT_COLUMNS: Final = tuple(
    [
        "schema_version",
        "session_id",
        "coded_id",
        "visit_id",
        "station_id",
        "sequence",
        "schedule_sha256",
        "clock_epoch",
        "host_mono_ms",
        "event_kind",
        "stage",
        "item_id",
        "value",
        "outcome_code",
    ]
)


def _payload(event: Mapping[str, Any]) -> dict[str, Any]:
    value = event["payload"]
    _require(isinstance(value, dict), "EXPORT_NATIVE_FIELDS", "event payload must be an object")
    return cast(dict[str, Any], value)


def _text(value: object) -> str:
    _require(value is None or isinstance(value, str), "EXPORT_NATIVE_FIELDS", "expected text")
    return value if isinstance(value, str) else ""


def _number(value: object) -> Decimal:
    _require(
        type(value) in (int, float, str) and not isinstance(value, bool),
        "EXPORT_NATIVE_TIME",
        "expected finite monotonic milliseconds",
    )
    try:
        result = Decimal(str(value))
    except ArithmeticError:
        raise ExportRefused("EXPORT_NATIVE_TIME", "invalid milliseconds") from None
    _require(result.is_finite() and result >= 0, "EXPORT_NATIVE_TIME", "invalid milliseconds")
    return result


def _ms(value: object) -> str:
    """Protocol millisecond resolution; exact original values remain in the journal."""
    if value is None or value == "":
        return ""
    return str(_number(value).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def _project(row: Event) -> Event:
    return {
        "sequence": row["sequence"],
        "source_event_sha256": row["sha256"],
        "clock_epoch": row["clock_epoch"],
        "host_mono_ms": row["host_mono_ms"],
        "opportunity_id": row["opportunity_id"],
        "attempt_id": row["attempt_id"],
        "audio_request_id": row["audio_request_id"],
        "kind": row["event_type"],
        "payload": row["payload"],
    }


def native_events(bundle: Bundle) -> tuple[Event, ...]:
    """Verify a complete, exact projection of the selected verified journal records."""
    doc = _exact(
        strict_json(bundle.files[NATIVE_OBSERVATIONS], NATIVE_OBSERVATIONS),
        ("format", "format_version", "evidence_level", "acoustic_authority", "identity", "events"),
        NATIVE_OBSERVATIONS,
    )
    _require(
        doc["format"] == "av-native-observations"
        and type(doc["format_version"]) is int
        and doc["format_version"] == 1
        and doc["evidence_level"] == "software_event_calls"
        and doc["acoustic_authority"] is False
        and doc["identity"] == dict(bundle.identity)
        and isinstance(doc["events"], list),
        "EXPORT_NATIVE_CONTRACT",
        "native observation version, identity or evidence level differs",
    )
    expected: list[Event] = []
    for name, data in sorted(bundle.files.items()):
        if not name.startswith("raw/events-"):
            continue
        for line in data.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                continue  # The journal verifier separately checks any acknowledged torn tail.
            row = cast(Event, strict_json(line, name))
            if row["event_type"] in PROJECTED_KINDS:
                expected.append(_project(row))
    events = tuple(_exact(e, OBSERVATION_FIELDS, "native event") for e in doc["events"])
    _require(
        list(events) == expected,
        "EXPORT_NATIVE_SOURCE",
        "native events differ from the complete verified journal projection",
    )
    _require(
        any(e["kind"] == "slot_context" for e in events),
        "EXPORT_NATIVE_CONTEXT",
        "native export has no executed slot contexts",
    )
    for event in events:
        if event["kind"] == "slot_context":
            p = _exact(event["payload"], SLOT_FIELDS, "native slot context")
            _require(
                p["schema_version"] == "native-slot-context-1",
                "EXPORT_NATIVE_VERSION",
                "unknown slot-context version",
            )
        elif event["kind"] == "menu_observation":
            p = _exact(event["payload"], MENU_FIELDS, "native menu observation")
            _require(
                p["schema_version"] == "native-menu-event-1",
                "EXPORT_NATIVE_VERSION",
                "unknown menu-observation version",
            )
    _check_auxiliary(bundle, events)
    return events


def _check_auxiliary(bundle: Bundle, events: Sequence[Event]) -> None:
    """Auxiliaries are retained evidence; they cannot introduce a second audio play."""
    by_hash = {e["source_event_sha256"]: e for e in events}
    if "lesson-exposures.csv" in bundle.files:
        contract = _exact(
            strict_json(bundle.files["lesson-header-contract.json"], "lesson contract"),
            (
                "schema_version",
                "qualified",
                "headers",
                "exposure_ledger_relation",
                "evidence_level",
                "acoustic_authority",
            ),
            "lesson contract",
        )
        _require(
            contract["schema_version"] == "lesson-exposures-provisional-1"
            and contract["qualified"] is False
            and contract["acoustic_authority"] is False
            and contract["exposure_ledger_relation"]
            == "play_rows_join_by_audio_request_id_no_additional_exposures"
            and contract["headers"] == list(LESSON_COLUMNS),
            "EXPORT_NATIVE_AUXILIARY",
            "unknown lesson auxiliary contract",
        )
        rows = _table(bundle.files["lesson-exposures.csv"], contract["headers"], "lesson exposures")
        starts: set[str] = set()
        for row in rows:
            start = by_hash.get(row.get("start_event_sha256", ""))
            end = by_hash.get(row.get("end_event_sha256", ""))
            _require(
                start is not None and start["kind"] == "lesson",
                "EXPORT_NATIVE_AUXILIARY",
                "lesson row has no journal start source",
            )
            assert start is not None
            p = _payload(start)
            _require(
                row["schema_version"] == "lesson-exposures-provisional-1"
                and all(
                    row[k] == bundle.identity[k]
                    for k in ("session_id", "coded_id", "visit_id", "station_id")
                )
                and start["source_event_sha256"] not in starts
                and row["attempt_id"] == start["attempt_id"]
                and row["opportunity_id"] == start["opportunity_id"]
                and row["observed_start_mono_ms"] == _plain_number(p.get("observed_mono_ms"))
                and all(
                    row[k] == _text(p.get(k))
                    for k in ("schedule_sha256", "package_sha256", "session_clock_epoch")
                ),
                "EXPORT_NATIVE_AUXILIARY",
                "lesson auxiliary source/context differs",
            )
            starts.add(start["source_event_sha256"])
            if row.get("end_event_sha256"):
                _require(
                    end is not None and end["kind"] == "lesson",
                    "EXPORT_NATIVE_AUXILIARY",
                    "lesson row has no journal end source",
                )
                assert end is not None
                _require(
                    end["attempt_id"] == start["attempt_id"]
                    and end["sequence"] > start["sequence"]
                    and row["observed_end_mono_ms"]
                    == _plain_number(_payload(end).get("observed_mono_ms")),
                    "EXPORT_NATIVE_AUXILIARY",
                    "lesson auxiliary end differs",
                )
            if row["row_kind"] == "play":
                _require(
                    row["audio_request_id"] in bundle.journal.requests,
                    "EXPORT_NATIVE_AUXILIARY",
                    "lesson play has no actual audio request",
                )
        expected_starts = {
            e["source_event_sha256"]
            for e in events
            if e["kind"] == "lesson"
            and _payload(e).get("kind")
            in ("play_request", "display_start", "retrieval_opportunity")
        }
        _require(
            starts == expected_starts,
            "EXPORT_NATIVE_AUXILIARY",
            "lesson auxiliary omitted or added an observed interval",
        )
    if "assessment-stages.csv" in bundle.files:
        contract = _exact(
            strict_json(bundle.files["assessment-header-contract.json"], "assessment contract"),
            ("schema_version", "qualified", "headers"),
            "assessment contract",
        )
        _require(
            contract["schema_version"] == "assessment-stage-csv-provisional-1"
            and contract["qualified"] is False
            and contract["headers"] == list(ASSESSMENT_COLUMNS),
            "EXPORT_NATIVE_AUXILIARY",
            "unknown assessment contract",
        )
        rows = _table(
            bundle.files["assessment-stages.csv"], contract["headers"], "assessment stages"
        )
        observed = [e for e in events if e["kind"] == "assessment_stage"]
        _require(len(rows) == len(observed), "EXPORT_NATIVE_AUXILIARY", "assessment count differs")
        for row, event in zip(rows, observed, strict=True):
            p = _payload(event)
            _require(
                row["schema_version"] == "assessment-stage-csv-provisional-1"
                and all(
                    row[k] == bundle.identity[k]
                    for k in ("session_id", "coded_id", "visit_id", "station_id")
                )
                and row["sequence"] == str(event["sequence"])
                and all(
                    row[k] == _text(p.get(k))
                    for k in (
                        "schedule_sha256",
                        "clock_epoch",
                        "event_kind",
                        "stage",
                        "item_id",
                        "outcome_code",
                    )
                )
                and row["host_mono_ms"] == _plain_number(p.get("host_mono_ms"))
                and row["value"] == ("" if p.get("value") is None else str(p["value"])),
                "EXPORT_NATIVE_AUXILIARY",
                "assessment projection differs from journal",
            )


def _plain_number(value: object) -> str:
    if value is None:
        return ""
    number = _number(value)
    return format(number.normalize(), "f")


def _context(
    event: Event,
    root: DataRoot,
    binding: Binding,
    refs: References,
    items: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]],
    events_by_hash: Mapping[str, Event],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    p = _payload(event)
    opportunity = event["opportunity_id"]
    _require(
        opportunity in items, "EXPORT_NATIVE_SCHEDULE", "executed opportunity is not scheduled"
    )
    block, item = items[opportunity]
    schedule_path = input_path(
        "schedule",
        study=refs.study,
        unit_id=refs.unit_id,
        person_id=refs.person_id,
        visit=refs.visit,
    )
    # Use the hash recorded by load_references, never a reserialized private schedule.
    expected_schedule = refs.inputs.get(schedule_path)
    loaded = events_by_hash.get(p["loaded_event_sha256"])
    _require(
        p["person_slot"] == binding.person_id
        and p["schedule_sha256"] == expected_schedule
        and p["package_sha256"] == refs.package_sha256
        and refs.package_manifest_sha256 == refs.package_sha256
        and loaded is not None
        and loaded["kind"] == "session"
        and loaded["sequence"] < event["sequence"]
        and loaded["attempt_id"] == event["attempt_id"]
        and loaded["opportunity_id"] == event["opportunity_id"]
        and _payload(loaded).get("event") == "state_after"
        and _payload(loaded).get("state") == "Loaded"
        and _payload(loaded).get("schedule_sha256") == expected_schedule
        and _payload(loaded).get("clock_epoch") == p["session_clock_epoch"]
        and _payload(loaded).get("scheduled_onset_mono_ms") == p["scheduled_onset_mono_ms"]
        and _payload(loaded).get("audio_request_ids") == p["audio_request_ids"]
        and p["observation_clock_epoch"] == event["clock_epoch"]
        and p["clock_basis"] == "shared_host_monotonic",
        "EXPORT_NATIVE_PIN",
        "executed slot, loaded session, schedule or package pin differs",
    )
    expected = {
        "trial_type": item["trial_type"],
        "phase": block["phase"],
        "heldout": item["trained_status"] == "heldout",
        "slot_seconds": item["slot_s"],
        "plays": item["plays"],
        "pass": item["pass"],
        "presentation": item.get("presentation"),
    }
    _require(
        all(p[k] == v for k, v in expected.items()) and _text(p["content_id"]) == cue_of(item),
        "EXPORT_NATIVE_SCHEDULE",
        "executed slot facts differ from its pinned schedule item",
    )
    _require(
        isinstance(p["audio_request_ids"], list)
        and len(set(p["audio_request_ids"])) == len(p["audio_request_ids"]),
        "EXPORT_NATIVE_CONTEXT",
        "slot audio request IDs are invalid",
    )
    return block, item


def _allocation(root: DataRoot, refs: References) -> tuple[str, str]:
    reveal = load_reveal(root, refs.study, refs.set_name)
    _require(reveal is not None, "EXPORT_NATIVE_REFERENCE", "allocation is missing")
    assert reveal is not None
    if refs.study == "A":
        return "", ""
    dyad = next(d for d in reveal.list_doc["dyads"] if d["unit_id"] == refs.unit_id)
    member = next(m for m in dyad["members"] if m["slot_id"] == refs.person_id)
    return str(member["role"]), str(dyad["structured_family"])


def _targets(item: Mapping[str, Any]) -> tuple[str, str, str]:
    intended = item.get("intended") or {}
    action = referent = ""
    if intended.get("kind") == "message":
        action, referent = intended["semantic_action"], intended["semantic_referent"]
    elif intended.get("kind") == "atom":
        if intended["role"] == "action":
            action = intended["semantic_label"]
        else:
            referent = intended["semantic_label"]
    family = "" if item["trial_type"] == "no_cue" else str(intended.get("family", ""))
    return action, referent, family


def _history(
    root: DataRoot, refs: References
) -> tuple[dict[str, int], dict[str, int], dict[str, str]]:
    phrase: dict[str, int] = defaultdict(int)
    atoms: dict[str, int] = defaultdict(int)
    hashes: dict[str, str] = {}
    visits = study_visits(refs.study)  # type: ignore[arg-type]
    for visit in visits[: visits.index(refs.visit)]:
        vid = f"{refs.person_id}-{visit}"
        raw = _verified_raw(root, vid)
        _require(
            raw.trial_log is not None
            and raw.exposure_ledger is not None
            and not raw.trial_log.problems
            and not raw.exposure_ledger.problems,
            "EXPORT_NATIVE_HISTORY",
            f"complete earlier exposure history required: {vid}",
        )
        hashes.update({f"raw/{vid}/{name}": sha for name, sha in raw.files.items()})
        for play in visit_logs(raw).plays:
            _consume(play, phrase, atoms)
    return phrase, atoms, hashes


def _verified_raw(root: DataRoot, visit_id: str) -> RawVisit:
    try:
        raw = load_raw_visit(root, visit_id)
    except FileNotFoundError:
        raise ExportRefused(
            "EXPORT_NATIVE_HISTORY", f"earlier raw visit required: {visit_id}"
        ) from None
    manifest = raw.exit_manifest or {}
    _require(
        not list(validator("exit-manifest.schema.json").iter_errors(manifest)),
        "EXPORT_NATIVE_HISTORY",
        "earlier exit manifest is invalid",
    )
    listed = manifest.get("files", [])
    _require(isinstance(listed, list), "EXPORT_NATIVE_HISTORY", "earlier exit manifest missing")
    expected = {e["path"]: e["sha256"] for e in listed}
    actual = {p: h for p, h in raw.files.items() if p != EXIT_MANIFEST}
    _require(expected == actual, "EXPORT_NATIVE_HISTORY", "earlier raw files changed after closure")
    return raw


def _active_source_events(
    root: DataRoot,
    refs: References,
) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve active MenuEvent IDs to actual audio-request IDs, with closed evidence."""
    from .export_import import KEPT_BUNDLE, read_bundle

    _require(refs.partner_person_id is not None, "EXPORT_NATIVE_YOKED", "active partner missing")
    vid = f"{refs.partner_person_id}-{refs.visit}"
    raw = _verified_raw(root, vid)
    manifest = raw.exit_manifest or {}
    source = manifest.get("source") or {}
    _require(
        source.get("format") == "data-export-native-1",
        "EXPORT_NATIVE_YOKED",
        "active partner must have an imported native export",
    )
    bundle = read_bundle(root.raw_visit_dir(vid) / KEPT_BUNDLE, source["manifest_sha256"])
    active = native_events(bundle)
    links: dict[str, str] = {}
    for event in active:
        if event["kind"] == "menu_observation" and _payload(event).get("kind") == "play_request":
            p = _payload(event)
            _require(p["role"] == "active", "EXPORT_NATIVE_YOKED", "source menu is not active")
            links[p["event_id"]] = event["audio_request_id"]
    return links, {f"raw/{vid}/{p}": h for p, h in raw.files.items()}


def _consume(row: Mapping[str, str], phrase: dict[str, int], atoms: dict[str, int]) -> None:
    item = row["atom_or_message_id"]
    if not item or row["audible_status"] not in CONSUMING_AUDIBLE_STATUS:
        return
    if is_message(item):
        phrase[item] += 1
    elif components(item) == (item,):
        atoms[item] += 1


def _event_time(event: Event) -> str:
    return _ms(_payload(event).get("observed_mono_ms"))


def _lesson_play(
    events: Sequence[Event],
    request: str,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    mine = [e for e in events if e["kind"] == "lesson"]
    start = next(
        (
            e
            for e in mine
            if _payload(e).get("kind") == "play_request"
            and _payload(e).get("audio_request_id") == request
        ),
        None,
    )
    _require(start is not None, "EXPORT_NATIVE_LESSON", "lesson audio has no observed lesson play")
    assert start is not None
    p = _payload(start)
    index = p["presentation_index"]
    retrievals = [e for e in mine if _payload(e).get("kind") == "retrieval_opportunity"]
    if index == 2:
        _require(
            len(retrievals) == 1,
            "EXPORT_NATIVE_LESSON",
            "retrieval play has no unique observed retrieval opportunity",
        )
    retrieval = bool(retrievals) and index == 2
    display_id = _text(p.get("meaning_display_id")) if index != 2 else ""
    displays = [
        e
        for e in mine
        if index != 2
        and _payload(e).get("kind") == "display_start"
        and _payload(e).get("meaning_display_id") == p.get("meaning_display_id")
        and bool(_payload(e).get("feedback_content_id")) == (index == 3)
    ]
    span: dict[str, str] = {}
    sources: list[dict[str, Any]] = []
    if displays:
        _require(len(displays) == 1, "EXPORT_NATIVE_LESSON", "ambiguous lesson display")
        display = displays[0]
        dp = _payload(display)
        ends = [
            e
            for e in mine
            if e["sequence"] > display["sequence"]
            and _payload(e).get("kind") == "display_end"
            and _payload(e).get("feedback_content_id") == dp.get("feedback_content_id")
        ]
        _require(len(ends) <= 1, "EXPORT_NATIVE_LESSON", "ambiguous lesson display end")
        span = {
            "display_start_mono_ms": _event_time(display),
            "display_end_mono_ms": _event_time(ends[0]) if ends else "",
        }
        sources.append(
            {
                "kind": "meaning_display",
                "start": display["source_event_sha256"],
                "end": ends[0]["source_event_sha256"] if ends else None,
                "clock_epoch": _payload(display).get("session_clock_epoch"),
            }
        )
    result = {
        "meaning_display_id": display_id if displays else "",
        "feedback_content_id": _text(p.get("feedback_content_id")) if displays else "",
        "retrieval_opportunity": "true" if retrieval else "false",
        **span,
    }
    return result, sources


def _menu_play(
    events: Sequence[Event], request: str
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    mine = [e for e in events if e["kind"] == "menu_observation"]
    plays = [
        e
        for e in mine
        if _payload(e).get("kind") == "play_request" and e["audio_request_id"] == request
    ]
    final = [e for e in mine if _payload(e).get("kind") == "choice_final"]
    _require(
        len(plays) == 1 and len(final) == 1,
        "EXPORT_NATIVE_MENU",
        "menu requires one actual play and a terminal choice/default decision",
    )
    play, choice = plays[0], final[0]
    p, c = _payload(play), _payload(choice)
    index, selected = p["presentation_index"], c["selected_index"]
    _require(
        type(index) is int
        and 1 <= index <= 8
        and type(selected) is int
        and 1 <= selected <= 3
        and type(c["defaulted"]) is bool,
        "EXPORT_NATIVE_MENU",
        "invalid observed menu decision",
    )
    candidate_index = (index + 1) // 2 if index <= 6 else selected
    displays = [e for e in mine if _payload(e).get("kind") == "display_changed"]
    before = [e for e in displays if e["sequence"] < play["sequence"]]
    # play_request occurs before the desired onset; associate the displayed phase at
    # that observed request, never a nominal display duration.
    display = before[-1] if before else None
    ends = [e for e in displays if display is not None and e["sequence"] > display["sequence"]]
    result = {
        "candidate_id": _text(p["candidate_id"]),
        "accepted_or_rejected": "accepted" if candidate_index == selected else "rejected",
        "active_choice_or_default": "default" if c["defaulted"] else "active_choice",
        "meaning_display_id": _text(p["meaning_display_id"]) if display is not None else "",
        "display_start_mono_ms": _event_time(display) if display is not None else "",
        "display_end_mono_ms": _event_time(ends[0]) if ends else "",
        "retrieval_opportunity": "false",
        "feedback_content_id": "",
        "yoked_source_event_id": _text(p["yoked_source_event_id"]),
    }
    return result, [
        {
            "kind": "menu_decision",
            "source": choice["source_event_sha256"],
            "play": play["source_event_sha256"],
            "software_display_start": display["source_event_sha256"] if display else None,
            "software_display_end": ends[0]["source_event_sha256"] if ends else None,
        }
    ]


def _operator_deviations(data: bytes, binding: Binding, coded_id: str) -> bytes:
    rows = _table(data, DEVIATIONS_COLUMNS, "native operator deviations")
    for row in rows:
        problems = value_problems(TEMPLATES["deviations"], row)
        _require(not problems, "EXPORT_NATIVE_OPERATOR", "operator deviation fields are invalid")
        _require(
            not row["participant_id"] or row["participant_id"] == coded_id,
            "EXPORT_NATIVE_OPERATOR",
            "operator deviation participant differs from bound visit",
        )
        _require(
            not row["dyad_or_batch"] or row["dyad_or_batch"] == binding.person_id[:5],
            "EXPORT_NATIVE_OPERATOR",
            "operator deviation unit differs from bound allocation",
        )
    return data


def map_native_protocol(
    root: DataRoot,
    binding: Binding,
    bundle: Bundle,
    run_sheet: bytes,
    deviations: bytes,
) -> tuple[dict[str, bytes], dict[str, tuple[str, ...]]]:
    """Map all protocol columns from observed facts, pinned joins and operator records."""
    events = native_events(bundle)
    refs = load_references(root, binding.visit_id)
    order = _check_links(bundle)
    by_hash = {e["source_event_sha256"]: e for e in events}
    by_attempt: dict[str, list[Event]] = defaultdict(list)
    slots: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if isinstance(event["attempt_id"], str):
            by_attempt[event["attempt_id"]].append(event)
        if event["kind"] == "slot_context":
            key = event["attempt_id"]
            _require(
                isinstance(key, str)
                and all(
                    _payload(prior)["loaded_event_sha256"] != _payload(event)["loaded_event_sha256"]
                    for prior in slots[key]
                ),
                "EXPORT_NATIVE_CONTEXT",
                "executed attempt has missing or repeated loaded-context source",
            )
            slots[key].append(event)
    _require(
        set(slots) == {r["attempt_id"] for r in bundle.trials},
        "EXPORT_NATIVE_CONTEXT",
        "slot contexts and exported attempts differ",
    )
    items = {i["trial_id"]: (b, i) for b, i in schedule_items(refs.schedules[refs.visit])}
    role, scaffold = _allocation(root, refs)
    phrase, atoms, history_hashes = _history(root, refs)
    active_sources: dict[str, str] = {}
    if role == "yoked" and any(
        _payload(e).get("phase") == "selection" for contexts in slots.values() for e in contexts
    ):
        active_sources, active_hashes = _active_source_events(root, refs)
        history_hashes.update(active_hashes)
    trial_ids: dict[str, str] = {}
    original: dict[str, str] = {}
    for row in bundle.trials:
        opportunity, attempt = row["opportunity_id"], row["attempt_id"]
        if not row["retry_of"]:
            _require(
                opportunity not in original, "EXPORT_NATIVE_RETRY", "multiple original attempts"
            )
            original[opportunity] = attempt
            trial_ids[attempt] = opportunity
        else:
            _require(
                original.get(opportunity) == row["retry_of"],
                "EXPORT_NATIVE_RETRY",
                "retry does not name its actual original attempt",
            )
            trial_ids[attempt] = f"{opportunity}-retry-{attempt}"
    native_plays = {r["audio_request_id"]: r for r in bundle.exposures}
    requests = {e["audio_request_id"]: e for e in events if e["kind"] == "audio_request"}
    trial_rows: list[dict[str, str]] = []
    play_rows: list[dict[str, str]] = []
    provenance: list[dict[str, Any]] = []
    for native in bundle.trials:
        attempt = native["attempt_id"]
        mine = by_attempt[attempt]
        ids = order[attempt]
        for candidate in slots[attempt]:
            _context(candidate, root, binding, refs, items, by_hash)
        actual = [e for e in mine if e["kind"] in ("audio_request", "panel_response")]
        first_actual = min((e["sequence"] for e in actual), default=math.inf)
        candidates = [
            e
            for e in slots[attempt]
            if e["sequence"] < first_actual and set(ids) <= set(_payload(e)["audio_request_ids"])
        ]
        _require(
            candidates,
            "EXPORT_NATIVE_CONTEXT",
            "actual requests span incompatible or unbound replacement slot contexts",
        )
        slot = candidates[-1]
        sp = _payload(slot)
        _, item = _context(slot, root, binding, refs, items, by_hash)
        for event in mine:
            if event["kind"] in ("lesson", "menu_observation"):
                p = _payload(event)
                _require(
                    p["schedule_sha256"] == sp["schedule_sha256"]
                    and p["package_sha256"] == sp["package_sha256"],
                    "EXPORT_NATIVE_PIN",
                    "native content event pins differ from executed slot",
                )
                if event["kind"] == "lesson":
                    _require(
                        p["session_clock_epoch"] == sp["session_clock_epoch"],
                        "EXPORT_NATIVE_TIME",
                        "lesson session-clock binding differs",
                    )
                else:
                    _require(
                        p["role"] == role,
                        "EXPORT_NATIVE_MENU",
                        "observed menu role differs from allocation",
                    )
        _require(
            ids == sp["audio_request_ids"][: len(ids)] or set(ids) <= set(sp["audio_request_ids"]),
            "EXPORT_NATIVE_CONTEXT",
            "actual requests are outside their executed slot",
        )
        cue = cue_of(item)
        target_action, target_referent, family = _targets(item)
        row = dict.fromkeys((*TRIAL_LOG_COLUMNS, *EXTENSION_COLUMNS["trial-log"]), "")
        for key in (
            "waveform_sha256",
            "audio_request_mono_ms",
            "audio_onset_estimate_mono_ms",
            "onset_uncertainty_ms",
            "playback_status",
            "frame_freeze_ms",
            "reset_ok",
            "focus_ok",
            "response_target",
            "response_action",
            "response_code",
            "technical_fault_code",
            "exposure_consumed",
            "deviation_id",
            "method_masked",
        ):
            row[key] = native[key]
        row.update(
            study=refs.study,
            protocol_version=bundle.identity["protocol_version"],
            participant_id=bundle.identity["coded_id"],
            dyad_id=refs.unit_id if refs.study == "B" else "",
            batch_id=refs.unit_id if refs.study == "A" else "",
            codebook_id=refs.package_id,
            session_id=bundle.identity["session_id"],
            visit=refs.visit,
            trial_id=trial_ids[attempt],
            retry_of=trial_ids[native["retry_of"]] if native["retry_of"] else "",
            role=role,
            scaffold_family=scaffold,
            semantic_family=family,
            trial_type=item["trial_type"],
            message_id=cue,
            target_action=target_action,
            target_referent=target_referent,
            trained_status=item["trained_status"],
            prior_complete_phrase_exposures=str(phrase.get(cue, 0)),
            prior_atom_exposures=str(sum(atoms.get(a, 0) for a in components(cue))),
            scheduled_onset_mono_ms=_ms(_payload(requests[ids[0]]).get("scheduled_mono_ms"))
            if ids
            else _ms(sp["scheduled_onset_mono_ms"]),
            pcm_sha256=bundle.journal.requests[ids[0]][2] if ids else "",
        )
        _require(
            sp["dictionary_policy"] == "prohibited",
            "EXPORT_NATIVE_POLICY",
            "dictionary availability is not reported by an enforced native gate",
        )
        row["dictionary_available"] = "false"
        feedback = [
            e
            for e in mine
            if e["kind"] == "lesson"
            and _payload(e).get("kind") == "display_start"
            and _payload(e).get("feedback_content_id")
        ]
        _require(
            sp["feedback_policy"] in ("prohibited", "scheduled_lesson"),
            "EXPORT_NATIVE_POLICY",
            "feedback state is not observed or explicitly prohibited",
        )
        if sp["feedback_policy"] == "scheduled_lesson":
            _require(feedback, "EXPORT_NATIVE_POLICY", "scheduled feedback has no observed display")
        else:
            _require(not feedback, "EXPORT_NATIVE_POLICY", "observed feedback contradicts gate")
        row["feedback_shown"] = "true" if feedback else "false"
        responses = [e for e in mine if e["kind"] == "panel_response"]
        if responses:
            first = responses[0]
            p = _payload(first)
            _require(
                p.get("response_code") == row["response_code"],
                "EXPORT_NATIVE_RESPONSE",
                "first panel response disagrees with exported response",
            )
            if row["response_code"] == "commit":
                _require(
                    row["response_action"] == _text(p.get("response_action"))
                    and row["response_target"] == _text(p.get("response_target")),
                    "EXPORT_NATIVE_RESPONSE",
                    "first committed labels disagree with export",
                )
                row["commit_mono_ms"] = _event_time(first)
                if row["audio_onset_estimate_mono_ms"] and ids and item["trial_type"] != "no_cue":
                    onset_events = [
                        e
                        for e in mine
                        if e["kind"] == "audio_observation"
                        and e["audio_request_id"] == ids[0]
                        and _payload(e).get("onset_estimate_mono_ms") is not None
                        and _ms(_payload(e)["onset_estimate_mono_ms"])
                        == _ms(row["audio_onset_estimate_mono_ms"])
                    ]
                    _require(
                        onset_events
                        and all(
                            e["clock_epoch"] == sp["observation_clock_epoch"]
                            for e in (first, *onset_events)
                        ),
                        "EXPORT_NATIVE_TIME",
                        "panel/onset observations lack a common declared host clock",
                    )
                    row["response_time_ms"] = str(
                        int(row["commit_mono_ms"]) - int(_ms(row["audio_onset_estimate_mono_ms"]))
                    )
        test = item["trial_type"] in ("pre_old", "trained", "novel", "atomic", "no_cue", "speech")
        if test and row["response_code"]:
            action_ok = (
                bool(target_action)
                and row["response_code"] == "commit"
                and row["response_action"] == target_action
            )
            referent_ok = (
                bool(target_referent)
                and row["response_code"] == "commit"
                and row["response_target"] == target_referent
            )
            row["action_correct"] = str(action_ok).lower() if target_action else ""
            row["referent_correct"] = str(referent_ok).lower() if target_referent else ""
            row["exact_correct"] = str(
                all(
                    (action_ok if target_action else True, referent_ok if target_referent else True)
                )
            ).lower()
        for key in (
            "audio_request_mono_ms",
            "audio_onset_estimate_mono_ms",
            "onset_uncertainty_ms",
            "frame_freeze_ms",
        ):
            row[key] = _ms(row[key])
        # No acoustic offset or cross-clock RT is fabricated. Exact software end events
        # are retained below and in the original native observation document.
        sources: dict[str, Any] = {
            "attempt_id": attempt,
            "opportunity_id": native["opportunity_id"],
            "protocol_trial_id": row["trial_id"],
            "slot_source": slot["source_event_sha256"],
            "all_slot_sources": [e["source_event_sha256"] for e in slots[attempt]],
            "software_callback_ends": [],
            "play_sources": [],
        }
        for request in ids:
            audio = native_plays[request]
            play = dict.fromkeys(
                (*EXPOSURE_LEDGER_COLUMNS, *EXTENSION_COLUMNS["exposure-ledger"]), ""
            )
            play.update(
                participant_id=bundle.identity["coded_id"],
                dyad_id=row["dyad_id"],
                session_id=bundle.identity["session_id"],
                wave=binding.values["binding:wave"],
                event_id=request,
                yoked_source_event_id=audio["yoked_source_event_id"],
                stage=item["trial_type"],
                atom_or_message_id=cue,
                waveform_sha256=audio["waveform_sha256"],
                whole_phrase=str(is_message(cue)).lower(),
                presentation_index=str(ids.index(request) + 1),
                audio_onset_mono_ms=_ms(audio["audio_onset_estimate_mono_ms"]),
                audible_status=audio["audible_status"],
                pause_ms=_ms(audio["pause_ms"]),
                matching_deviation_id=audio["matching_deviation_id"],
                pcm_sha256=bundle.journal.requests[request][2],
                trial_ref=row["trial_id"],
            )
            if item["trial_type"] in ("atomic_lesson", "message_lesson"):
                facts, spans = _lesson_play(mine, request)
            elif item["trial_type"] in ("profile_menu", "atom_menu"):
                facts, spans = _menu_play(mine, request)
                if role == "yoked":
                    native_source = facts["yoked_source_event_id"]
                    _require(
                        native_source in active_sources,
                        "EXPORT_NATIVE_YOKED",
                        "yoked play has no verified active menu source",
                    )
                    facts["yoked_source_event_id"] = active_sources[native_source]
                    spans.append(
                        {
                            "native_yoked_source_event_id": native_source,
                            "protocol_source_audio_request_id": active_sources[native_source],
                        }
                    )
                _require(
                    not audio["candidate_id"] or audio["candidate_id"] == facts["candidate_id"],
                    "EXPORT_NATIVE_MENU",
                    "menu candidate differs from audio projection",
                )
                _require(
                    audio["accepted_or_rejected"] in ("pending", "", facts["accepted_or_rejected"]),
                    "EXPORT_NATIVE_MENU",
                    "menu final choice contradicts audio projection",
                )
            else:
                opened = any(
                    e["kind"] == "panel_process" and _payload(e).get("kind") == "panel_open"
                    for e in mine
                )
                facts, spans = {"retrieval_opportunity": str(opened).lower()}, []
            play.update(facts)
            play_rows.append(play)
            _consume(play, phrase, atoms)
            sources["play_sources"].append({"audio_request_id": request, "sources": spans})
            sources["software_callback_ends"].extend(
                {
                    "audio_request_id": request,
                    "observed_mono_ms": _payload(e).get("observed_mono_ms"),
                    "source": e["source_event_sha256"],
                    "acoustic_authority": False,
                }
                for e in mine
                if e["audio_request_id"] == request
                and e["kind"] == "audio_observation"
                and _payload(e).get("code") == "AUDIO_PLAYBACK_COMPLETED"
            )
        trial_rows.append(row)
        provenance.append(sources)
    operator = _operator_deviations(deviations, binding, bundle.identity["coded_id"])
    for template, rows in (("trial-log", trial_rows), ("exposure-ledger", play_rows)):
        for row in rows:
            problems = value_problems(TEMPLATES[template], row)  # type: ignore[index]
            _require(
                not problems,
                "EXPORT_NATIVE_DOMAIN",
                f"{template}: mapped values outside protocol domains: {problems[:1]}",
            )
    unknowns = {
        "trial-log": tuple(
            c
            for c in TRIAL_LOG_COLUMNS
            if any(not r[c] for r in trial_rows)
            and c in ("audio_offset_mono_ms", "sim_time", "actual_delay_hours", "response_time_ms")
        ),
        "exposure-ledger": ("audio_offset_mono_ms",) if play_rows else (),
        "deviations": (),
    }
    report = {
        "format": "av-analysis/native-protocol-mapping",
        "format_version": 1,
        "visit_id": binding.visit_id,
        "source_manifest_sha256": bundle.manifest_sha256,
        "producer_headers_qualified": bundle.manifest["headers_qualified"],
        "acoustic_authority": False,
        "time_resolution_ms": 1,
        "rounding": "half_even",
        "reference_inputs": dict(refs.inputs),
        "prior_history_inputs": history_hashes,
        "operator_run_sheet_sha256": sha256_bytes(run_sheet),
        "operator_deviations_sha256": sha256_bytes(deviations),
        "optional_unknown_columns": unknowns,
        "unknown_reasons": {
            "audio_offset_mono_ms": (
                "no acoustic offset authority; software completions retained separately"
            ),
            "sim_time": "no simulator time observation in native contract",
            "actual_delay_hours": "no observed wall-clock/monotonic anchor for per-item encounters",
            "response_time_ms": "no observed commit/audible onset in a declared common clock",
        },
        "attempts": provenance,
    }
    columns_t = (*TRIAL_LOG_COLUMNS, *EXTENSION_COLUMNS["trial-log"])
    columns_e = (*EXPOSURE_LEDGER_COLUMNS, *EXTENSION_COLUMNS["exposure-ledger"])
    return {
        "trial-log.csv": csv_bytes(columns_t, [[r[c] for c in columns_t] for r in trial_rows]),
        "exposure-ledger.csv": csv_bytes(columns_e, [[r[c] for c in columns_e] for r in play_rows]),
        "deviations.csv": operator,
        "mapping/native-protocol.json": json_bytes(report),
    }, unknowns
