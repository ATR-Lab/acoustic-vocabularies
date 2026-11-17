"""Panel order schedule for any number of panels (#266, #20; Study A protocol §3.1).

Whenever the panels number a multiple of six, each of the six method orders is used
N/6 times, also when the profiles end in a partial chunk (30 batches: 10 per profile);
within a profile the order counts differ by at most 1, and panels at the same position
within their profiles get different orders. Every schedule that already met this
without the partial-chunk rule (18 panels, the 3-panel pilot, the committed DEMO
schedules, sizes not a multiple of six) is byte-identical to the schedule before it:
`_reference_group_orders` is the selection as it was before #266.
"""

import collections
import itertools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from av_generation.constants import PANEL_ORDERS
from av_generation.orchestrator import _group_orders, panel_order_schedule
from av_generation.seeds import panel_seed_key, rng_for

ORDERS = range(1, len(PANEL_ORDERS) + 1)
NAMESPACES = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,20}", fullmatch=True)


def _reference_group_orders(set_ns, sizes):
    """The selection before the partial-chunk rule (whole seeded Latin permutations)."""
    rng = rng_for(panel_seed_key(set_ns, "orders"))
    perms = list(itertools.permutations(ORDERS))
    out = {g: [] for g in sizes}
    chunks = max((-(-n // 6) for n in sizes.values()), default=0)
    for chunk in range(chunks):
        rows = []
        for group in sorted(sizes):
            if sizes[group] <= chunk * 6:
                continue
            allowed = [p for p in perms if all(p[i] != r[i] for r in rows for i in range(6))]
            pick = allowed[int(rng.integers(len(allowed)))]
            rows.append(pick)
            out[group].extend(pick)
    return {g: orders[: sizes[g]] for g, orders in out.items()}


def _panels(profile_order):
    return [(f"N{i:02d}", profile) for i, profile in enumerate(profile_order)]


def _check(schedule, panels):
    """Every order N/6 times (N a multiple of 6), per-profile counts within 1, the Latin
    rule across profiles; returns the orders per profile in panel order."""
    by_profile = collections.defaultdict(list)
    for panel_id, profile in panels:
        by_profile[profile].append(schedule[panel_id])
    counts = collections.Counter(schedule.values())
    if len(panels) % 6 == 0:
        assert counts == {o: len(panels) // 6 for o in ORDERS}
    for orders in by_profile.values():
        mine = collections.Counter(orders)
        assert max(mine[o] for o in ORDERS) - min(mine[o] for o in ORDERS) <= 1
        for start in range(0, len(orders), 6):
            chunk = orders[start : start + 6]
            assert len(set(chunk)) == len(chunk)
    for rank in range(max(len(v) for v in by_profile.values())):
        at_rank = [v[rank] for v in by_profile.values() if rank < len(v)]
        assert len(set(at_rank)) == len(at_rank)
    return by_profile


@settings(max_examples=60, deadline=None)
@given(
    NAMESPACES,
    st.sampled_from([6, 10]),
    st.randoms(use_true_random=False),
)
def test_18_and_30_panels_use_every_order_n_over_6_times(set_ns, per_profile, random):
    """18 panels (6 per profile) and 30 panels (10 per profile), any interleaving."""
    order = ["P1"] * per_profile + ["P2"] * per_profile + ["P3"] * per_profile
    random.shuffle(order)
    panels = _panels(order)
    ids = [p for p, _ in panels]
    schedule = panel_order_schedule(set_ns, ids, profiles=dict(panels))
    assert list(schedule) == ids
    by_profile = _check(schedule, panels)
    assert len(schedule) == 3 * per_profile
    for orders in by_profile.values():
        assert sorted(orders[:6]) == list(ORDERS)  # the first chunk is a permutation
    # the schedule depends on the group sizes and the namespace only, not the interleaving
    sizes = {p: per_profile for p in ("P1", "P2", "P3")}
    assert dict(by_profile) == _group_orders(set_ns, sizes)


def test_30_panels_balance_where_the_old_selection_did_not():
    """With 10 panels per profile the old selection can leave 6,6,6,6,3,3 (or similar);
    some namespace shows it, and the new schedule balances it with the first chunk kept."""
    sizes = {"P1": 10, "P2": 10, "P3": 10}
    unbalanced = []
    for i in range(40):
        set_ns = f"DEMO-SET-{i}"
        old = _reference_group_orders(set_ns, sizes)
        new = _group_orders(set_ns, sizes)
        old_counts = collections.Counter(o for v in old.values() for o in v)
        if set(old_counts.values()) != {5}:
            unbalanced.append(set_ns)
        assert collections.Counter(o for v in new.values() for o in v) == {o: 5 for o in ORDERS}
        assert {g: v[:6] for g, v in new.items()} == {g: v[:6] for g, v in old.items()}
    assert unbalanced  # the rule changes real cases


def test_existing_schedules_are_unchanged():
    """The DEMO-A-C 18-panel and DEMO-A-P pilot schedules (committed CSVs) and every case
    the old selection already balanced or that has no N/6 condition stay byte-identical."""
    for set_ns, sizes in (
        ("DEMO-A-C", {"P1": 6, "P2": 6, "P3": 6}),
        ("DEMO-A-P", {"P1": 1, "P2": 1, "P3": 1}),
        ("DEMO-DRY", {"P1": 1}),
    ):
        assert _group_orders(set_ns, sizes) == _reference_group_orders(set_ns, sizes)
    assert _group_orders("DEMO-A-C", {"P1": 6, "P2": 6, "P3": 6}) == {
        "P1": [1, 5, 2, 4, 3, 6],
        "P2": [2, 1, 3, 5, 6, 4],
        "P3": [4, 6, 5, 3, 2, 1],
    }
    assert _group_orders("DEMO-A-P", {"P1": 1, "P2": 1, "P3": 1}) == {
        "P1": [5],
        "P2": [2],
        "P3": [6],
    }
    changed = 0
    for set_ns in ("DEMO-A-C", "A-C-secret"):
        for sizes_t in itertools.product(range(0, 11), repeat=3):
            sizes = {p: n for p, n in zip(("P1", "P2", "P3"), sizes_t, strict=True) if n}
            if not sizes:
                continue
            old = _reference_group_orders(set_ns, sizes)
            new = _group_orders(set_ns, sizes)
            total = sum(sizes.values())
            old_counts = collections.Counter(o for v in old.values() for o in v)
            old_balanced = total % 6 == 0 and set(old_counts.values()) == {total // 6}
            if total % 6 or old_balanced:
                assert new == old, (set_ns, sizes)
            else:
                changed += 1
        for n in range(1, 31):  # one group (no profiles): unchanged
            assert _group_orders(set_ns, {"": n}) == _reference_group_orders(set_ns, {"": n})
    assert changed > 0


@pytest.mark.parametrize(
    "sizes",
    [
        {"P1": 8, "P2": 4, "P3": 6},  # partial rows in different chunks
        {"P1": 12, "P2": 5, "P3": 7},
        {"P1": 5, "P2": 5, "P3": 2},
        {"P1": 3, "P2": 3},
        {"P1": 11, "P3": 1},
        {"P1": 7, "P2": 5, "P3": 3},  # 15 panels: no N/6 condition, still Latin
        {"P1": 17, "P2": 1},
    ],
)
def test_uneven_profile_sizes_give_a_valid_schedule(sizes):
    panels = _panels([p for p, n in sizes.items() for _ in range(n)])
    schedule = panel_order_schedule("DEMO-SET", [p for p, _ in panels], profiles=dict(panels))
    by_profile = _check(schedule, panels)
    assert {p: len(v) for p, v in by_profile.items()} == sizes


def test_every_size_up_to_17_per_profile_balances():
    """Exhaustive over 0..17 panels per profile (the `pragma: no cover` branch)."""
    for sizes_t in itertools.product(range(0, 18), repeat=3):
        total = sum(sizes_t)
        if total == 0 or total % 6:
            continue
        sizes = {p: n for p, n in zip(("P1", "P2", "P3"), sizes_t, strict=True) if n}
        out = _group_orders("DEMO-SET", sizes)
        assert collections.Counter(o for v in out.values() for o in v) == {
            o: total // 6 for o in ORDERS
        }
        for rank in range(max(sizes.values())):
            at_rank = [v[rank] for v in out.values() if rank < len(v)]
            assert len(set(at_rank)) == len(at_rank)
