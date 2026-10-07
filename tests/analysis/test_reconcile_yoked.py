"""C6 yoked ledger (#33 review follow-up): replayed timings, meaning-display durations
and pauses, pure reordering, and role-neutral details whichever member's ledger holds the
fault (Study B protocol sections 5.2 and 5.3; architecture section 5)."""

from __future__ import annotations

import shutil

import pytest

from av_analysis.derive import derive_tables
from av_analysis.fileio import csv_bytes, parse_csv, read_bytes
from av_analysis.loaders import raw_visit_ids
from av_analysis.paths import DataRoot, write_synthetic_input
from av_analysis.reconcile import partner_slot, reconcile_many, reconcile_visit, write_report
from av_analysis.reconcile_checks import YOKED_TIMING_TOLERANCE_MS
from av_analysis.references import load_references
from av_analysis.synthetic_logs import build_synthetic_root, refresh_exit_manifest, suite_visits

SEED = "DEMO-test-33-yoked"


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return build_synthetic_root(
        tmp_path_factory.mktemp("yoked") / "base", seed_label=SEED, study="B", units=1
    )


@pytest.fixture
def root(base, tmp_path):
    target = tmp_path / "root"
    shutil.copytree(base.path, target)
    return DataRoot.open(target)


def members(root, visit="V2"):
    """(active visit ID, yoked visit ID) of the dyad's acquisition visit."""
    active = load_references(root, suite_visits(root)[visit]).active_person_id
    return f"{active}-{visit}", f"{partner_slot(active)}-{visit}"


def event_ids(root, vid):
    header, records = parse_csv(read_bytes(root.raw_visit_dir(vid) / "exposure-ledger.csv"))
    return {dict(zip(header, r, strict=True))["event_id"] for r in records}


def edit_menu(root, vid, change):
    """Apply ``change(menu_plays)`` to the atom-menu plays of a visit's ledger."""
    header, records = parse_csv(read_bytes(root.raw_visit_dir(vid) / "exposure-ledger.csv"))
    rows = [dict(zip(header, r, strict=True)) for r in records]
    change(rows, [r for r in rows if r["stage"] == "atom_menu"])
    data = csv_bytes(header, ([r[c] for c in header] for r in rows))
    write_synthetic_input(root, "raw", f"{vid}/exposure-ledger.csv", data)
    refresh_exit_manifest(root, vid)


def c6(root, vid):
    report = reconcile_visit(root, vid)
    return [
        (d.code, d.rows, d.detail)
        for c in report.checks
        if c.check == "C6"
        for d in c.discrepancies
    ]


def shift(row, column, ms):
    row[column] = str(int(row[column]) + ms)


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (lambda r: shift(r, "display_end_mono_ms", -20_000), "meaning-display duration"),
        (lambda r: r.update(pause_ms="30000"), "pause"),
        (lambda r: r.update(pause_ms=""), "pause"),  # recorded on one side only
        (lambda r: r.update(pause_ms="x"), "pause"),  # unreadable (also C1 RAW_FORMAT)
        (lambda r: shift(r, "audio_offset_mono_ms", 500), "audio duration"),
        (
            lambda r: [shift(r, c, 150) for c in ("audio_onset_mono_ms", "audio_offset_mono_ms")],
            "timing",
        ),
    ],
    ids=["display", "pause", "pause_missing", "pause_invalid", "audio", "onset"],
)
def test_replayed_timings_must_match_the_source(root, change, field):
    active, yoked = members(root)
    edit_menu(root, yoked, lambda rows, menu: change(menu[4]))
    found = c6(root, active)
    assert [(code, detail) for code, _, detail in found] == [
        ("YOKED_MISMATCH", f"matched events differ in {field}")
    ]
    assert found == c6(root, yoked)  # identical in both members' reports


def test_timing_within_the_tolerance_is_matched(root):
    active, yoked = members(root)
    ms = YOKED_TIMING_TOLERANCE_MS // 2

    def nudge(rows, menu):
        for column in ("audio_onset_mono_ms", "audio_offset_mono_ms", "display_end_mono_ms"):
            shift(menu[1], column, ms)
        menu[2]["pause_ms"] = str(ms)

    edit_menu(root, yoked, nudge)
    assert c6(root, active) == []


def test_pure_reordering_is_a_mismatch_naming_both_members(root):
    active, yoked = members(root)

    def swap(rows, menu):
        i, j = rows.index(menu[0]), rows.index(menu[1])
        rows[i], rows[j] = rows[j], rows[i]

    edit_menu(root, yoked, swap)
    found = c6(root, active)
    assert [(code, detail) for code, _, detail in found] == [
        ("YOKED_MISMATCH", "matched selection events are in a different order")
    ]
    rows = set(found[0][1])
    assert rows & event_ids(root, active) and rows & event_ids(root, yoked)


def _neutral_texts(found):
    return {(code, detail, len(rows)) for code, rows, detail in found}


def test_details_do_not_depend_on_the_member_whose_ledger_holds_the_fault(root, tmp_path):
    active, yoked = members(root)
    # the same fault (a selection play naming a missing source) in either member's ledger
    for vid in (active, yoked):
        case = tmp_path / vid
        shutil.copytree(root.path, case)
        case_root = DataRoot.open(case)
        edit_menu(case_root, vid, lambda rows, menu: menu[3].update(yoked_source_event_id="E-x"))
        found = c6(case_root, active)
        misnamed = [f for f in found if f[0] == "YOKED_MISMATCH"]
        assert _neutral_texts(misnamed) == {
            (
                "YOKED_MISMATCH",
                "selection event names a source event inconsistently with the pair's ledgers",
                2,
            )
        }
    # a selection play without a counterpart: the copy's and the source's texts are equal
    edit_menu(root, yoked, lambda rows, menu: menu[3].update(yoked_source_event_id=""))
    found = c6(root, active)
    assert len(found) == 2
    assert _neutral_texts(found) == {
        ("YOKED_SOURCE_MISSING", "selection event has no counterpart in the pair's ledgers", 1)
    }


def test_c6_rows_and_details_reveal_no_role_through_the_exposure_ledger(root):
    """A masked reader joins discrepancy rows to exposure-cumulative's first audible
    event (which names the person): every C6 detail is raised for both members."""
    active, yoked = members(root)
    edit_menu(root, yoked, lambda rows, menu: menu[0].update(yoked_source_event_id=""))
    for report in reconcile_many(root, raw_visit_ids(root)):
        write_report(root, report)
    tables = derive_tables(root)
    owner = {
        r["first_audible_event_id"]: r["person_id"]
        for r in tables["exposure-cumulative"]
        if r["first_audible_event_id"]
    }
    by_detail: dict[str, set[str]] = {}
    for d in tables["discrepancies"]:
        if d["check"] == "C6":
            for row in d["rows"]:
                if row in owner:
                    by_detail.setdefault(d["detail"], set()).add(owner[row])
    assert by_detail and all(len(people) == 2 for people in by_detail.values())
