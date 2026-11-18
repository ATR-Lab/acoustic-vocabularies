"""Study B acquisition pair rule at the operator console (DEMO visits only)."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from ops.console.core import ACQUISITION_VISITS, Audit, Console, ConsoleFault, visit_window
from ops.console.server import demo_catalog
from ops.console.transport import DemoEngine

NOW = datetime(2030, 1, 8, 12, tzinfo=timezone.utc)
TODAY = NOW.astimezone().date()
V1_DAYS_AGO = {'V1': 0, 'V2': 2, 'V3': 4}  # Centre of each calendar window.


def anchors(visit, active_age, ended_age=timedelta(minutes=10)):
    """Yoked anchors: the active partner started `active_age` and closed `ended_age` before NOW."""
    values = {'V1': (TODAY-timedelta(days=V1_DAYS_AGO[visit])).isoformat(), 'active': (NOW-active_age).isoformat()}
    if visit == 'V3':
        values['V2'] = (TODAY-timedelta(days=1)).isoformat()  # Held V2 on an earlier day.
    if ended_age is not None:
        values['active_end'] = dict(session_id='S-ACTIVE-01', ended_utc=(NOW-ended_age).isoformat())
    return values


def ready(c):
    c.load('visit', 'ops-01')
    c.command('checks', 'ops-01', dict(comfort=True, phone=True))
    return c


def station(tmp_path, visit, values, role='yoked', clock=lambda: NOW):
    """One console; reloading picks up later changes to `values`, as a re-imported config would."""
    def load():
        return replace(demo_catalog()['demo-a'](), study='B', visit=visit, role=role, anchors=dict(values))
    return ready(Console({'visit': load}, DemoEngine(mono=lambda: 0.0),
                         Audit(tmp_path/f'{visit}-{role}.local.jsonl', 'protocol-test'), clock=clock))


@pytest.mark.parametrize('visit', ACQUISITION_VISITS)
@pytest.mark.parametrize('age,admitted', [(timedelta(hours=23, minutes=59), True), (timedelta(hours=24), True),
                                          (timedelta(hours=24, minutes=1), False), (timedelta(hours=30), False)])
def test_acquisition_pair_window_edges_match_the_app(tmp_path, visit, age, admitted):
    """REQ-YOKE-05, REQ-YOKE-01: a yoked V1-V3 session starts at most 24 h after the active start, as in the app."""
    view = station(tmp_path, visit, anchors(visit, age)).snapshot()
    assert ('pair_window' in view['faults']) is not admitted and view['can_start'] is admitted


@pytest.mark.parametrize('visit', ACQUISITION_VISITS)
@pytest.mark.parametrize('age', [timedelta(hours=24, minutes=1), timedelta(hours=30)])
def test_pair_window_deviation_is_recorded_but_never_starts_a_late_acquisition_visit(tmp_path, visit, age):
    """REQ-YOKE-05: like the app's MENU_REPLAY_AGE refusal, a logged pair_window deviation does not enable start."""
    c = station(tmp_path, visit, anchors(visit, age))
    view = c.command('deviation', 'ops-01', dict(reason='pair_window', note='Late assigned session'))
    assert [d['reason'] for d in view['deviations']] == ['pair_window']
    assert 'pair_window' in view['faults'] and not view['can_start']
    with pytest.raises(ConsoleFault, match='start_blocked'):
        c.command('start', 'ops-01', {})
    assert c.engine.state == 'awaiting_operator'
    assert [r['event'] for r in c.audit.rows if r['event'] in ('deviation', 'start_requested')] == ['deviation']


@pytest.mark.parametrize('visit', ACQUISITION_VISITS)
def test_yoked_start_waits_for_the_imported_active_close(tmp_path, visit):
    """REQ-SB-03, REQ-YOKE-01: without a record that the active session ended, the yoked session cannot start."""
    c = station(tmp_path, visit, anchors(visit, timedelta(minutes=30), ended_age=None))
    for reason in ('pair_window', 'procedure'):
        c.command('deviation', 'ops-01', dict(reason=reason, note='Partner session status unknown'))
    view = c.snapshot()
    assert view['faults'] == ['active_end_missing'] and not view['can_start']


def test_active_close_after_now_refuses_until_it_has_passed(tmp_path):
    """REQ-SB-03: a recorded close later than the station clock means the active session may still be running."""
    clock = [NOW]
    c = station(tmp_path, 'V2', anchors('V2', timedelta(minutes=30), ended_age=-timedelta(minutes=5)),
                clock=lambda: clock[0])
    view = c.snapshot()
    assert view['faults'] == ['active_end_future'] and not view['can_start']
    clock[0] = NOW+timedelta(minutes=5)
    c.command('start', 'ops-01', {})
    assert c.snapshot()['state'] == 'running'


def test_partner_sessions_do_not_overlap_across_stations(tmp_path):
    """REQ-SB-03: the yoked station cannot start while the active partner's session runs on another station."""
    clock = [NOW]
    active = station(tmp_path, 'V1', {}, role='active', clock=lambda: clock[0])
    active.command('start', 'ops-01', {})
    values = {'active': NOW.isoformat()}
    yoked = station(tmp_path, 'V1', values, clock=lambda: clock[0])
    clock[0] = NOW+timedelta(minutes=40)
    assert active.snapshot()['state'] == 'running'
    assert yoked.snapshot()['faults'] == ['active_end_missing'] and not yoked.snapshot()['can_start']
    active.command('stop', 'ops-01', {})
    values['active_end'] = dict(session_id='S-ACTIVE-01', ended_utc=clock[0].isoformat())
    assert ready(yoked).snapshot()['can_start']


@pytest.mark.parametrize('record', [
    'S-ACTIVE-01',
    dict(session_id='S-ACTIVE-01'),
    dict(session_id='S-ACTIVE-01', ended_utc=NOW.isoformat(), station='st-01'),
    dict(session_id='not a code', ended_utc=NOW.isoformat()),
    dict(session_id='S-ACTIVE-01', ended_utc='2030-01-08T11:50:00'),
    dict(session_id='S-ACTIVE-01', ended_utc=(NOW-timedelta(hours=1)).isoformat()),
    dict(session_id='S-ACTIVE-01', ended_utc=(NOW-timedelta(hours=2)).isoformat()),
])
def test_malformed_active_close_is_rejected(record):
    """REQ-SB-03: the imported close has exactly a coded session ID and an aware end time after the active start."""
    values = {'active': (NOW-timedelta(hours=1)).isoformat(), 'active_end': record}
    with pytest.raises(ConsoleFault, match='active_end_invalid'):
        visit_window('B', 'V1', values, TODAY, 'yoked', NOW)


def test_calendar_window_deviation_still_applies_at_acquisition_visits(tmp_path):
    """REQ-YOKE-05: only the pair window is fixed; a late V2 calendar day keeps the logged-deviation path."""
    values = dict(anchors('V2', timedelta(hours=1)), V1=(TODAY-timedelta(days=5)).isoformat())
    c = station(tmp_path, 'V2', values)
    assert c.snapshot()['faults'] == ['visit_window']
    assert c.command('deviation', 'ops-01', dict(reason='visit_window', note='Late return'))['can_start']


@pytest.mark.parametrize('visit,offset', [('W1', 7), ('W4', 28)])
def test_follow_up_pair_window_is_unchanged(tmp_path, visit, offset):
    """Follow-ups keep the logged pair_window deviation and need no close record (scope decision pending in #316)."""
    values = {'V3': (TODAY-timedelta(days=offset)).isoformat(), 'active': (NOW-timedelta(hours=30)).isoformat()}
    c = station(tmp_path, visit, values)
    assert c.snapshot()['faults'] == ['pair_window']
    assert c.command('deviation', 'ops-01', dict(reason='pair_window', note='Late follow-up'))['can_start']
