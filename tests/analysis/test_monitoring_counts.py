"""Integrity dashboard (#35): every count on the page equals the reconciled tables.

Acceptance: "Dashboard counts equal reconciliation totals on synthetic A and B datasets".
The expected numbers are computed here straight from the CSV text of the three source
tables (``csv.DictReader``, no ``av_analysis`` reader) and compared with every
``data-metric`` element of the rendered page, so a count that is missing, extra or
different fails the test.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from html.parser import HTMLParser

import pytest
from av_schedules.planning import ALLOCATION_CHECKS, PILOT_ALLOCATION_COUNTS
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from av_analysis import cli
from av_analysis.codes import suspension_event
from av_analysis.monitoring import write_dashboard
from av_analysis.monitoring_demo import write_demo_root
from av_analysis.paths import DataRoot

VISITS = {"A": ("D0", "D7"), "B": ("V1", "V2", "V3", "W1", "W4")}
ANCHORS = {"A": "D0", "B": "V1"}
FAULT_TYPES = (
    "audio_underrun",
    "missing_playback",
    "hash_mismatch",
    "failed_reset",
    "missing_response_log",
    "presentation_freeze",
    "other",
)
TARGETS = {
    ("A", "confirmatory"): ALLOCATION_CHECKS["A_assigned_learners"],
    ("B", "confirmatory"): ALLOCATION_CHECKS["B_assigned_participants"],
    ("A", "pilot"): PILOT_ALLOCATION_COUNTS["A_assigned_learners"],
    ("B", "pilot"): PILOT_ALLOCATION_COUNTS["B_assigned_participants"],
}


def _rows(root, name: str) -> list[dict[str, str]]:
    text = (root.area("reconciled") / f"{name}.csv").read_text(encoding="utf-8")
    return list(csv.DictReader(io.StringIO(text, newline="")))


class _Metrics(HTMLParser):
    """Collect ``data-metric`` elements: name -> integer text."""

    def __init__(self) -> None:
        super().__init__()
        self.metrics: dict[str, int] = {}
        self._open: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        self._open.append(dict(attrs).get("data-metric"))

    def handle_endtag(self, tag):
        self._open.pop()

    def handle_data(self, data):
        if self._open and self._open[-1] is not None:
            name = self._open[-1]
            assert name not in self.metrics, f"metric {name} rendered twice"
            self.metrics[name] = int(data)


def page_metrics(html: str) -> dict[str, int]:
    parser = _Metrics()
    parser.feed(html)
    return parser.metrics


def _n(value: str) -> int:
    return int(value) if value else 0


def _fault_block(prefix: str, rows: list[dict[str, str]], out: dict[str, int]) -> None:
    out[f"{prefix}.held_visits_n"] = sum(r["visit_state"] == "held" for r in rows)
    out[f"{prefix}.opportunities_n"] = sum(_n(r["opportunities_n"]) for r in rows)
    out[f"{prefix}.fault_n"] = sum(_n(r["fault_n"]) for r in rows)
    for t in FAULT_TYPES:
        out[f"{prefix}.type.{t}"] = sum(_n(r[f"fault_{t}_n"]) for r in rows)


def _overrun_block(prefix: str, rows: list[dict[str, str]], out: dict[str, int]) -> None:
    checked = [r for r in rows if r["overrun"] != ""]
    out[f"{prefix}.checked_n"] = len(checked)
    out[f"{prefix}.overrun_n"] = sum(r["overrun"] == "true" for r in checked)


def expected_metrics(root) -> dict[str, int]:
    """Every count the page must show, from the reconciled CSV text alone."""
    visits = _rows(root, "visit-status")
    discrepancies = _rows(root, "discrepancies")
    enrollment = _rows(root, "enrollment")
    out: dict[str, int] = {}
    groups = sorted({(r["study"], r["set"]) for r in visits + enrollment})
    for e in enrollment:
        k = f"enrollment.{e['study']}.{e['set']}"
        for field in (
            "revealed_persons_n",
            "revealed_units_n",
            "planned_units_n",
            "planned_persons_n",
            "eligibility_records_n",
            "eligible_persons_n",
            "screening_cases_n",
            "spares_used_n",
            "bank_unavailable_n",
        ):
            if e[field] != "":
                out[f"{k}.{field}"] = int(e[field])
        target = TARGETS[(e["study"], e["set"])]
        out[f"{k}.remaining_persons_n"] = max(target - int(e["revealed_persons_n"]), 0)
    for study, set_name in groups:
        rows = [r for r in visits if r["study"] == study and r["set"] == set_name]
        g = f"{study}.{set_name}"
        for unit in {r["unit_id"] for r in rows}:
            unit_rows = [r for r in rows if r["unit_id"] == unit]
            out[f"allocation.{unit}.revealed_persons_n"] = len({r["person_id"] for r in unit_rows})
            for v in VISITS[study]:
                vrows = [r for r in unit_rows if r["visit"] == v]
                out[f"allocation.{unit}.{v}.expected_n"] = len(vrows)
                out[f"allocation.{unit}.{v}.held_n"] = sum(
                    r["visit_state"] == "held" for r in vrows
                )
        out[f"attrition.{g}.persons_n"] = len({r["person_id"] for r in rows})
        out[f"attrition.{g}.withdrawn_persons_n"] = len(
            {r["person_id"] for r in rows if r["visit_state"] == "withdrawn"}
        )
        held = [r for r in rows if r["visit_state"] == "held"]
        for v in VISITS[study]:
            vrows = [r for r in rows if r["visit"] == v]
            states = Counter(r["visit_state"] for r in vrows)
            out[f"attrition.{g}.{v}.expected_n"] = len(vrows)
            for s in ("held", "missed", "withdrawn", "pending"):
                out[f"attrition.{g}.{v}.{s}_n"] = states[s]
            if v != ANCHORS[study]:
                timings = Counter(r["timing"] for r in held if r["visit"] == v)
                m = f"windows.{g}.{v}"
                out[f"{m}.held_n"] = sum(timings.values())
                out[f"{m}.in_window_n"] = timings["in_window"]
                out[f"{m}.early_n"] = timings["early"]
                out[f"{m}.late_n"] = timings["late"]
                out[f"{m}.unknown_n"] = timings["unknown"] + timings["not_applicable"]
            vchecked = [r for r in vrows if r["overrun"] != ""]
            if vchecked:
                _overrun_block(f"overruns.{g}.{v}", vrows, out)
        if study == "B":
            pairs: dict[tuple[str, str], list[str]] = {}
            for r in held:
                if r["pair_gap_ok"] != "":
                    pairs.setdefault((r["unit_id"], r["visit"]), []).append(r["pair_gap_ok"])
            out[f"windows.{g}.pairs.checked_n"] = len(pairs)
            out[f"windows.{g}.pairs.outside_n"] = sum("false" in v for v in pairs.values())
        _fault_block(f"faults.{g}", rows, out)
        _overrun_block(f"overruns.{g}", rows, out)
        rec = Counter(r["reconciliation"] for r in rows)
        k = f"reconciliation.{g}"
        out[f"{k}.visits_n"] = len(rows)
        out[f"{k}.held_n"] = len(held)
        out[f"{k}.pass_n"] = rec["pass"]
        out[f"{k}.fail_n"] = rec["fail"]
        out[f"{k}.not_run_n"] = rec["not_run"]
        out[f"{k}.held_not_run_n"] = sum(r["reconciliation"] == "not_run" for r in held)
    _fault_block("faults.all", visits, out)
    _overrun_block("overruns.all", visits, out)
    for station in {r["station_id"] for r in visits}:
        srows = [r for r in visits if r["station_id"] == station]
        key = station or "unrecorded"
        if station or any(r["visit_state"] == "held" or _n(r["opportunities_n"]) for r in srows):
            _fault_block(f"faults.station.{key}", srows, out)
            _overrun_block(f"overruns.station.{key}", srows, out)
    for code_id, n in Counter(r["code"] for r in discrepancies).items():
        out[f"discrepancies.{code_id}.discrepancies_n"] = n
        out[f"discrepancies.{code_id}.unresolved_n"] = sum(
            r["code"] == code_id and r["resolved"] == "false" for r in discrepancies
        )
    for field in (
        "deviations_n",
        "open_deviations_n",
        "comfort_deviations_n",
        "withdrawal_deviations_n",
    ):
        out[f"deviations.{field}"] = sum(int(r[field]) for r in visits)
    out["deviations.comfort_flags_n"] = sum(r["comfort_flag"] == "true" for r in visits)
    events: dict[str, list[dict[str, str]]] = {}
    for r in discrepancies:
        for event in {r["suspension_event"], suspension_event(r["code"])} - {"", None}:
            events.setdefault(event, []).append(r)
    flagged: dict[str, set[str]] = {}
    for r in visits:
        for event in filter(None, r["suspension_events"].split("|")):
            flagged.setdefault(event, set()).add(r["visit_id"])
    for event in set(events) | set(flagged):
        drows = events.get(event, [])
        out[f"alerts.{event}.discrepancies_n"] = len(drows)
        out[f"alerts.{event}.unresolved_n"] = sum(r["resolved"] == "false" for r in drows)
        ids = {r["visit_id"] for r in drows} | flagged.get(event, set())
        out[f"alerts.{event}.visits_n"] = len(ids)
    return out


def _check(root) -> dict:
    write_dashboard(root)
    html = (root.area("monitoring") / "index.html").read_text(encoding="utf-8")
    expected = expected_metrics(root)
    shown = page_metrics(html)
    assert sorted(set(shown) ^ set(expected)) == []
    assert {k: v for k, v in shown.items() if expected[k] != v} == {}
    return json.loads((root.area("monitoring") / "dashboard.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("study", ["A", "B"])
def test_full_size_counts_equal_the_reconciled_tables(tmp_path, study):
    root = write_demo_root(
        tmp_path / study,
        f"DEMO-counts-{study}",
        studies=(study,),
        sets=("confirmatory",),
        inject=[("wrong_hash", None), ("answer_leak", None)],
    )
    doc = _check(root)
    persons = {"A": 216, "B": 128}[study]
    (enrollment,) = doc["enrollment"]
    assert enrollment["target_persons_n"] == enrollment["revealed_persons_n"] == persons
    assert enrollment["target_reached"] is True
    visits = _rows(root, "visit-status")
    assert len(visits) == persons * len(VISITS[study])
    pooled = doc["faults"]["pooled"]
    assert pooled["fault_rate"] == pooled["fault_n"] / pooled["opportunities_n"]
    assert pooled["opportunities_n"] == sum(int(r["opportunities_n"]) for r in visits)


def test_counts_equal_the_tables_for_both_studies_and_sets_in_progress(tmp_path):
    root = write_demo_root(
        tmp_path / "both",
        "DEMO-counts-both",
        sets=("pilot", "confirmatory"),
        progress=0.6,
        inject=[("wrong_hash", None), ("changed_old_atom", None), ("answer_leak", None)],
    )
    doc = _check(root)
    assert [(g["study"], g["set"]) for g in doc["groups"]] == [
        ("A", "pilot"),
        ("A", "confirmatory"),
        ("B", "pilot"),
        ("B", "confirmatory"),
    ]
    assert {e["event"] for e in doc["alerts"]["suspension"]} == {
        "WRONG_FILE_MAPPING",
        "ANSWER_LEAK",
        "OLD_WAVEFORM_CHANGED",
    }
    pending = sum(r["visit_state"] == "pending" for r in _rows(root, "visit-status"))
    assert pending > 0


@settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    seed=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8),
    studies=st.sampled_from([("A",), ("B",), ("A", "B")]),
    progress=st.floats(min_value=0.05, max_value=1.0),
    inject=st.lists(st.sampled_from(["wrong_hash", "answer_leak"]), max_size=2, unique=True),
)
def test_property_page_counts_equal_table_sums(tmp_path_factory, seed, studies, progress, inject):
    root = write_demo_root(
        tmp_path_factory.mktemp("counts") / "root",
        f"DEMO-{seed}",
        studies=studies,
        sets=("pilot",),
        progress=progress,
        inject=[(name, None) for name in inject],
    )
    doc = _check(root)
    by_station = sum(s["opportunities_n"] for s in doc["faults"]["by_station"])
    assert by_station == doc["faults"]["pooled"]["opportunities_n"]
    by_group = sum(g["fault_n"] for g in doc["faults"]["by_group"])
    assert by_group == doc["faults"]["pooled"]["fault_n"]


def test_metric_parser_rejects_duplicates():
    with pytest.raises(AssertionError, match="twice"):
        page_metrics('<p><span data-metric="x">1</span><span data-metric="x">2</span></p>')


def test_expected_metrics_helper_reads_only_csv_text(tmp_path):
    root = write_demo_root(tmp_path / "r", "DEMO-helper", studies=("A",), sets=("pilot",))
    assert expected_metrics(root)["attrition.A.pilot.persons_n"] == 18


def _chain_root(tmp_path, name, *extra):
    """A #33 synthetic root reconciled and derived by ``refresh`` (skips until #33 lands)."""
    out = tmp_path / name
    code = cli.main(["synth-logs", "--demo-seed", "DEMO-chain-35", "--out", str(out), *extra])
    if code == 3:
        pytest.skip("#33 synth-logs is not implemented on this branch yet")
    assert code == 0
    refresh = cli.main(["refresh", "--root", str(out)])
    if not (out / "reconciled" / "visit-status.csv").is_file():
        pytest.skip("#33 reconcile/derive are not implemented on this branch yet")
    assert refresh in (0, 1)
    return DataRoot.open(out)


def test_dashboard_on_the_reconciliation_chain_equals_its_tables(tmp_path):
    """After the restack on #33: synth-logs -> refresh (reconcile, derive, dashboard)."""
    root = _chain_root(tmp_path, "clean")
    html = (root.area("monitoring") / "index.html").read_text(encoding="utf-8")
    assert page_metrics(html) == expected_metrics(root)


@pytest.mark.parametrize(
    ("fault", "event", "prefix", "suffix"),
    [
        ("wrong_hash", "WRONG_FILE_MAPPING", "A-", "-D0"),
        ("changed_old_atom", "OLD_WAVEFORM_CHANGED", "B-", "-V2"),
    ],
)
def test_injected_faults_on_the_reconciliation_chain_raise_red_alerts(
    tmp_path, fault, event, prefix, suffix
):
    clean = _chain_root(tmp_path, "clean")
    visits = sorted(
        p.name
        for p in clean.area("raw").iterdir()
        if p.is_dir() and p.name.startswith(prefix) and p.name.endswith(suffix)
    )
    assert visits
    root = _chain_root(tmp_path, fault, "--fault", fault, "--visit", visits[0])
    doc = json.loads((root.area("monitoring") / "dashboard.json").read_text(encoding="utf-8"))
    alerts = {a["event"]: a["visit_ids"] for a in doc["alerts"]["suspension"]}
    assert visits[0] in alerts[event]


def test_held_visits_without_a_station_are_counted_as_not_recorded(tmp_path):
    root = write_demo_root(tmp_path / "r", "DEMO-station", studies=("A",), sets=("pilot",))
    path = root.area("reconciled") / "visit-status.csv"
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))
    column = rows[0].index("station_id")
    for row in rows[1:4]:
        row[column] = ""
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator="\n").writerows(rows)
    path.write_bytes(buf.getvalue().encode("utf-8"))
    doc = _check(root)
    assert doc["faults"]["by_station"][-1]["station_id"] is None
    assert "faults.station.unrecorded.fault_n" in expected_metrics(root)
