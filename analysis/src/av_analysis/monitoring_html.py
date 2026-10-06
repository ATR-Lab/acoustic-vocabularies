"""Static HTML page of the integrity dashboard (#35), rendered from the dashboard-data
document only (``monitoring_metrics``).

The page is one self-contained file: inline CSS, no script, no external resource (a
``Content-Security-Policy`` meta element forbids both), light and dark colour schemes, and
the watermark (``<meta name="av-data-kind">`` and, for synthetic data, a visible
``SYNTHETIC`` banner at the top and bottom). Every number shown as a count carries a
``data-metric`` attribute naming it (for example ``faults.all.fault_n``), so tests compare
the page with the reconciled tables cell by cell. The renderer never sees a table row: it
receives the validated document, whose schema admits counts, coded IDs, enumerations and
generated text only. All text is HTML-escaped. Same document, same bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from typing import Any, Final

from .codes import CHECK_BY_ID
from .monitoring_metrics import STATION_UNRECORDED, TRIGGERS, percent
from .vocab import FAULT_RATE_TRIGGER, FAULT_TITLES, FAULT_TYPES, OVERRUN_SHARE_TRIGGER

TITLE: Final = "Integrity monitoring dashboard"
SET_TITLES: Final[dict[str, str]] = {"pilot": "pilot", "confirmatory": "confirmatory"}
UNIT_TITLES: Final[dict[str, str]] = {"batch": "batches", "dyad": "dyads"}
PERSON_TITLES: Final[dict[str, str]] = {"A": "learners", "B": "participants"}
# One-line reading hints shown under each panel title (full guide: docs/monitoring.md).
PANEL_HINTS: Final[dict[str, str]] = {
    "alerts": (
        "Red: a suspension event (wrong-file mapping, leaked answer display, changed old "
        "waveform). Stop collection on the affected visits, keep every record and write "
        "the amendment. Amber: a trigger to review."
    ),
    "enrollment": (
        "Revealed person slots against the frozen target. Stop assigning at the target; "
        "withdrawals after assignment are not refilled."
    ),
    "allocation": (
        "Per batch (Study A) or dyad slot (Study B): slots revealed and visits held. Study B "
        "cells count dyad members (of two), never which member."
    ),
    "attrition": "Expected visits by state; missed visits and withdrawals by coded ID.",
    "windows": (
        "Held visits against their windows (days after the anchor visit); exceptions list "
        "early, late and undated visits. Study B pairs: both members' sessions within 24 h."
    ),
    "faults": (
        "Accounted opportunities with an apparatus fault, pooled and by station (never per "
        "person or condition), against the 5% trigger; visits over booking by more than "
        "10 min against the 10% trigger."
    ),
    "reconciliation": (
        "Reconciliation result per visit, discrepancy codes with the first operator step, "
        "deviation records still open, and comfort and withdrawal reports."
    ),
}
_CSS: Final = """
:root{color-scheme:light dark;--bg:#ffffff;--fg:#1f2328;--muted:#5a6470;--line:#d5dbe1;
--soft:#f4f6f8;--accent:#0b5cad;--red:#b3261e;--red-soft:#fdecea;--amber:#8a5a00;
--amber-soft:#fff4d6;--green:#1a7f37;--green-soft:#e7f5ec;--banner:#5b2a86;--banner-fg:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e8eb;--muted:#9aa4b2;
--line:#2c313a;--soft:#171a21;--accent:#79b8ff;--red:#ff8a80;--red-soft:#3a1514;
--amber:#f2c14e;--amber-soft:#30250b;--green:#7ee2a8;--green-soft:#10281b;--banner:#7c4dbb}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
.banner{background:var(--banner);color:var(--banner-fg);font-weight:700;letter-spacing:.04em;
text-align:center;padding:6px 16px}
.wrap{max-width:1200px;margin:0 auto;padding:0 16px}
h1{font-size:1.6rem;margin:18px 0 4px}h2{font-size:1.2rem;margin:0 0 4px}
h3{font-size:1rem;margin:16px 0 6px}
.lede{color:var(--muted);margin:0 0 12px}
dl.meta{display:grid;grid-template-columns:max-content 1fr;gap:2px 14px;font-size:.9rem;
margin:0 0 12px}
dl.meta dt{color:var(--muted)}dl.meta dd{margin:0}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.85em;
overflow-wrap:anywhere}
nav ul{display:flex;flex-wrap:wrap;gap:4px 16px;list-style:none;padding:0;margin:0 0 4px}
nav a{color:var(--accent)}
section.panel{border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:14px 0}
.hint{color:var(--muted);font-size:.9rem;margin:0 0 10px}
.scroll{overflow-x:auto;margin:0 0 8px}
table{border-collapse:collapse;width:100%;font-size:.88rem;font-variant-numeric:tabular-nums}
table.compact{width:auto;min-width:min(100%,32rem)}
caption{text-align:left;font-weight:600;padding:2px 0 6px}
th,td{padding:4px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;
white-space:nowrap}
td.wrap-text{white-space:normal;min-width:16em}
th{background:var(--soft);font-weight:600}
.n{text-align:right}
.alert{border:2px solid;border-radius:8px;padding:10px 14px;margin:8px 0}
.alert h3{margin:0 0 6px}.alert p{margin:4px 0}
.alert.red{border-color:var(--red);background:var(--red-soft)}.alert.red h3{color:var(--red)}
.alert.amber{border-color:var(--amber);background:var(--amber-soft)}
.alert.ok{border-color:var(--green);background:var(--green-soft)}
.bad{color:var(--red);font-weight:600}.warn{color:var(--amber);font-weight:600}
.good{color:var(--green)}.muted{color:var(--muted)}
.bar{display:inline-block;width:110px;height:8px;background:var(--soft);
border:1px solid var(--line);border-radius:4px;vertical-align:middle;margin-left:8px;
overflow:hidden}
.bar>span{display:block;height:100%;background:var(--accent)}
ul.ids{columns:4 11em;padding-left:1.2em;margin:4px 0 8px}
ul.plain{padding-left:1.2em;margin:4px 0 8px}
footer{color:var(--muted);font-size:.85rem;margin:20px 0}
@media print{.banner,.alert{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
""".strip()


@dataclass(frozen=True)
class Cell:
    """One table cell: text (escaped unless ``html``), an optional metric name."""

    text: str
    metric: str | None = None
    numeric: bool = False
    css: str | None = None
    html: bool = False

    def render(self, tag: str = "td") -> str:
        classes = " ".join(c for c in ("n" if self.numeric else None, self.css) if c)
        attrs = f' class="{classes}"' if classes else ""
        if self.metric is not None:
            attrs += f' data-metric="{escape(self.metric)}"'
        body = self.text if self.html else escape(self.text)
        return f"<{tag}{attrs}>{body}</{tag}>"


def _n(value: int, metric: str | None = None) -> Cell:
    return Cell(str(value), metric, numeric=True)


def _opt_n(value: int | None, metric: str | None = None, *, missing: str = "n/a") -> Cell:
    if value is None:
        return Cell(missing, numeric=True, css="muted")
    return _n(value, metric)


def _t(value: str, css: str | None = None) -> Cell:
    return Cell(value, css=css)


def _code(value: str) -> Cell:
    return Cell(f"<code>{escape(value)}</code>", html=True)


def _span(value: int, metric: str) -> str:
    return f'<span data-metric="{escape(metric)}">{value}</span>'


def _table(
    caption: str,
    headers: Sequence[tuple[str, bool]],
    rows: Sequence[Sequence[Cell]],
    *,
    empty: str = "None.",
    compact: bool = False,
) -> str:
    """A captioned table; ``headers`` are (title, numeric); ``compact`` sizes it to its
    content instead of the panel width."""
    if not rows:
        return f'<p class="muted">{escape(caption)}: {escape(empty)}</p>'
    head = "".join(Cell(h, numeric=num).render("th") for h, num in headers)
    body = "\n".join("<tr>" + "".join(c.render() for c in row) + "</tr>" for row in rows)
    css = ' class="compact"' if compact else ""
    return (
        f'<div class="scroll"><table{css}><caption>{escape(caption)}</caption>\n'
        f"<thead><tr>{head}</tr></thead>\n<tbody>\n{body}\n</tbody></table></div>"
    )


def _ids(values: Sequence[str], *, attr: str | None = None) -> str:
    if not values:
        return '<p class="muted">None.</p>'
    extra = f' data-alert="{escape(attr)}"' if attr else ""
    items = "".join(f"<li><code{extra}>{escape(v)}</code></li>" for v in values)
    return f'<ul class="ids">{items}</ul>'


def _group_title(group: Mapping[str, Any]) -> str:
    return f"Study {group['study']} {SET_TITLES[group['set']]}"


def _gk(group: Mapping[str, Any]) -> str:
    return f"{group['study']}.{group['set']}"


def _station_key(station: str | None) -> str:
    return STATION_UNRECORDED if station is None else station


def _rate_cell(rate: float | None, exceeded: bool) -> Cell:
    return Cell(percent(rate), numeric=True, css="bad" if exceeded else None)


def _status(exceeded: bool, rate: float | None) -> Cell:
    if rate is None:
        return _t("no data", "muted")
    return _t("above trigger", "bad") if exceeded else _t("below trigger", "good")


def _bar(share: float) -> str:
    width = max(0.0, min(share, 1.0)) * 100
    return f'<span class="bar" aria-hidden="true"><span style="width:{width:.1f}%"></span></span>'


# ---------------------------------------------------------------------------------------
# Panels


def _alerts(doc: Mapping[str, Any]) -> str:
    out = []
    suspension = doc["alerts"]["suspension"]
    if not suspension:
        out.append(
            '<div class="alert ok"><h3>No suspension event</h3><p>No wrong-file mapping, '
            "leaked answer display or changed old waveform in the reconciled tables.</p></div>"
        )
    for a in suspension:
        event = a["event"]
        codes = ", ".join(f"<code>{escape(c)}</code>" for c in a["codes"]) or "none"
        out.append(
            f'<div class="alert red" id="alert-{escape(event)}" data-event="{escape(event)}">'
            f"<h3>Red alert: {escape(a['title'])} (<code>{escape(event)}</code>)</h3>"
            "<p><strong>Stop collection on the affected visits now</strong>, keep every record "
            "and write the amendment (analysis plan section 8).</p>"
            f"<p>Codes: {codes}. Discrepancies: "
            f"{_span(a['discrepancies_n'], f'alerts.{event}.discrepancies_n')}; not linked "
            f"to a deviation record: {_span(a['unresolved_n'], f'alerts.{event}.unresolved_n')}"
            f". Affected visits: {_span(len(a['visit_ids']), f'alerts.{event}.visits_n')}.</p>"
            f"{_ids(a['visit_ids'], attr=event)}</div>"
        )
    triggers = doc["alerts"]["triggers"]
    out.append("<h3>Triggers to review</h3>")
    if not triggers:
        out.append('<p class="good">No trigger reached.</p>')
    else:
        items = []
        for t in triggers:
            scope = (
                f" (Study {escape(t['study'])} {escape(SET_TITLES[t['set']])})"
                if t["study"] is not None and t["set"] is not None
                else ""
            )
            items.append(
                f'<li data-trigger="{escape(t["trigger"])}"><strong>'
                f"{escape(TRIGGERS[t['trigger']])}</strong>{scope}: {escape(t['message'])}</li>"
            )
        out.append(f'<div class="alert amber"><ul class="plain">{"".join(items)}</ul></div>')
    return "\n".join(out)


def _enrollment(doc: Mapping[str, Any]) -> str:
    progress, sources = [], []
    for e in doc["enrollment"]:
        k = f"enrollment.{_gk(e)}"
        target = e["target_persons_n"]
        share = e["revealed_persons_n"] / target if target else 0.0
        target_text = (
            f"{target} {PERSON_TITLES[e['study']]} in {e['target_units_n']} "
            f"{UNIT_TITLES[e['unit_kind']]}"
        )
        if e["target_books_n"] is not None:
            target_text += f" ({e['target_books_n']} books)"
        if e["revealed_persons_n"] > target:
            status = _t("above target", "bad")
        elif e["target_reached"]:
            status = _t("target reached: stop", "warn")
        else:
            status = _t("open")
        progress.append(
            [
                _t(_group_title(e)),
                _t(target_text),
                Cell(
                    _span(e["revealed_persons_n"], f"{k}.revealed_persons_n")
                    + f" ({percent(share)}){_bar(share)}",
                    numeric=True,
                    html=True,
                ),
                _n(e["revealed_units_n"], f"{k}.revealed_units_n"),
                _n(e["remaining_persons_n"], f"{k}.remaining_persons_n"),
                status,
            ]
        )
        sources.append(
            [
                _t(_group_title(e)),
                _n(e["planned_units_n"], f"{k}.planned_units_n"),
                _n(e["planned_persons_n"], f"{k}.planned_persons_n"),
                _n(e["eligibility_records_n"], f"{k}.eligibility_records_n"),
                _n(e["eligible_persons_n"], f"{k}.eligible_persons_n"),
                _opt_n(e["screening_cases_n"], f"{k}.screening_cases_n", missing="Pending"),
                _opt_n(e["spares_used_n"], f"{k}.spares_used_n"),
                _opt_n(e["bank_unavailable_n"], f"{k}.bank_unavailable_n"),
                _t(e["last_event_date"] or "none"),
            ]
        )
    return "\n".join(
        [
            _table(
                "Revealed person slots against the frozen targets",
                [
                    ("Study and set", False),
                    ("Frozen target", False),
                    ("Revealed persons", True),
                    ("Revealed units", True),
                    ("Remaining", True),
                    ("Status", False),
                ],
                progress,
                empty="no enrollment rows yet",
            ),
            _table(
                "Lists, eligibility and reveal log",
                [
                    ("Study and set", False),
                    ("Planned units", True),
                    ("Planned persons", True),
                    ("Eligibility records", True),
                    ("Eligible persons", True),
                    ("Screening cases", True),
                    ("Spares used", True),
                    ("Bank unavailable", True),
                    ("Last reveal", False),
                ],
                sources,
                empty="no enrollment rows yet",
            ),
        ]
    )


def _allocation(doc: Mapping[str, Any]) -> str:
    out = []
    for g in doc["allocation"]:
        units = g["units"]
        visits = [v["visit"] for v in units[0]["visits"]] if units else []
        rows = []
        for u in units:
            uid = u["unit_id"]
            k = f"allocation.{uid}"
            cells = [
                _code(uid),
                _t(u["unit_kind"]),
                Cell(
                    _span(u["revealed_persons_n"], f"{k}.revealed_persons_n")
                    + f" / {u['planned_persons_n']}",
                    numeric=True,
                    html=True,
                ),
            ]
            for v in u["visits"]:
                cells.append(
                    Cell(
                        _span(v["held_n"], f"{k}.{v['visit']}.held_n")
                        + " / "
                        + _span(v["expected_n"], f"{k}.{v['visit']}.expected_n"),
                        numeric=True,
                        html=True,
                    )
                )
            for state in ("missed", "withdrawn", "pending"):
                cells.append(_n(sum(v[f"{state}_n"] for v in u["visits"])))
            rows.append(cells)
        headers = [("Unit", False), ("Kind", False), ("Slots revealed", True)]
        headers += [(f"{v} held", True) for v in visits]
        headers += [("Missed", True), ("Withdrawn", True), ("Pending", True)]
        out.append(_table(f"{_group_title(g)}: allocation progress", headers, rows))
    return "\n".join(out) or '<p class="muted">No revealed units yet.</p>'


def _attrition(doc: Mapping[str, Any]) -> str:
    out = []
    for g in doc["attrition"]:
        k = f"attrition.{_gk(g)}"
        share = g["withdrawn_persons_n"] / g["persons_n"] if g["persons_n"] else None
        out.append(
            f"<h3>{escape(_group_title(g))}</h3><p>Persons revealed: "
            f"{_span(g['persons_n'], f'{k}.persons_n')}; withdrawn: "
            f"{_span(g['withdrawn_persons_n'], f'{k}.withdrawn_persons_n')} "
            f"({percent(share)}).</p>"
        )
        rows = [
            [_t(v["visit"])]
            + [
                _n(v[f"{s}_n"], f"{k}.{v['visit']}.{s}_n")
                for s in ("expected", "held", "missed", "withdrawn", "pending")
            ]
            for v in g["visits"]
        ]
        out.append(
            _table(
                "Visits by state",
                [
                    ("Visit", False),
                    ("Expected", True),
                    ("Held", True),
                    ("Missed", True),
                    ("Withdrawn", True),
                    ("Pending", True),
                ],
                rows,
            )
        )
        out.append("<p>Missed visits:</p>" + _ids(g["missed_visit_ids"]))
        withdrawals = [f"{w['person_id']} from {w['visit']}" for w in g["withdrawals"]]
        out.append("<p>Withdrawals (person slot, first visit not held):</p>" + _ids(withdrawals))
    return "\n".join(out) or '<p class="muted">No visits yet.</p>'


def _windows(doc: Mapping[str, Any]) -> str:
    out = []
    for g in doc["windows"]:
        k = f"windows.{_gk(g)}"
        out.append(f"<h3>{escape(_group_title(g))}</h3>")
        rows = []
        for v in g["visits"]:
            m = f"{k}.{v['visit']}"
            share = v["in_window_n"] / v["held_n"] if v["held_n"] else None
            rows.append(
                [
                    _t(v["visit"]),
                    _t(
                        f"days {v['window_lo_days']}-{v['window_hi_days']} "
                        f"after {v['anchor_visit']}"
                    ),
                    _n(v["held_n"], f"{m}.held_n"),
                    _n(v["in_window_n"], f"{m}.in_window_n"),
                    _n(v["early_n"], f"{m}.early_n"),
                    _n(v["late_n"], f"{m}.late_n"),
                    _n(v["unknown_n"], f"{m}.unknown_n"),
                    Cell(percent(share), numeric=True),
                ]
            )
        out.append(
            _table(
                "Held visits against their windows",
                [
                    ("Visit", False),
                    ("Window", False),
                    ("Held", True),
                    ("In window", True),
                    ("Early", True),
                    ("Late", True),
                    ("Undated", True),
                    ("In window %", True),
                ],
                rows,
            )
        )
        exceptions = [
            [
                _code(e["visit_id"]),
                _t(e["timing"], "bad" if e["timing"] in ("early", "late") else "warn"),
                _opt_n(e["days_since_anchor"]),
                _t(
                    "n/a"
                    if e["window_lo_days"] is None
                    else f"{e['window_lo_days']}-{e['window_hi_days']}"
                ),
            ]
            for e in g["exceptions"]
        ]
        out.append(
            _table(
                "Exceptions (early, late or undated held visits)",
                [("Visit", False), ("Timing", False), ("Day", True), ("Window", False)],
                exceptions,
                compact=True,
            )
        )
        pairs = g["pairs"]
        if pairs is not None:
            out.append(
                f"<p>Dyad pair timing (V1-V3): "
                f"{_span(pairs['checked_n'], f'{k}.pairs.checked_n')} dyad sessions checked; "
                f"{_span(pairs['outside_n'], f'{k}.pairs.outside_n')} outside 24 h.</p>"
            )
            pair_rows = [
                [
                    _code(p["unit_id"]),
                    _t(p["visit"]),
                    Cell(
                        "n/a" if p["pair_gap_hours"] is None else f"{p['pair_gap_hours']:.1f}",
                        numeric=True,
                    ),
                ]
                for p in pairs["outside"]
            ]
            out.append(
                _table(
                    "Dyad sessions outside the pair rule",
                    [("Dyad", False), ("Visit", False), ("Gap (h)", True)],
                    pair_rows,
                    compact=True,
                )
            )
    return "\n".join(out) or '<p class="muted">No visits yet.</p>'


def _fault_rows(prefix: str, label: Cell, s: Mapping[str, Any]) -> list[Cell]:
    return [
        label,
        _n(s["held_visits_n"], f"{prefix}.held_visits_n"),
        _n(s["opportunities_n"], f"{prefix}.opportunities_n"),
        _n(s["fault_n"], f"{prefix}.fault_n"),
        _rate_cell(s["fault_rate"], s["trigger_exceeded"]),
        _status(s["trigger_exceeded"], s["fault_rate"]),
    ]


def _overrun_row(prefix: str, label: Cell, s: Mapping[str, Any]) -> list[Cell]:
    return [
        label,
        _n(s["checked_n"], f"{prefix}.checked_n"),
        _n(s["overrun_n"], f"{prefix}.overrun_n"),
        _rate_cell(s["overrun_share"], s["trigger_exceeded"]),
        _status(s["trigger_exceeded"], s["overrun_share"]),
    ]


def _faults(doc: Mapping[str, Any]) -> str:
    faults, overruns = doc["faults"], doc["overruns"]
    headers = [
        ("Scope", False),
        ("Held visits", True),
        ("Opportunities", True),
        ("Faulted", True),
        ("Rate", True),
        (f"Trigger (> {percent(FAULT_RATE_TRIGGER)})", False),
    ]
    scopes: list[tuple[str, Cell, Mapping[str, Any]]] = [
        ("faults.all", _t("All visits"), faults["pooled"])
    ]
    scopes += [(f"faults.{_gk(g)}", _t(_group_title(g)), g) for g in faults["by_group"]]
    stations: list[tuple[str, Cell, Mapping[str, Any]]] = [
        (
            f"faults.station.{_station_key(s['station_id'])}",
            _code(s["station_id"]) if s["station_id"] is not None else _t("not recorded", "muted"),
            s,
        )
        for s in faults["by_station"]
    ]
    by_type_headers = [("Fault type", False)]
    by_type_headers += [(_header_text(label), True) for _, label, _ in (*scopes, *stations)]
    by_type = [
        [_t(FAULT_TITLES[t])]
        + [_n(s["by_type"][t], f"{prefix}.type.{t}") for prefix, _, s in (*scopes, *stations)]
        for t in FAULT_TYPES
    ]
    o_headers = [
        ("Scope", False),
        ("Visits checked", True),
        ("Overran", True),
        ("Share", True),
        (f"Trigger (> {percent(OVERRUN_SHARE_TRIGGER)})", False),
    ]
    o_rows = [_overrun_row("overruns.all", _t("All visits"), overruns["pooled"])]
    o_rows += [
        _overrun_row(f"overruns.{_gk(g)}", _t(_group_title(g)), g) for g in overruns["by_group"]
    ]
    o_rows += [
        _overrun_row(f"overruns.{_gk(v)}.{v['visit']}", _t(f"{_group_title(v)} {v['visit']}"), v)
        for v in overruns["by_visit"]
        if v["checked_n"]
    ]
    o_stations = [
        _overrun_row(
            f"overruns.station.{_station_key(s['station_id'])}",
            _code(s["station_id"]) if s["station_id"] is not None else _t("not recorded", "muted"),
            s,
        )
        for s in overruns["by_station"]
    ]
    note = (
        '<p class="hint">Rate = faulted / accounted opportunities (lost opportunities '
        "included). A faulted opportunity counts once per fault type, so a column of the type "
        "table can add up to more than the faulted count.</p>"
    )
    return "\n".join(
        [
            _table(
                "Apparatus faults, pooled and by study",
                headers,
                [_fault_rows(p, label, s) for p, label, s in scopes],
            ),
            _table(
                "Apparatus faults by station",
                headers,
                [_fault_rows(p, label, s) for p, label, s in stations],
                empty="no station data",
            ),
            _table("Faulted opportunities by fault type", by_type_headers, by_type),
            note,
            _table("Visit overruns (booking + 10 min)", o_headers, o_rows),
            _table(
                "Visit overruns by station",
                o_headers,
                o_stations,
                empty="no station data",
                compact=True,
            ),
        ]
    )


def _header_text(label: Cell) -> str:
    """Plain text of a scope label cell (station codes lose their markup)."""
    if not label.html:
        return label.text
    return label.text.removeprefix("<code>").removesuffix("</code>")


def _reconciliation(doc: Mapping[str, Any]) -> str:
    rec = doc["reconciliation"]
    status_rows = []
    for g in rec["by_group"]:
        k = f"reconciliation.{_gk(g)}"
        status_rows.append(
            [_t(_group_title(g))]
            + [
                _n(g[f], f"{k}.{f}")
                for f in ("visits_n", "held_n", "pass_n", "fail_n", "not_run_n", "held_not_run_n")
            ]
        )
    failing = [
        [
            _code(f["visit_id"]),
            _t(", ".join(f["checks_failed"]) or "none"),
            _n(f["discrepancies_n"]),
            _n(f["unresolved_n"]),
            _t(
                ", ".join(f["suspension_events"]) or "none",
                "bad" if f["suspension_events"] else None,
            ),
        ]
        for f in rec["failing"]
    ]
    codes = [
        [
            _code(c["code"]),
            _t(f"{c['check']} {CHECK_BY_ID[c['check']].name}"),
            _t(c["title"], "bad" if c["suspension_event"] else None),
            _n(c["discrepancies_n"], f"discrepancies.{c['code']}.discrepancies_n"),
            _n(c["unresolved_n"], f"discrepancies.{c['code']}.unresolved_n"),
            Cell(c["resolution"], css="wrap-text"),
        ]
        for c in rec["codes"]
    ]
    d = rec["deviations"]
    deviation_rows = [
        [_t(label), _n(d[field], f"deviations.{field}")]
        for label, field in (
            ("Deviation records", "deviations_n"),
            ("Open (no resolution yet)", "open_deviations_n"),
            ("Comfort and welfare records", "comfort_deviations_n"),
            ("Withdrawal records", "withdrawal_deviations_n"),
            ("Visits with a comfort adjustment or stop", "comfort_flags_n"),
        )
    ]
    open_rows = [
        [_code(v["visit_id"]), _n(v["open_deviations_n"])] for v in rec["open_deviation_visits"]
    ]
    welfare_rows = [
        [
            _code(v["visit_id"]),
            _t({True: "yes", False: "no", None: "n/a"}[v["comfort_flag"]]),
            _n(v["comfort_deviations_n"]),
            _n(v["withdrawal_deviations_n"]),
        ]
        for v in rec["welfare_visits"]
    ]
    return "\n".join(
        [
            _table(
                "Reconciliation status",
                [
                    ("Study and set", False),
                    ("Expected visits", True),
                    ("Held", True),
                    ("Passed", True),
                    ("Failed", True),
                    ("Not run", True),
                    ("Held, not reconciled", True),
                ],
                status_rows,
            ),
            _table(
                "Visits that failed reconciliation",
                [
                    ("Visit", False),
                    ("Checks failed", False),
                    ("Discrepancies", True),
                    ("Not linked", True),
                    ("Suspension", False),
                ],
                failing,
                compact=True,
            ),
            _table(
                "Discrepancies by code",
                [
                    ("Code", False),
                    ("Check", False),
                    ("Title", False),
                    ("Count", True),
                    ("Not linked", True),
                    ("First step", False),
                ],
                codes,
            ),
            _table(
                "Deviations, comfort and welfare",
                [("Records", False), ("n", True)],
                deviation_rows,
                compact=True,
            ),
            _table(
                "Visits with open deviation records",
                [("Visit", False), ("Open", True)],
                open_rows,
                compact=True,
            ),
            _table(
                "Comfort and withdrawal reports by visit",
                [
                    ("Visit", False),
                    ("Comfort adjustment or stop", False),
                    ("Comfort records", True),
                    ("Withdrawal records", True),
                ],
                welfare_rows,
                compact=True,
            ),
        ]
    )


_PANEL_BODIES: Final = {
    "alerts": _alerts,
    "enrollment": _enrollment,
    "allocation": _allocation,
    "attrition": _attrition,
    "windows": _windows,
    "faults": _faults,
    "reconciliation": _reconciliation,
}


def render_document(doc: Mapping[str, Any], panels: Sequence[tuple[str, str]]) -> str:
    """The dashboard page of a validated dashboard-data document; ``panels`` are the
    ``(id, title)`` pairs of ``monitoring.PANELS``, in page order."""
    kind = doc["data_kind"]
    synthetic = kind == "SYNTHETIC"
    as_of = doc["as_of"]
    banner = (
        '<div class="banner">SYNTHETIC demonstration data (DEMO): not study data</div>'
        if synthetic
        else ""
    )
    inputs = "".join(
        f"<li><code>{escape(i['path'])}</code> <code>{escape(i['sha256'])}</code></li>"
        for i in doc["inputs"]
    )
    nav = "".join(f'<li><a href="#{escape(pid)}">{escape(title)}</a></li>' for pid, title in panels)
    sections = []
    for pid, title in panels:
        body = _PANEL_BODIES[pid](doc)
        sections.append(
            f'<section class="panel" id="{escape(pid)}" aria-labelledby="h-{escape(pid)}">'
            f'<h2 id="h-{escape(pid)}">{escape(title)}</h2>'
            f'<p class="hint">{escape(PANEL_HINTS[pid])}</p>\n{body}\n</section>'
        )
    red = len(doc["alerts"]["suspension"])
    summary = (
        f'<p class="bad">{red} suspension event{"s" if red != 1 else ""} raised: see '
        '<a href="#alerts">alerts</a>.</p>'
        if red
        else '<p class="good">No suspension event.</p>'
    )
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        f'<meta name="av-data-kind" content="{escape(kind)}">',
        f'<meta name="generator" content="av-analysis {escape(doc["analyzer"]["version"])}">',
        f"<title>{TITLE}{' (SYNTHETIC)' if synthetic else ''}</title>",
        f"<style>\n{_CSS}\n</style>",
        "</head>",
        "<body>",
        banner,
        '<div class="wrap">',
        "<header>",
        "<h1>Integrity monitoring</h1>",
        '<p class="lede">Masked view for study staff: engineering integrity, scheduling and '
        "retention, and participant welfare (analysis plan section 8). Built from the "
        "reconciled tables through a column allowlist: no outcome, rating or condition field "
        "is read or shown, and every person appears as a coded slot ID.</p>",
        summary,
        '<dl class="meta">',
        f"<dt>Data as of</dt><dd>{escape(as_of['data_date'] or 'no data yet')}</dd>",
        f"<dt>Latest visit</dt><dd>{escape(as_of['latest_visit_date'] or 'none')}</dd>",
        f"<dt>Latest reveal</dt><dd>{escape(as_of['latest_reveal_date'] or 'none')}</dd>",
        f"<dt>Data kind</dt><dd>{escape(kind)}</dd>",
        f'<dt>Source tables</dt><dd><ul class="plain">{inputs}</ul></dd>',
        "<dt>Regenerate</dt><dd><code>av-analysis refresh --root DIR</code> after each "
        "imported visit</dd>",
        "</dl>",
        f"<nav><ul>{nav}</ul></nav>",
        "</header>",
        "<main>",
        *sections,
        "</main>",
        "<footer>Generated by av-analysis "
        f"{escape(doc['analyzer']['version'])} from reconciled/visit-status.csv, "
        "discrepancies.csv and enrollment.csv. Reading guide: analysis/docs/monitoring.md."
        "</footer>",
        "</div>",
        banner,
        "</body>",
        "</html>",
    ]
    return "\n".join(line for line in lines if line) + "\n"
