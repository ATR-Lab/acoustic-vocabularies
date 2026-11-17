"""Integrity dashboard (#35): enrollment targets follow the sample-size presets (#266)."""

from __future__ import annotations

from av_schedules.planning import allocation_counts
from av_schedules.presets import DEFAULT_PRESET, PRESET_NAMES, get_preset

from av_analysis.monitoring import load_monitoring_data
from av_analysis.monitoring_demo import write_demo_root
from av_analysis.monitoring_metrics import TARGETS, build_document, targets

PERSONS = {"A": "A_assigned_learners", "B": "B_assigned_participants"}


def test_default_targets_are_unchanged():
    assert targets() == TARGETS == targets(DEFAULT_PRESET)
    a, b = TARGETS[("A", "confirmatory")], TARGETS[("B", "confirmatory")]
    assert (a.units_n, a.books_n, a.persons_n) == (18, 54, 216)
    assert (b.units_n, b.persons_n) == (64, 128)


def test_targets_follow_the_preset():
    for name in PRESET_NAMES:
        goals, counts = targets(name), get_preset(name).counts()
        a, b = goals[("A", "confirmatory")], goals[("B", "confirmatory")]
        assert (a.units_n, a.books_n, a.persons_n) == (
            counts["batches"],
            counts["books"],
            counts["learners"],
        )
        assert (b.units_n, b.persons_n) == (counts["pairs"], counts["people"])
        for (study, set_name), target in goals.items():
            assert target.persons_n == allocation_counts(set_name, name)[PERSONS[study]]
            if set_name == "pilot":  # pilot sizes are the same in every preset
                assert target == TARGETS[(study, set_name)]


def test_dashboard_document_uses_the_preset_targets(tmp_path):
    root = write_demo_root(tmp_path / "root", "DEMO-monitoring-presets", progress=0.2)
    data = load_monitoring_data(root)
    default = build_document(data.tables, data.data_kind, {}, version="0")
    revised = build_document(data.tables, data.data_kind, {}, version="0", preset="revised-30-88")
    targets_of = {
        name: {
            (e["study"], e["set"]): (e["target_units_n"], e["target_persons_n"])
            for e in d["enrollment"]
        }
        for name, d in (("default", default), ("revised", revised))
    }
    assert targets_of["default"] == {
        ("A", "confirmatory"): (18, 216),
        ("B", "confirmatory"): (64, 128),
    }
    assert targets_of["revised"] == {
        ("A", "confirmatory"): (30, 360),
        ("B", "confirmatory"): (88, 176),
    }
    revealed = {(e["study"], e["set"]): e["revealed_persons_n"] for e in revised["enrollment"]}
    for e in revised["enrollment"]:
        key = (e["study"], e["set"])
        assert e["remaining_persons_n"] == targets_of["revised"][key][1] - revealed[key]
