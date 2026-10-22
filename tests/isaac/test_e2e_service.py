"""Owner-thread service sequencing; actual Isaac E2E evidence is separate."""
import ast
import gc
import inspect
from types import SimpleNamespace
import weakref

import pytest

from isaac.e2e import service
from isaac.e2e.service import advance_once, freeze_startup_heap, run_joined_service


@pytest.fixture
def unfrozen():
    gc.unfreeze()
    yield
    gc.unfreeze()


def test_startup_heap_freeze_keeps_gc_enabled_with_unchanged_thresholds(unfrozen):
    settings=(gc.isenabled(),gc.get_threshold())
    survivors=[{'node':i} for i in range(5000)]
    record=freeze_startup_heap()
    assert (gc.isenabled(),gc.get_threshold())==settings
    assert record['enabled']==settings[0] and tuple(record['thresholds'])==settings[1]
    # Refcount frees can still remove frozen objects, so compare lower bounds.
    assert record['frozen_objects']>=len(survivors) and gc.get_freeze_count()>=len(survivors)
    assert record['duration_ms']>=0 and record['collected']>=0


def test_startup_garbage_is_collected_not_frozen_and_new_cycles_still_collect(unfrozen):
    class Node: pass
    def cycle():
        a,b=Node(),Node();a.peer,b.peer=b,a
        return weakref.ref(a)
    before=cycle()
    freeze_startup_heap()
    assert before() is None  # the pre-freeze full collection freed it
    after=cycle()
    gc.collect()
    assert after() is None   # collection still works for post-READY objects


def test_service_freezes_after_startup_and_before_the_paced_loop():
    # The freeze must follow all startup allocations (transports, journals,
    # trace) and precede READY, which is only written inside the paced loop.
    tree=ast.parse(inspect.getsource(service.run_joined_service))
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and getattr(n.func,'id',None) in
           ('freeze_startup_heap','PrivateCommandTransport','StepTrace')]
    lines={n.func.id:n.lineno for n in calls}
    loop=min(n.lineno for n in ast.walk(tree) if isinstance(n,ast.While))
    assert lines['PrivateCommandTransport']<lines['StepTrace']<lines['freeze_startup_heap']<loop
    assert sum(n.func.id=='freeze_startup_heap' for n in calls)==1


def fixture():
    calls=[]
    robot=SimpleNamespace(write_data_to_sim=lambda:calls.append('flush'),update=lambda dt:calls.append(('update',dt)))
    sim=SimpleNamespace(step=lambda **kwargs:calls.append(('physics',kwargs)),get_physics_dt=lambda:1/60)
    adapter=SimpleNamespace(robot=robot,sim=sim,sim_time=2.)
    dispatcher=SimpleNamespace(fault=None,stopped=False,
        after_physics_step=lambda:calls.append('full_hold_verify') or True)
    handoff=SimpleNamespace(drain=lambda:calls.append('commands'),refresh_health=lambda:calls.append('health'))
    publisher=SimpleNamespace(fault=None,after_step=lambda t,n:calls.append(('guarded_publish',t,n)) or {'seq':0})
    return calls,adapter,dispatcher,handoff,publisher


def test_actual_step_checks_precede_guarded_publication_and_health():
    calls,a,d,h,p=fixture()
    result=advance_once(a,d,h,p,7)
    assert result==(8,{'seq':0},False)
    assert calls==['commands','flush',('physics',{'render':False}),('update',1/60),
        'full_hold_verify','commands',('guarded_publish',2.,8),'health']


def test_private_stop_does_not_advance_or_publish():
    calls,a,d,h,p=fixture();d.stopped=True
    assert advance_once(a,d,h,p,7)==(7,None,True)
    assert calls==['commands']


def test_full_hold_failure_prevents_publication():
    calls,a,d,h,p=fixture();d.after_physics_step=lambda:False
    with pytest.raises(RuntimeError,match='NEUTRAL_HOLD_FAULT'):advance_once(a,d,h,p,0)
    assert not any(isinstance(x,tuple) and x[0]=='guarded_publish' for x in calls)


def test_queued_reset_stop_boundary_precedes_publication():
    calls,a,d,h,p=fixture()
    def drain():
        calls.append('commands')
        if calls.count('commands')==2:d.stopped=True
    h.drain=drain
    assert advance_once(a,d,h,p,0)==(1,None,True)
    assert not any(isinstance(x,tuple) and x[0]=='guarded_publish' for x in calls)


@pytest.mark.parametrize('which',['before','after','publisher'])
def test_any_latched_fault_aborts(which):
    calls,a,d,h,p=fixture()
    if which=='before':d.fault='failure'
    elif which=='after':
        def drain():
            calls.append('commands')
            if calls.count('commands')==2:d.fault='failure'
        h.drain=drain
    else:p.fault='failure'
    with pytest.raises(RuntimeError):advance_once(a,d,h,p,0)
    assert 'health' not in calls


def test_not_yet_due_frame_is_not_fabricated():
    calls,a,d,h,p=fixture();p.after_step=lambda t,n:None
    assert advance_once(a,d,h,p,0)==(1,None,False)
    assert calls[-1]=='health'


@pytest.mark.parametrize('seconds',[0,4,3601,float('inf'),float('nan'),True])
def test_service_duration_is_explicit_and_bounded(tmp_path,seconds):
    with pytest.raises(ValueError,match='duration'):
        run_joined_service(None,None,tmp_path/'evidence',seconds=seconds,station_id='sim-01',
            host_uid=1000,public_socket=tmp_path/'a.sock',private_socket=tmp_path/'b.sock')


@pytest.mark.parametrize('session',[None,'','A'*32,'a'*31,'a'*33,'not-a-session'])
def test_control_session_must_be_exact_pinned_identifier(tmp_path,session):
    with pytest.raises(ValueError,match='control session'):
        run_joined_service(None,None,tmp_path/'evidence',seconds=5,station_id='sim-01',
            host_uid=1000,public_socket=tmp_path/'a.sock',private_socket=tmp_path/'b.sock',
            control_session_id=session)
