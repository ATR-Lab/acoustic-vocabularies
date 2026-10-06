"""Integrity dashboard (#35): red alerts for the three suspension events, amber triggers.

Acceptance: "Injected wrong-file mapping and changed-old-hash events produce red alerts"
(with the affected visit IDs).
"""

from __future__ import annotations

import re

import pytest

from av_analysis import cli
from av_analysis.codes import SUSPENSION_EVENTS, SUSPENSION_TITLES
from av_analysis.monitoring import (
    MonitoringData,
    allowlist,
    dashboard_document,
    load_monitoring_data,
    render,
)
from av_analysis.monitoring_demo import INJECTIONS, demo_tables, write_demo_root
from av_analysis.vocab import FAULT_RATE_TRIGGER, FAULT_TYPES, OVERRUN_SHARE_TRIGGER


def _red_alerts(html: str) -> dict[str, list[str]]:
    """Event -> visit IDs listed inside its red alert box."""
    out: dict[str, list[str]] = {}
    for event, body in re.findall(
        r'<div class="alert red" id="alert-([A-Z_]+)"[^>]*>(.*?)</div>', html, re.S
    ):
        out[event] = re.findall(rf'<code data-alert="{event}">([^<]+)</code>', body)
    return out


def _root(tmp_path, inject, **kwargs):
    kwargs.setdefault("sets", ("pilot",))
    return write_demo_root(tmp_path / "root", "DEMO-alerts", inject=inject, **kwargs)


def test_injected_wrong_file_mapping_and_changed_old_hash_produce_red_alerts(tmp_path, capsys):
    """Acceptance: both injected events appear as red alerts with the affected visit IDs."""
    root = _root(
        tmp_path,
        [("wrong_hash", "A-P02-L04-D7"), ("changed_old_atom", "B-P03-M2-V3")],
        progress=1.0,
    )
    assert cli.main(["dashboard", "--root", str(root.path)]) == 1  # red alerts present
    out = capsys.readouterr().out
    assert "RED ALERT WRONG_FILE_MAPPING (Wrong-file mapping): A-P02-L04-D7" in out
    assert "RED ALERT OLD_WAVEFORM_CHANGED (Changed old waveform): B-P03-M2-V3" in out
    html = (root.area("monitoring") / "index.html").read_text(encoding="utf-8")
    assert _red_alerts(html) == {
        "WRONG_FILE_MAPPING": ["A-P02-L04-D7"],
        "OLD_WAVEFORM_CHANGED": ["B-P03-M2-V3"],
    }
    assert "2 suspension events raised" in html


def test_all_three_events_with_default_targets(tmp_path):
    root = _root(tmp_path, [(name, None) for name in INJECTIONS])
    doc = dashboard_document(load_monitoring_data(root))
    alerts = {a["event"]: a for a in doc["alerts"]["suspension"]}
    assert list(alerts) == list(SUSPENSION_TITLES)  # page order: the codes module's order
    for event, alert in alerts.items():
        assert alert["title"] == SUSPENSION_TITLES[event]
        assert set(alert["codes"]) <= set(SUSPENSION_EVENTS[event])
        assert alert["discrepancies_n"] == alert["unresolved_n"] == 1
        assert len(alert["visit_ids"]) == 1
    assert alerts["OLD_WAVEFORM_CHANGED"]["visit_ids"][0].startswith("B-")
    html = render(load_monitoring_data(root))
    assert set(_red_alerts(html)) == set(SUSPENSION_TITLES)


def test_no_injection_no_red_alert(tmp_path, capsys):
    root = _root(tmp_path, [])
    assert cli.main(["dashboard", "--root", str(root.path)]) == 0
    assert "RED ALERT" not in capsys.readouterr().out
    html = (root.area("monitoring") / "index.html").read_text(encoding="utf-8")
    assert 'class="alert red"' not in html
    assert "No suspension event" in html


def _with_rows(data: MonitoringData, table: str, rows) -> MonitoringData:
    return MonitoringData(data.data_kind, {**data.tables, table: tuple(rows)}, data.inputs)


def test_a_suspension_event_on_visit_status_alone_still_raises_a_red_alert(tmp_path):
    data = load_monitoring_data(_root(tmp_path, []))
    target = data.tables["visit-status"][5]["visit_id"]
    rows = [
        {**r, "suspension_events": ("ANSWER_LEAK",)} if r["visit_id"] == target else r
        for r in data.tables["visit-status"]
    ]
    doc = dashboard_document(_with_rows(data, "visit-status", rows))
    (alert,) = doc["alerts"]["suspension"]
    assert alert["event"] == "ANSWER_LEAK"
    assert alert["visit_ids"] == [target]
    assert alert["discrepancies_n"] == 0


def test_a_suspension_code_without_its_event_column_still_raises_a_red_alert(tmp_path):
    data = load_monitoring_data(_root(tmp_path, [("wrong_hash", None)]))
    rows = [
        {**r, "suspension_event": None} if r["code"] == "WAVEFORM_HASH_MISMATCH" else r
        for r in data.tables["discrepancies"]
    ]
    vs = [{**r, "suspension_events": ()} for r in data.tables["visit-status"]]
    doc = dashboard_document(
        _with_rows(_with_rows(data, "discrepancies", rows), "visit-status", vs)
    )
    (alert,) = doc["alerts"]["suspension"]
    assert alert["event"] == "WRONG_FILE_MAPPING"
    assert alert["codes"] == ["WAVEFORM_HASH_MISMATCH"]


def test_a_deviation_linked_suspension_event_stays_red(tmp_path):
    data = load_monitoring_data(_root(tmp_path, [("answer_leak", None)]))
    rows = [
        {**r, "resolved": True} if r["code"] == "ANSWER_DISPLAY_LEAK" else r
        for r in data.tables["discrepancies"]
    ]
    doc = dashboard_document(_with_rows(data, "discrepancies", rows))
    (alert,) = doc["alerts"]["suspension"]
    assert (alert["discrepancies_n"], alert["unresolved_n"]) == (1, 0)
    assert "Red alert: Leaked answer display" in render(_with_rows(data, "discrepancies", rows))


def _triggers(doc) -> set[tuple[str, str | None, str | None]]:
    return {(t["trigger"], t["study"], t["set"]) for t in doc["alerts"]["triggers"]}


def _demo_data(studies) -> MonitoringData:
    """Allow-listed in-memory demo rows of the pilot set (full size)."""
    tables = demo_tables("DEMO-triggers", studies=studies, sets=("pilot",), progress=1.0)
    return MonitoringData(
        "SYNTHETIC",
        {
            name: tuple({k: v for k, v in r.items() if k in keep} for r in rows)
            for name, rows in tables.items()
            for keep in [allowlist()[name]]
        },
        {},
    )


def test_amber_triggers_follow_the_protocol_thresholds(tmp_path):
    data = _demo_data(("A", "B"))
    base = _triggers(dashboard_document(data))
    assert ("target_reached", "A", "pilot") in base and ("target_reached", "B", "pilot") in base
    visits = list(data.tables["visit-status"])

    def edit(row_filter, **changes):
        return [{**r, **changes} if row_filter(r) else r for r in visits]

    # More than 5% of accounted opportunities faulted in Study A pilot.
    faulty = edit(lambda r: r["study"] == "A" and r["visit_state"] == "held", fault_n=10)
    found = _triggers(dashboard_document(_with_rows(data, "visit-status", faulty)))
    assert ("fault_rate", "A", "pilot") in found and ("fault_rate", "B", "pilot") not in found
    # More than 10% of visits overrunning in Study B pilot.
    late = edit(lambda r: r["study"] == "B" and r["overrun"] is not None, overrun=True)
    assert ("overrun_share", "B", "pilot") in _triggers(
        dashboard_document(_with_rows(data, "visit-status", late))
    )
    # A held visit without a reconciliation result; a dyad session outside 24 h.
    held = [r for r in visits if r["study"] == "B" and r["visit_state"] == "held"]
    first, pair = held[0]["visit_id"], held[0]["unit_id"]
    rows = edit(lambda r: r["visit_id"] == first, reconciliation="not_run")
    rows = [
        {**r, "pair_gap_ok": False, "pair_gap_hours": 30.5}
        if r["unit_id"] == pair and r["visit"] == "V1"
        else r
        for r in rows
    ]
    doc = dashboard_document(_with_rows(data, "visit-status", rows))
    assert {("not_reconciled", "B", "pilot"), ("pair_gap", "B", "pilot")} <= _triggers(doc)
    (pairs,) = [w["pairs"] for w in doc["windows"] if w["study"] == "B"]
    assert {"unit_id": pair, "visit": "V1", "pair_gap_hours": 30.5} in pairs["outside"]
    # Enrollment beyond the frozen target, and a table mismatch.
    enrollment = [
        {**e, "revealed_persons_n": 19} if e["study"] == "A" else e
        for e in data.tables["enrollment"]
    ]
    found = _triggers(dashboard_document(_with_rows(data, "enrollment", enrollment)))
    assert "above target" in render(_with_rows(data, "enrollment", enrollment))
    assert ("target_exceeded", "A", "pilot") in found
    assert ("enrollment_mismatch", "A", "pilot") in found
    assert ("target_reached", "A", "pilot") not in found


# Analysis plan section 8: "more than" 5% of opportunities faulted and "more than" 10% of
# visits overrunning; exactly at the threshold is not above it.
QUIET = {
    "opportunities_n": 0,
    "fault_n": 0,
    "overrun": None,
    **{f"fault_{t}_n": 0 for t in FAULT_TYPES},
}


def _quiet_held_rows(data: MonitoringData) -> tuple[list[dict], list[int]]:
    """Study A pilot rows without faults or overrun checks, and the held row indexes."""
    rows = [{**r, **QUIET} for r in data.tables["visit-status"]]
    return rows, [i for i, r in enumerate(rows) if r["visit_state"] == "held"]


@pytest.mark.parametrize(("faulted", "raised"), [(5, False), (6, True)])
def test_fault_rate_trigger_is_strictly_more_than_5_percent(faulted, raised):
    data = _demo_data(("A",))
    rows, held = _quiet_held_rows(data)
    rows[held[0]].update(opportunities_n=100, fault_n=faulted, fault_audio_underrun_n=faulted)
    edited = _with_rows(data, "visit-status", rows)
    doc = dashboard_document(edited)
    assert (("fault_rate", "A", "pilot") in _triggers(doc)) is raised
    (group,) = doc["faults"]["by_group"]
    assert group["fault_rate"] == faulted / 100
    assert (group["fault_rate"] == FAULT_RATE_TRIGGER) is not raised  # 5/100: at the trigger
    summaries = [doc["faults"]["pooled"], group, *doc["faults"]["by_station"]]
    assert [s["trigger_exceeded"] for s in summaries if s["fault_n"]] == [raised] * 3
    html = render(edited)
    assert ("above trigger" in html) is raised  # overruns are unchecked: "no data"
    assert html.count("below trigger") == (0 if raised else 3)


@pytest.mark.parametrize(("overran", "raised"), [(1, False), (2, True)])
def test_overrun_share_trigger_is_strictly_more_than_10_percent(overran, raised):
    data = _demo_data(("A",))
    rows, held = _quiet_held_rows(data)
    for j, i in enumerate(held[:10]):
        rows[i]["overrun"] = j < overran
    edited = _with_rows(data, "visit-status", rows)
    doc = dashboard_document(edited)
    assert (("overrun_share", "A", "pilot") in _triggers(doc)) is raised
    (group,) = doc["overruns"]["by_group"]
    assert (group["checked_n"], group["overrun_n"]) == (10, overran)
    assert group["overrun_share"] == overran / 10
    assert (group["overrun_share"] == OVERRUN_SHARE_TRIGGER) is not raised  # 1/10: at it
    assert group["trigger_exceeded"] is raised
    assert doc["overruns"]["pooled"]["trigger_exceeded"] is raised


@pytest.mark.parametrize(
    ("inject", "message"),
    [
        ([("unknown", None)], "unknown injection"),
        ([("wrong_hash", "A-P01-L01-D9")], "invalid visit ID"),
        ([("wrong_hash", "A-C01-L01-D0")], "not a held visit"),
        ([("changed_old_atom", None)], "needs Study B"),
    ],
)
def test_demo_injection_errors(inject, message):
    with pytest.raises(ValueError, match=message):
        demo_tables("DEMO-bad", studies=("A",), sets=("pilot",), inject=inject)
