"""Rules of checks C1-C8 beyond the fault suite (#33): raw integrity, references, hashes,
exposure, growth, yoked ledgers, windows and deviation links."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta

import pytest

from av_analysis.fileio import csv_bytes, json_bytes, parse_csv, read_bytes
from av_analysis.loaders import RefusedInputError, load_raw_visit
from av_analysis.paths import DataRoot, remove_synthetic_input, write_synthetic_input
from av_analysis.reconcile import (
    build_context,
    partner_slot,
    reconcile_visit,
    related_visits,
    run_checks,
)
from av_analysis.reconcile_checks import (
    LINK_CATEGORIES,
    Found,
    _longest_increasing,
    link,
    usable,
    visit_date,
)
from av_analysis.references import (
    ExpectedHash,
    ReferenceError,
    canonical_sha256,
    load_references,
    revealed_persons,
    set_of_unit,
)
from av_analysis.synthetic_logs import build_synthetic_root, refresh_exit_manifest, suite_visits

SEED = "DEMO-test-33-checks"


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return build_synthetic_root(
        tmp_path_factory.mktemp("checks") / "base", seed_label=SEED, max_persons=1
    )


@pytest.fixture
def root(base, tmp_path):
    target = tmp_path / "root"
    shutil.copytree(base.path, target)
    return DataRoot.open(target)


@pytest.fixture
def visits(root):
    return suite_visits(root)


def read_rows(root, vid, name):
    header, records = parse_csv(read_bytes(root.raw_visit_dir(vid) / name))
    return list(header), [dict(zip(header, r, strict=True)) for r in records]


def write_rows(root, vid, name, header, rows, *, manifest=True):
    data = csv_bytes(header, ([r[c] for c in header] for r in rows))
    write_synthetic_input(root, "raw", f"{vid}/{name}", data)
    if manifest:
        refresh_exit_manifest(root, vid)


def edit(root, vid, name, change, *, manifest=True):
    header, rows = read_rows(root, vid, name)
    change(rows)
    write_rows(root, vid, name, header, rows, manifest=manifest)


def add_deviation(root, vid, **fields):
    header, rows = read_rows(root, vid, "deviations.csv")
    row = dict.fromkeys(header, "")
    row.update(
        deviation_id=f"DEV-{len(rows) + 1}",
        timestamp="2027-03-01T12:00:00+01:00",
        protocol_version="v0.1",
        operator="S01",
        category="technical",
    )
    row.update(fields)
    write_rows(root, vid, "deviations.csv", header, [*rows, row])
    return row["deviation_id"]


def codes(report, check=None):
    return sorted(
        d.code for c in report.checks for d in c.discrepancies if check in (None, c.check)
    )


def status(report, check):
    return next(c.status for c in report.checks if c.check == check)


# ---------------------------------------------------------------------------------------
# C1


def test_c1_manifest_files_and_format(root, visits):
    vid = visits["D0"]
    folder = root.raw_visit_dir(vid)
    edit(
        root, vid, "trial-log.csv", lambda rows: rows[0].update(playback_status="?"), manifest=False
    )
    write_synthetic_input(root, "raw", f"{vid}/notes.csv", b"a\n1\n")
    report = reconcile_visit(root, vid)
    assert {"RAW_HASH_CHANGED", "RAW_FILE_UNLISTED", "RAW_FORMAT"} <= set(codes(report, "C1"))
    assert status(report, "C1") == "fail" and status(report, "C8") == "fail"
    remove_synthetic_input(root, "raw", f"{vid}/notes.csv")
    remove_synthetic_input(root, "raw", f"{vid}/deviations.csv")
    report = reconcile_visit(root, vid)
    assert "RAW_FILE_MISSING" in codes(report, "C1")
    manifest = json.loads((folder / "exit-manifest.json").read_text(encoding="utf-8"))
    manifest["visit_id"] = visits["D7"]
    manifest["closed"] = "maybe"
    write_synthetic_input(root, "raw", f"{vid}/exit-manifest.json", json_bytes(manifest))
    found = [d.detail for c in reconcile_visit(root, vid).checks for d in c.discrepancies]
    assert any("names another visit" in d for d in found)
    assert any("schema violation" in d for d in found)
    remove_synthetic_input(root, "raw", f"{vid}/exit-manifest.json")
    assert "RAW_MANIFEST_MISSING" in codes(reconcile_visit(root, vid), "C1")


def test_c1_identity_columns_and_run_sheet_times(root, visits):
    vid = visits["D7"]
    edit(root, vid, "trial-log.csv", lambda rows: rows[3].update(participant_id="DEMO-X9"))
    edit(
        root,
        vid,
        "visit-run-sheet.csv",
        lambda rows: [r.update(start_time="", end_time="") for r in rows],
    )
    report = reconcile_visit(root, vid)
    details = [d.detail for c in report.checks for d in c.discrepancies if c.check == "C1"]
    assert "participant_id differs from the visit identity" in details
    assert "no start_time recorded" in details
    assert "DEMO-X9" not in json.dumps(report.document())


def test_c1_exposure_ledger_without_trial_ref(root, visits):
    vid = visits["D7"]
    header, rows = read_rows(root, vid, "exposure-ledger.csv")
    header = [c for c in header if c != "trial_ref"]
    write_rows(root, vid, "exposure-ledger.csv", header, rows)
    report = reconcile_visit(root, vid)
    assert any("trial_ref" in d.detail for c in report.checks for d in c.discrepancies)
    assert "COUNT_MISSING_PLAY" not in codes(report)


def test_c1_reference_input_missing_or_invalid(root, visits):
    vid = visits["D7"]
    person = vid.rsplit("-", 1)[0]
    rel = f"schedules/A/{person[:5]}/run-sheets/{person}/D7.csv"
    remove_synthetic_input(root, "inputs", rel)
    report = reconcile_visit(root, vid)
    assert codes(report, "C1") == ["REFERENCE_INPUT"]
    assert {status(report, c) for c in ("C2", "C3", "C4")} == {"not_applicable"}
    assert any(i[0] == f"inputs/schedules/A/{person[:5]}-x" for i in report.inputs) is False
    with pytest.raises(ReferenceError, match="missing"):
        load_references(root, vid)


def test_reference_loading_rules(root, visits):
    vid = visits["V2"]
    refs = load_references(root, vid)
    assert refs.study == "B" and refs.set_name == "pilot" and refs.visit_id == vid
    assert refs.partner_person_id == partner_slot(refs.person_id)
    assert refs.active_person_id in (refs.person_id, refs.partner_person_id)
    assert refs.package_sha256 == refs.package_manifest_sha256
    assert set(refs.store_snapshots) == {"V1", "V2"} and len(refs.store_receipts) == 17
    assert all(isinstance(e, ExpectedHash) for e in refs.expected_hashes.values())
    assert set_of_unit("B-S03") == "confirmatory"
    assert revealed_persons(root)[refs.person_id] == refs.participant_id
    assert related_visits(vid) == ([vid[:-2] + "V1"], partner_slot(refs.person_id) + "-V2")
    write_synthetic_input(
        root, "inputs", "schedules/A/pilot-book-key.json", json_bytes({"demo": True})
    )
    with pytest.raises(RefusedInputError, match="book key"):
        load_references(root, vid)


def test_references_refuse_unrevealed_slots_and_bad_packages(root, visits):
    vid = visits["D0"]
    refs = load_references(root, vid)
    rel = f"packages/{refs.package_id}/audio.json"
    write_synthetic_input(root, "inputs", rel, b'{"format": "x"}')
    with pytest.raises(ReferenceError, match="audio.json"):
        load_references(root, vid)
    with pytest.raises(ReferenceError, match="not revealed"):
        load_references(root, vid.replace("-L", "-X").replace("-X", "-L", 1)[:-6] + "L06-D0")


# ---------------------------------------------------------------------------------------
# C2 and C3


def test_c2_order_item_and_run_sheet_rules(root, visits):
    vid = visits["D7"]

    def swap(rows):
        rows[0], rows[5] = rows[5], rows[0]
        rows[7]["message_id"] = rows[8]["message_id"]

    edit(root, vid, "trial-log.csv", swap)
    edit(root, vid, "visit-run-sheet.csv", lambda rows: rows[1].update(actual_count=""))
    report = reconcile_visit(root, vid)
    details = [d.detail for c in report.checks for d in c.discrepancies if c.check == "C2"]
    assert any("outside the scheduled order" in d for d in details)
    assert any("trial logged as trained" in d for d in details)
    assert "actual_count not recorded" in details
    assert _longest_increasing([1, 5, 2, 3, 9, 4]) == {0, 2, 3, 5}
    assert _longest_increasing([]) == set()


def test_c2_extra_trials_and_unlinked_plays(root, visits):
    vid = visits["D7"]
    header, rows = read_rows(root, vid, "trial-log.csv")
    extra = {**rows[0], "trial_id": "EXTRA-1"}
    write_rows(root, vid, "trial-log.csv", header, [*rows, extra])
    lh, plays = read_rows(root, vid, "exposure-ledger.csv")
    stray = {**plays[0], "event_id": "E-stray", "trial_ref": "NOWHERE"}
    write_rows(root, vid, "exposure-ledger.csv", lh, [*plays, stray])
    report = reconcile_visit(root, vid)
    assert {"COUNT_EXTRA_TRIAL", "COUNT_EXTRA_PLAY"} <= set(codes(report, "C2"))


def test_c3_missing_hash_and_package_mismatch(root, visits):
    vid = visits["V3"]

    def blank(rows):
        rows[-1].update(waveform_sha256="", pcm_sha256="")

    edit(root, vid, "trial-log.csv", blank)
    edit(
        root,
        vid,
        "visit-run-sheet.csv",
        lambda rows: rows[0].update(hash_check="sha256:" + "0" * 64),
    )
    report = reconcile_visit(root, vid)
    assert {"WAVEFORM_HASH_MISSING", "PACKAGE_HASH_MISMATCH"} <= set(codes(report, "C3"))
    assert report.document()["summary"]["suspension_events"] == ["WRONG_FILE_MAPPING"]


# ---------------------------------------------------------------------------------------
# C4


def test_c4_consumption_answer_display_and_repeats(root, visits):
    vid = visits["D7"]

    def change(rows):
        rows[0].update(exposure_consumed="false")
        rows[1].update(feedback_shown="true")
        novel = next(r for r in rows if r["trial_type"] == "novel")
        novel["playback_status"] = "uncertain"

    edit(root, vid, "trial-log.csv", change)
    report = reconcile_visit(root, vid)
    assert {"UNCERTAIN_NOT_CONSUMED", "ANSWER_DISPLAY_LEAK"} <= set(codes(report, "C4"))
    assert report.document()["summary"]["suspension_events"] == ["ANSWER_LEAK"]


def test_c4_valid_retry_of_a_no_onset_failure(root, visits):
    vid = visits["D7"]
    header, rows = read_rows(root, vid, "trial-log.csv")
    lh, plays = read_rows(root, vid, "exposure-ledger.csv")
    i = next(k for k, r in enumerate(rows) if r["trial_type"] == "novel")
    original = rows[i]
    original.update(playback_status="confirmed_no_onset", exposure_consumed="false")
    for p in plays:
        if p["trial_ref"] == original["trial_id"]:
            p["audible_status"] = "confirmed_no_onset"
            retry_play = {
                **p,
                "event_id": "E-retry",
                "trial_ref": original["trial_id"] + "-R1",
                "audible_status": "confirmed_audible",
            }
    retry = {
        **original,
        "trial_id": original["trial_id"] + "-R1",
        "retry_of": original["trial_id"],
        "playback_status": "observed_complete",
        "exposure_consumed": "true",
    }
    last = max(k for k, r in enumerate(rows) if r["trial_type"] == "novel")
    rows.insert(last + 1, retry)
    plays.append(retry_play)
    write_rows(root, vid, "trial-log.csv", header, rows)
    write_rows(root, vid, "exposure-ledger.csv", lh, plays)
    report = reconcile_visit(root, vid)
    assert "RETRY_LINK_BROKEN" not in codes(report)
    assert "HOLDOUT_REPEAT_AS_NOVEL" not in codes(report)
    # a second retry of the same trial is broken
    rows.insert(last + 2, {**retry, "trial_id": original["trial_id"] + "-R2"})
    write_rows(root, vid, "trial-log.csv", header, rows)
    assert "RETRY_LINK_BROKEN" in codes(reconcile_visit(root, vid), "C4")


def test_c4_repeat_logged_as_novel_after_uncertain_onset(root, visits):
    vid = visits["D7"]
    header, rows = read_rows(root, vid, "trial-log.csv")
    novel = next(r for r in rows if r["trial_type"] == "novel")
    retry = {**novel, "trial_id": novel["trial_id"] + "-R1", "retry_of": novel["trial_id"]}
    last = max(k for k, r in enumerate(rows) if r["trial_type"] == "novel")
    rows.insert(last + 1, retry)
    write_rows(root, vid, "trial-log.csv", header, rows)
    found = codes(reconcile_visit(root, vid), "C4")
    assert "HOLDOUT_REPEAT_AS_NOVEL" in found and "RETRY_LINK_BROKEN" in found


# ---------------------------------------------------------------------------------------
# C5, C6 and C7


def store_edit(root, vid, change):
    person, visit = vid.rsplit("-", 1)
    rel = f"store-snapshots/{person[:5]}/{visit}.json"
    snap = json.loads(read_bytes(root.input_path("inputs", rel)))
    change(snap)
    snap["manifest_sha256"] = canonical_sha256(snap, "manifest_sha256")
    write_synthetic_input(root, "inputs", rel, json_bytes(snap))


def test_c5_profile_head_and_receipt_chain(root, visits):
    vid = visits["V3"]
    store_edit(root, vid, lambda s: s.update(profile="P9", book_head="f" * 64))
    found = [d.detail for c in reconcile_visit(root, vid).checks for d in c.discrepancies]
    assert any("profile changed" in d for d in found)
    assert any("book_head is not the after_head" in d for d in found)
    rel = f"store-snapshots/{vid[:5]}/receipts.jsonl"
    lines = read_bytes(root.input_path("inputs", rel)).splitlines(keepends=True)
    write_synthetic_input(root, "inputs", rel, b"".join([lines[0], *lines[2:]]))
    store_edit(root, visits["V2"], lambda s: s["entries"].pop())
    found = [d.detail for c in reconcile_visit(root, visits["V2"]).checks for d in c.discrepancies]
    assert any("before_head does not continue" in d for d in found)
    assert any("lacks atoms" in d for d in found)


def test_c5_reports_receipts_only_up_to_the_visit_head(root, visits):
    rel = f"store-snapshots/{visits['V1'][:5]}/receipts.jsonl"
    lines = read_bytes(root.input_path("inputs", rel)).splitlines(keepends=True)
    write_synthetic_input(root, "inputs", rel, b"".join(lines[:-2] + lines[-1:]))
    assert status(reconcile_visit(root, visits["V1"]), "C5") == "pass"
    assert status(reconcile_visit(root, visits["V3"]), "C5") == "fail"


def test_c5_snapshot_missing_is_a_reference_input(root, visits):
    vid = visits["W1"]
    remove_synthetic_input(root, "inputs", f"store-snapshots/{vid[:5]}/W1.json")
    report = reconcile_visit(root, vid)
    assert "REFERENCE_INPUT" in codes(report, "C1")
    assert "WAVEFORM_HASH_MISMATCH" not in codes(report)  # V3 ranks are used instead


def test_c6_gap_and_missing_partner(root, visits):
    vid = visits["V1"]
    partner = partner_slot(vid.rsplit("-", 1)[0]) + "-V1"
    for target in (vid, partner):  # the second session starts before the first one ends

        def shift(rows, target=target):
            for r in rows:
                for col in ("start_time", "end_time"):
                    t = datetime.fromisoformat(r[col])
                    hour = 9 if target == vid else 9
                    r[col] = t.replace(hour=hour).isoformat()

        edit(root, target, "visit-run-sheet.csv", shift)
    assert "YOKED_GAP" in codes(reconcile_visit(root, vid), "C6")
    shutil.rmtree(root.raw_visit_dir(partner))
    report = reconcile_visit(root, vid)
    assert codes(report, "C6") == ["YOKED_SOURCE_MISSING"]
    assert report.checks[5].discrepancies[0].rows == (partner,)


def test_c6_unmatched_and_reordered_events(root, visits):
    vid = visits["V2"]
    person = vid.rsplit("-", 1)[0]
    refs = load_references(root, vid)
    yoked = refs.partner_person_id if refs.active_person_id == person else person
    yvid = f"{yoked}-V2"

    def change(rows):
        menu = [r for r in rows if r["stage"] == "atom_menu"]
        menu[0]["yoked_source_event_id"], menu[1]["yoked_source_event_id"] = (
            menu[1]["yoked_source_event_id"],
            menu[0]["yoked_source_event_id"],
        )
        menu[2]["yoked_source_event_id"] = ""

    edit(root, yvid, "exposure-ledger.csv", change)
    found = codes(reconcile_visit(root, vid), "C6")
    assert "YOKED_SOURCE_MISSING" in found and "YOKED_MISMATCH" in found


def test_c7_early_visit_and_order(root, visits):
    vid = visits["D7"]

    def early(rows):
        for r in rows:
            for col in ("start_time", "end_time"):
                r[col] = (datetime.fromisoformat(r[col]) - timedelta(days=7)).isoformat()

    edit(root, vid, "visit-run-sheet.csv", early)
    found = codes(reconcile_visit(root, vid), "C7")
    assert found == ["VISIT_ORDER", "WINDOW_EARLY"]
    shutil.rmtree(root.raw_visit_dir(visits["D0"]))
    found = [d.detail for c in reconcile_visit(root, vid).checks for d in c.discrepancies]
    assert any("anchor visit D0 has no raw logs" in d for d in found)
    assert visit_date(None) is None


# ---------------------------------------------------------------------------------------
# C8 and deviation links


def test_c8_unknown_deviation_and_correction(root, visits):
    vid = visits["D0"]
    edit(root, vid, "trial-log.csv", lambda rows: rows[2].update(deviation_id="DEV-404"))
    report = reconcile_visit(root, vid)
    assert codes(report, "C8") == ["DEVIATION_UNKNOWN"] and status(report, "C8") == "fail"
    tid = read_rows(root, vid, "trial-log.csv")[1][2]["trial_id"]
    add_deviation(root, vid, event_id=tid, category="correction")
    report = reconcile_visit(root, vid)
    assert status(report, "C8") == "explained" and report.passed


def test_visit_level_records_resolve_only_fitting_categories(root, visits):
    vid = visits["D7"]

    def late(rows):
        for r in rows:
            for col in ("start_time", "end_time"):
                r[col] = (datetime.fromisoformat(r[col]) + timedelta(days=4)).isoformat()

    edit(root, vid, "visit-run-sheet.csv", late)
    add_deviation(root, vid, event_id=vid, category="technical")
    assert status(reconcile_visit(root, vid), "C7") == "fail"
    add_deviation(root, vid, event_id=vid.rsplit("-", 1)[0], category="window")
    report = reconcile_visit(root, vid)
    assert status(report, "C7") == "explained" and report.passed
    assert set(LINK_CATEGORIES) >= {"WINDOW_LATE", "DEVIATION_MISSING"}


def test_link_prefers_row_records_and_participant_records(root, visits):
    vid = visits["D0"]
    raw = load_raw_visit(root, vid)
    refs = load_references(root, vid)
    ctx = build_context(root, raw, refs)
    found = Found("C2", "COUNT_EXTRA_PLAY", ("E-x",), "x")
    assert link(ctx, found).resolved is False
    add_deviation(root, vid, participant_id=refs.participant_id, category="audio")
    ctx = build_context(root, load_raw_visit(root, vid), refs)
    linked = link(ctx, found)
    assert linked.resolved and linked.deviation_id == "DEV-1"
    assert link(ctx, Found("C8", "DEVIATION_MISSING", ("E-x",), "x")).resolved is False


def test_run_checks_and_usable(root, visits):
    vid = visits["D0"]
    results = run_checks(load_raw_visit(root, vid), load_references(root, vid), root)
    assert [r.check for r in results] == [f"C{i}" for i in range(1, 9)]
    assert usable(None) is False
    with pytest.raises(ValueError, match="no raw folder"):
        reconcile_visit(root, "A-P03-L06-D0")


# ---------------------------------------------------------------------------------------
# Reference inputs: fatal problems raise, non-fatal ones become REFERENCE_INPUT


def _inputs_json(root, rel):
    return json.loads(read_bytes(root.input_path("inputs", rel)))


def _put_json(root, rel, doc):
    write_synthetic_input(root, "inputs", rel, json_bytes(doc))


def _schedule_rel(vid):
    person, visit = vid.rsplit("-", 1)
    return f"schedules/{person[0]}/{person[:5]}/schedules/{person}/{visit}.json"


def _fatal_cases():
    def no_list(root, vid):
        remove_synthetic_input(root, "inputs", "schedules/A/pilot-slots.json")

    def no_log(root, vid):
        remove_synthetic_input(root, "inputs", "reveal/A-pilot.jsonl")

    def edited_log(root, vid):
        data = read_bytes(root.input_path("inputs", "reveal/A-pilot.jsonl"))
        write_synthetic_input(root, "inputs", "reveal/A-pilot.jsonl", data.replace(b"S01", b"S02"))

    def mapping_key(root, vid):
        doc = _inputs_json(root, "schedules/A/pilot-package-hashes.json")
        doc["packages"] = {"BK-P-XXXXXX": "0" * 64}
        _put_json(root, "schedules/A/pilot-package-hashes.json", doc)

    def mapping_bad(root, vid):
        _put_json(root, "schedules/A/pilot-package-hashes.json", {"format": "x"})

    def schedule_identity(root, vid):
        doc = _inputs_json(root, _schedule_rel(vid))
        doc["visit"] = "D7"
        _put_json(root, _schedule_rel(vid), doc)

    def schedule_format(root, vid):
        _put_json(root, _schedule_rel(vid), {"format": "other"})

    def schedule_content(root, vid):
        doc = _inputs_json(root, _schedule_rel(vid))
        doc["blocks"][-1]["items"].pop()
        _put_json(root, _schedule_rel(vid), doc)

    def manifest_study(root, vid):
        refs = load_references(root, vid)
        rel = f"packages/{refs.package_id}/manifest.json"
        doc = _inputs_json(root, rel)
        doc["study"] = "B"
        _put_json(root, rel, doc)

    def audio_entry(root, vid):
        refs = load_references(root, vid)
        rel = f"packages/{refs.package_id}/audio.json"
        doc = _inputs_json(root, rel)
        del doc["atoms"][0]["pcm_sha256"]
        data = json_bytes(doc)
        write_synthetic_input(root, "inputs", rel, data)
        mrel = f"packages/{refs.package_id}/manifest.json"
        manifest = _inputs_json(root, mrel)
        manifest["files"]["audio.json"] = {"bytes": len(data), "sha256": sha256(data)}
        _put_json(root, mrel, manifest)

    return {
        "no_list": (no_list, "missing"),
        "no_log": (no_log, "missing"),
        "edited_log": (edited_log, "does not validate"),
        "mapping_key": (mapping_key, "no package hash"),
        "mapping_bad": (mapping_bad, "package-hashes"),
        "schedule_identity": (schedule_identity, "identity differs"),
        "schedule_format": (schedule_format, "visit-schedule"),
        "schedule_content": (schedule_content, "schedule check fails"),
        "manifest_study": (manifest_study, "not a Study A"),
        "audio_entry": (audio_entry, "atom entry"),
    }


def sha256(data):
    from av_analysis.fileio import sha256_bytes

    return sha256_bytes(data)


@pytest.mark.parametrize("case", sorted(_fatal_cases()))
def test_fatal_reference_problems(root, visits, case):
    change, message = _fatal_cases()[case]
    vid = visits["D0"]
    change(root, vid)
    with pytest.raises(ReferenceError, match=message):
        load_references(root, vid)
    report = reconcile_visit(root, vid)
    assert codes(report, "C1") == ["REFERENCE_INPUT"] and not report.passed


def test_non_fatal_reference_problems(root, visits):
    vid = visits["V2"]
    unit = vid[:5]
    remove_synthetic_input(root, "inputs", _schedule_rel(vid[:-2] + "W4"))
    # a damaged input (the synthetic writer refuses invalid JSON, so write the file directly)
    root.input_path("inputs", f"store-snapshots/{unit}/V1.json").write_bytes(b"{not json")
    rel = f"store-snapshots/{unit}/receipts.jsonl"
    data = read_bytes(root.input_path("inputs", rel))
    write_synthetic_input(root, "inputs", rel, data + b"not json\n[1]\n\n")
    refs = load_references(root, vid)
    paths = sorted(p for p, _ in refs.problems)
    assert any(p.endswith("W4.json") for p in paths)
    assert f"inputs/store-snapshots/{unit}/V1.json" in paths
    assert sum(p.endswith("receipts.jsonl") for p in paths) == 2
    report = reconcile_visit(root, vid)
    assert "REFERENCE_INPUT" in codes(report, "C1")
    _put_json(root, f"store-snapshots/{unit}/V1.json", {"book_head": None})
    snap = _inputs_json(root, f"store-snapshots/{unit}/V2.json")
    snap["entries"][0]["extra"] = 1
    _put_json(root, f"store-snapshots/{unit}/V2.json", snap)
    remove_synthetic_input(root, "inputs", rel)
    messages = [m for _, m in load_references(root, vid).problems]
    assert any("not a verified_snapshot" in m for m in messages)
    assert any("entries must hold" in m for m in messages)
    assert "store receipts missing" in messages


def test_real_root_refuses_demo_inputs(root, visits, tmp_path):
    real = DataRoot.create(tmp_path / "real", "REAL", label="lab")
    shutil.copytree(root.area("inputs"), real.area("inputs"))
    with pytest.raises(RefusedInputError, match="DEMO input"):
        load_references(real, visits["D0"])
