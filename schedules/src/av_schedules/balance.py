"""Balance report: how often each level of each counterbalanced factor occurs.

Every row compares an observed ``count`` with the ``expected`` mean count for that item
across positions (or levels) within a scope; ``deviation = count - expected``.

Metrics: ``label_index`` (semantic label at matrix index, per family and role),
``atom_position`` (Study A: position 1..16 in the atom order), ``wave<w>_position``
(Study B: position within the wave's menu order), and the factor levels
``family_first``, ``swap_w1_w4``, ``profile``/``designer`` (A) and ``sq_arm`` (B).
Scopes: ``all`` (main units; Study B spares excluded), ``all+spares`` (Study B
confirmatory), and subsets by profile (A), SQ arm (B) and swap flag. Study A atom
positions are reported for ``all`` only.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

from .design import DESIGNERS, PROFILES, SQ_ARMS, ABatch, BDyadSlot, Unit
from .matrix import FAMILIES, INDICES, LABELS, ROLES, atoms, wave_atoms

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
    study, set_name = units[0].study, units[0].set_name
    rows: list[BalanceRow] = []

    def add(metric: str, scope: str, keys: tuple[str, str, str, str], n: int, e: Fraction) -> None:
        rows.append(BalanceRow(study, set_name, metric, scope, *keys, n, e))

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
        if study == "A":
            # Study A atom orders are balanced over the whole set, not within subsets.
            seqs_a = [u.atom_order for u in members] if scope == "all" else []
            orders = [("atom_position", atoms(), seqs_a)] if seqs_a else []
        else:
            orders = [
                (f"wave{w}_position", wave_atoms(w), [u.wave_orders[w - 1] for u in members])
                for w in (1, 2, 3)
            ]
        for metric, wave_set, seqs in orders:
            expected = Fraction(n, len(wave_set))
            for a in wave_set:
                for pos in range(1, len(wave_set) + 1):
                    count = sum(1 for s in seqs if s[pos - 1] == a)
                    add(metric, scope, (a[0], "", a, str(pos)), count, expected)
        for metric, levels, key in factors:
            if scope.startswith(f"{metric}="):
                continue  # degenerate: the scope fixes this factor
            for level in levels:
                count = sum(1 for u in members if key(u) == level)
                add(metric, scope, ("", "", level, ""), count, Fraction(n, len(levels)))
    return tuple(rows)


def balance_csv(rows: Sequence[BalanceRow]) -> bytes:
    """Balance report CSV bytes (UTF-8, LF), header ``BALANCE_COLUMNS``."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(BALANCE_COLUMNS)
    for r in rows:
        values = (r.study, r.set_name, r.metric, r.scope, r.family, r.role, r.item, r.position)
        w.writerow([*values, r.count, _fmt(r.expected), _fmt(r.deviation)])
    return buf.getvalue().encode("utf-8")


def max_abs_deviation(rows: Sequence[BalanceRow]) -> dict[str, dict[str, str]]:
    """``{metric: {scope: max |deviation|}}`` (formatted), for summaries."""
    out: dict[str, dict[str, Fraction]] = {}
    for r in rows:
        cur = out.setdefault(r.metric, {}).get(r.scope, Fraction(0))
        out[r.metric][r.scope] = max(cur, abs(r.deviation))
    return {m: {s: _fmt(v) for s, v in scopes.items()} for m, scopes in out.items()}
