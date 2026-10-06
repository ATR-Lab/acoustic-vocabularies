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


def test_amber_triggers_follow_the_protocol_thresholds(tmp_path):
    tables = demo_tables("DEMO-triggers", studies=("A", "B"), sets=("pilot",), progress=1.0)
    data = MonitoringData(
        "SYNTHETIC",
        {
            name: tuple({k: v for k, v in r.items() if k in keep} for r in rows)
            for name, rows in tables.items()
            for keep in [allowlist()[name]]
        },
        {},
    )
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
