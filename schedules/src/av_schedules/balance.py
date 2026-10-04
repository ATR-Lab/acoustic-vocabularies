"""Balance report: how often each level of each counterbalanced factor occurs.

Every row compares an observed ``count`` with the ``expected`` mean count for that item
across positions (or levels) within a scope; ``deviation = count - expected``.

Metrics per scope: ``label_index`` (semantic label at matrix index, per family and
role), ``wave<w>_position`` (Study B: atom at position within the wave's menu order) and
the factor levels ``family_first``, ``swap_w1_w4``, ``profile``/``designer`` (A) and
``sq_arm`` (B). Scopes: ``all`` (main units; Study B spares excluded), ``all+spares``
(Study B confirmatory), and subsets by profile (A), SQ arm (B) and swap flag.

Message-level and order metrics, scope ``all`` only: ``message_cell`` (semantic message,
e.g. ``ADD_ONE+B``, at matrix cell ``a1-r2``), ``message_heldout`` (units in which the
semantic message is held out), ``message_novel`` (units in which it is the novel test at
a visit; swap-dependent), ``atom_position`` (Study A: matrix atom at position 1..16),
``label_position`` (Study A: semantic label at position 1..16) and
``wave<w>_label_position`` (Study B: semantic label at position within wave w).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

from .design import DESIGNERS, PROFILES, SQ_ARMS, ABatch, BDyadSlot, Unit
from .matrix import (
    A_VISITS,
    B_VISITS,
    FAMILIES,
    INDICES,
    LABELS,
    ROLES,
    atoms,
    family_cells,
    novel_visit,
    wave_atoms,
)

BALANCE_COLUMNS: Final[tuple[str, ...]] = (
    "study",
    "set",
    "metric",
    "scope",
    "family",
    "role",
    "item",
    "position",
    "count",
    "expected",
    "deviation",
    "seed_label",
)


@dataclass(frozen=True)
class BalanceRow:
    study: str
    set_name: str
    metric: str
    scope: str
    family: str
    role: str
    item: str
    position: str
    count: int
    expected: Fraction
    seed_label: str = ""

    @property
    def deviation(self) -> Fraction:
        return self.count - self.expected


def _fmt(x: Fraction) -> str:
    text = f"{float(x):.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _profile(u: Unit) -> str:
    return u.profile if isinstance(u, ABatch) else ""


def _designer(u: Unit) -> str:
    return u.designer if isinstance(u, ABatch) else ""


def _sq_arm(u: Unit) -> str:
    return u.sq_arm if isinstance(u, BDyadSlot) else ""


def _swap(u: Unit) -> str:
    return str(int(u.swap_w1_w4))


def _family_first(u: Unit) -> str:
    return u.family_first


def _scopes(study: str, units: Sequence[Unit]) -> list[tuple[str, list[Unit]]]:
    main = [u for u in units if u.kind != "spare"]
    scopes: list[tuple[str, list[Unit]]] = [("all", main)]
    if len(main) != len(units):
        scopes.append(("all+spares", list(units)))
    if study == "A":
        scopes += [(f"profile={p}", [u for u in main if _profile(u) == p]) for p in PROFILES]
    else:
        scopes += [(f"sq_arm={a}", [u for u in main if _sq_arm(u) == a]) for a in SQ_ARMS]
    scopes += [(f"swap_w1_w4={s}", [u for u in main if _swap(u) == s]) for s in ("0", "1")]
    return [(name, members) for name, members in scopes if members]


def balance_rows(units: Sequence[Unit]) -> tuple[BalanceRow, ...]:
    """All balance rows for one study and set."""
    if not units:
        return ()
    study, set_name, seed_label = units[0].study, units[0].set_name, units[0].seed_label
    rows: list[BalanceRow] = []

    def add(metric: str, scope: str, keys: tuple[str, str, str, str], n: int, e: Fraction) -> None:
        rows.append(BalanceRow(study, set_name, metric, scope, *keys, n, e, seed_label))

    factors: list[tuple[str, tuple[str, ...], Callable[[Unit], str]]] = [
        ("family_first", FAMILIES, _family_first),
        ("swap_w1_w4", ("0", "1"), _swap),
    ]
    if study == "A":
        factors += [("profile", PROFILES, _profile), ("designer", DESIGNERS, _designer)]
    else:
        factors += [("sq_arm", SQ_ARMS, _sq_arm)]

    for scope, members in _scopes(study, units):
        n = len(members)
        for f in FAMILIES:
            for r in ROLES:
                for label in LABELS[f][r]:
                    for i in INDICES:
                        count = sum(1 for u in members if u.permutation.label(f, r, i) == label)
                        add("label_index", scope, (f, r, label, str(i)), count, Fraction(n, 4))
        if study == "B":
            for w in (1, 2, 3):
                wave_set = wave_atoms(w)
                seqs = [u.wave_orders[w - 1] for u in members]
                expected = Fraction(n, len(wave_set))
                for a in wave_set:
                    for pos in range(1, len(wave_set) + 1):
                        count = sum(1 for s in seqs if s[pos - 1] == a)
                        add(f"wave{w}_position", scope, (a[0], "", a, str(pos)), count, expected)
        if scope == "all":
            _message_rows(add, study, members)
            _order_rows(add, study, members)
        for metric, levels, key in factors:
            if scope.startswith(f"{metric}="):
                continue  # degenerate: the scope fixes this factor
            for level in levels:
                count = sum(1 for u in members if key(u) == level)
                add(metric, scope, ("", "", level, ""), count, Fraction(n, len(levels)))
    return tuple(rows)


_Add = Callable[[str, str, tuple[str, str, str, str], int, Fraction], None]


def _message(u: Unit, family: str, a: int, r: int) -> str:
    return (
        f"{u.permutation.label(family, 'action', a)}+{u.permutation.label(family, 'referent', r)}"
    )


def _message_rows(add: _Add, study: str, members: Sequence[Unit]) -> None:
    """Semantic message x matrix cell, held-out counts and novel-test counts per message."""
    n = len(members)
    visits = A_VISITS if study == "A" else B_VISITS
    for f in FAMILIES:
        messages = [f"{a}+{r}" for a in LABELS[f]["action"] for r in LABELS[f]["referent"]]
        fam_cells = family_cells(f)
        cell_count = {(m, c.action_index, c.referent_index): 0 for m in messages for c in fam_cells}
        held = dict.fromkeys(messages, 0)
        novel = {(m, v): 0 for m in messages for v in visits}
        for u in members:
            for c in fam_cells:
                m = _message(u, f, c.action_index, c.referent_index)
                cell_count[(m, c.action_index, c.referent_index)] += 1
                if c.heldout_set is not None:
                    held[m] += 1
                    visit = novel_visit(u.study, c.heldout_set, u.swap_w1_w4)
                    if visit in visits:
                        novel[(m, visit)] += 1
        for (m, a, r), count in cell_count.items():
            add("message_cell", "all", (f, "", m, f"a{a}-r{r}"), count, Fraction(n, 16))
        n_held = sum(1 for c in fam_cells if c.heldout_set is not None)
        for m in messages:
            add("message_heldout", "all", (f, "", m, ""), held[m], Fraction(n * n_held, 16))
        # Messages per family tested at each visit (the same with or without the swap).
        tested = [
            novel_visit(members[0].study, c.heldout_set, False)
            for c in fam_cells
            if c.heldout_set is not None
        ]
        per_visit = {v: tested.count(v) for v in visits}
        for (m, v), count in novel.items():
            add("message_novel", "all", (f, "", m, v), count, Fraction(n * per_visit[v], 16))


def _order_rows(add: _Add, study: str, members: Sequence[Unit]) -> None:
    """Atom order by matrix atom (Study A) and by semantic label (both studies)."""
    n = len(members)
    if study == "A":
        for a in atoms():
            for pos in range(1, 17):
                count = sum(1 for u in members if u.atom_order[pos - 1] == a)
                add("atom_position", "all", (a[0], "", a, str(pos)), count, Fraction(n, 16))
        waves = [("label_position", [u.atom_order for u in members], 16)]
    else:
        waves = [
            (f"wave{w}_label_position", [u.wave_orders[w - 1] for u in members], len(wave_atoms(w)))
            for w in (1, 2, 3)
        ]
    for metric, _seqs, size in waves:
        for f in FAMILIES:
            for r in ROLES:
                for label in LABELS[f][r]:
                    for pos in range(1, size + 1):
                        count = sum(
                            1
                            for u, seq in zip(members, _seqs, strict=True)
                            if u.permutation.atom_label(seq[pos - 1]) == label
                        )
                        add(metric, "all", (f, r, label, str(pos)), count, Fraction(n, 16))


def balance_csv(rows: Sequence[BalanceRow]) -> bytes:
    """Balance report CSV bytes (UTF-8, LF), header ``BALANCE_COLUMNS``."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(BALANCE_COLUMNS)
    for r in rows:
        values = (r.study, r.set_name, r.metric, r.scope, r.family, r.role, r.item, r.position)
        w.writerow([*values, r.count, _fmt(r.expected), _fmt(r.deviation), r.seed_label])
    return buf.getvalue().encode("utf-8")


def max_abs_deviation(rows: Sequence[BalanceRow]) -> dict[str, dict[str, str]]:
    """``{metric: {scope: max |deviation|}}`` (formatted), for summaries."""
    out: dict[str, dict[str, Fraction]] = {}
    for r in rows:
        cur = out.setdefault(r.metric, {}).get(r.scope, Fraction(0))
        out[r.metric][r.scope] = max(cur, abs(r.deviation))
    return {m: {s: _fmt(v) for s, v in scopes.items()} for m, scopes in out.items()}
