"""Requirement trace: Study B visit windows and partner sessions at the operator console (DEMO only)."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from ops.console.core import Audit, Console, ConsoleFault, visit_window
from ops.console.server import demo_catalog
from ops.console.transport import DemoEngine

NOW = datetime(2030, 1, 8, 12, tzinfo=timezone.utc)
TODAY = NOW.astimezone().date()


def study_b(visit, role, anchors):
    return replace(demo_catalog()['demo-a'](), study='B', visit=visit, role=role, anchors=anchors)


def console(tmp_path, name, visits):
    c = Console({k: (lambda v=v: v) for k, v in visits.items()}, DemoEngine(mono=lambda: 0.0),
                Audit(tmp_path/f'{name}.local.jsonl', 'protocol-test'), clock=lambda: NOW)
    return c


def ready(c, alias):
    c.load(alias, 'ops-01')
    c.command('checks', 'ops-01', dict(comfort=True, phone=True))
    return c


def test_v3_window_is_v1_plus_four_and_a_later_day_than_v2_when_spaced_normally():
    """REQ-VIS-01: V1 is day 0, V2 day 2 +/- 1, V3 day 4 +/- 1 (here V2 on day 2, V3 on days 3-5)."""
    v1 = date(2030, 1, 1)
    anchors = {'V1': v1.isoformat(), 'V2': (v1+timedelta(days=2)).isoformat()}
    for day in (3, 4, 5):
        assert not visit_window('B', 'V3', anchors, v1+timedelta(days=day))['faults']
    for day in (2, 6):
        assert visit_window('B', 'V3', anchors, v1+timedelta(days=day))['faults'] == ['visit_window']
    assert visit_window('B', 'V1', {}, v1)['faults'] == []


@pytest.mark.xfail(strict=True, reason='Requirement gap: the console accepts V3 on the same calendar day as V2')
def test_v3_on_the_same_day_as_v2_is_refused():
    """REQ-VIS-01: V3 must fall on a later calendar day than V2, even when both are inside their windows."""
    v1 = date(2030, 1, 1)
    anchors = {'V1': v1.isoformat(), 'V2': (v1+timedelta(days=3)).isoformat()}
    assert visit_window('B', 'V3', anchors, v1+timedelta(days=3))['faults']


def test_follow_up_windows_follow_the_persons_own_v3_not_v1():
    """REQ-VIS-02: W1 = own V3 + 7 +/- 1 days and W4 = own V3 + 28 +/- 2 days, whatever the V1 date."""
    v1, v3 = date(2030, 1, 1), date(2030, 1, 6)
    anchors = {'V1': v1.isoformat(), 'V3': v3.isoformat()}
    for visit, offset, tolerance in (('W1', 7, 1), ('W4', 28, 2)):
        for delta in (-tolerance, 0, tolerance):
            assert not visit_window('B', visit, anchors, v3+timedelta(days=offset+delta))['faults']
        for delta in (-tolerance-1, tolerance+1):
            assert visit_window('B', visit, anchors, v3+timedelta(days=offset+delta))['faults'] == ['visit_window']
    # The V1-based date of the same offset is outside the person's W1 window.
    assert visit_window('B', 'W1', anchors, v1+timedelta(days=7))['faults'] == ['visit_window']


@pytest.mark.xfail(strict=True, reason='Requirement gap: an assigned follow-up also needs the active partner anchor and 24-h pair window')
def test_assigned_follow_up_is_anchored_to_own_v3_only():
    """REQ-VIS-02: an assigned member's W1 inside own V3 + 7 +/- 1 is admitted whatever the partner's follow-up date."""
    v3 = date(2030, 1, 5)
    day = v3+timedelta(days=8)
    now = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    partner_follow_up = now-timedelta(days=2)
    view = visit_window('B', 'W1', {'V3': v3.isoformat(), 'active': partner_follow_up.isoformat()}, day, 'yoked', now)
    assert view['faults'] == []


@pytest.mark.parametrize('elapsed,fault', [(timedelta(hours=24), False), (timedelta(hours=24, seconds=1), True)])
def test_console_flags_an_assigned_session_more_than_24_hours_after_the_active_one(tmp_path, elapsed, fault):
    """REQ-YOKE-05: the console flags (and does not start) an assigned session more than 24 h after the active one."""
    visit = study_b('V2', 'yoked', {'V1': (TODAY-timedelta(days=2)).isoformat(), 'active': (NOW-elapsed).isoformat()})
    view = ready(console(tmp_path, 'pair', {'visit': visit}), 'visit').snapshot()
    assert ('pair_window' in view['faults']) is fault and view['can_start'] is not fault


@pytest.mark.xfail(strict=True, reason='Requirement gap: a logged pair_window deviation starts an assigned session that the app refuses after 24 h')
def test_console_refuses_a_late_assigned_session_like_the_app(tmp_path):
    """REQ-YOKE-05: like the app (MENU_REPLAY_AGE), the console cannot start an assigned session more than 24 h after the active one."""
    visit = study_b('V2', 'yoked', {'V1': (TODAY-timedelta(days=2)).isoformat(),
                                    'active': (NOW-timedelta(hours=30)).isoformat()})
    c = ready(console(tmp_path, 'late', {'visit': visit}), 'visit')
    assert 'pair_window' in c.snapshot()['faults']
    c.command('deviation', 'ops-01', dict(reason='pair_window', note='Late assigned session'))
    assert not c.snapshot()['can_start']


def test_one_console_cannot_load_the_partner_visit_while_a_visit_is_running(tmp_path):
    """REQ-SB-03: on one console the partner's visit cannot be loaded until the running visit has stopped."""
    active = study_b('V1', 'active', {'V1': TODAY.isoformat()})
    assigned = study_b('V1', 'yoked', {'V1': TODAY.isoformat(), 'active': NOW.isoformat()})
    c = ready(console(tmp_path, 'station', {'active': active, 'assigned': assigned}), 'active')
    c.command('start', 'ops-01', {})
    with pytest.raises(ConsoleFault, match='visit_active'):
        c.load('assigned', 'ops-01')
    assert c.visit is active
    c.command('stop', 'ops-01', {})
    c.load('assigned', 'ops-01')
    assert c.visit is assigned


@pytest.mark.xfail(strict=True, reason='Requirement gap: the console never checks that the active partner session has ended')
def test_assigned_partner_cannot_start_while_the_active_session_is_still_running(tmp_path):
    """REQ-SB-03: partners' sessions do not overlap; the assigned session cannot start while the active one runs."""
    start = NOW-timedelta(minutes=30)
    active = study_b('V1', 'active', {'V1': TODAY.isoformat()})
    first = ready(console(tmp_path, 'active-station', {'active': active}), 'active')
    first.command('start', 'ops-01', {})
    assert first.snapshot()['state'] == 'running'
    assigned = study_b('V1', 'yoked', {'V1': TODAY.isoformat(), 'active': start.isoformat()})
    second = ready(console(tmp_path, 'assigned-station', {'assigned': assigned}), 'assigned')
    assert not second.snapshot()['can_start']
