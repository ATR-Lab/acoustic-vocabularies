"""Synthetic structural/equivalence evidence; no throughput or Isaac claim."""
import copy
import json
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from test_reset import manager
from test_command_lock import setup, command
from isaac.commands.queue import CommandQueue
from isaac.e2e.same_iteration import SameIterationCapture, StageMutationWatch, VerifiedTransport, advance_once_verified
from isaac.publisher.protocol import PublicRegistry, StateEncoder, encode
from isaac.publisher.runtime import StatePublisher
from isaac.reset.snapshot import sha256


def proof_fixture():
    adapter, snapshot, events, reset = manager()
    assert reset.reset()["reset_ok"]
    clock = [1_000_000_000]
    identity = ["bound-stage"]
    proof = SameIterationCapture(reset, lambda: tuple(identity), clock_ns=lambda: clock[0])
    proof.bind_iteration(1, adapter.sim_time)
    return adapter, reset, proof, clock, identity


def registry(reset):
    state = reset.neutral_state
    return PublicRegistry("engineering-fixture", reset.adapter.scene_sha256,
        reset.reset_snapshot_sha256, tuple(state["robot"]["joint_names"]),
        tuple(sorted((name, tuple(sorted(obj["state"]))) for name, obj in state["objects"].items())),
        ("fixture_anchor",))


def payload(proof, reset, clock):
    positions, objects, complete = proof.sample()
    assert proof.neutral_check(complete) is True
    encoder = StateEncoder(registry(reset), clock_ns=lambda: clock[0])
    return encode(encoder.build(positions, objects, reset.adapter.sim_time, proof._step)), complete


def test_complete_snapshot_is_detached_immutable_and_one_use():
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)["reset_ok"]
    assert isinstance(proof._capture.state_bytes, bytes)
    with pytest.raises(FrozenInstanceError):
        proof._capture.step = 100
    raw, state = payload(proof, reset, clock)
    state["objects"][next(iter(state["objects"]))]["position_m"][0] = 100.
    with pytest.raises(RuntimeError, match="Sample changed"):
        proof.consume_payload(raw)
    assert adapter.state == reset.neutral_state


def test_single_use_and_no_second_sample():
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)["reset_ok"]
    raw, state = payload(proof, reset, clock)
    with pytest.raises(RuntimeError, match="already sampled"):
        proof.sample()
    proof.consume_payload(raw)
    with pytest.raises(RuntimeError, match="already consumed"):
        proof.consume_payload(raw)


@pytest.mark.parametrize("boundary", ["control", "write", "physics", "notice", "next_iteration"])
def test_each_mutation_boundary_invalidates(boundary):
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)["reset_ok"]
    raw, state = payload(proof, reset, clock)
    if boundary == "notice":
        proof.notify_mutation()
    elif boundary == "next_iteration":
        adapter.sim_time += .01
        proof.bind_iteration(2, adapter.sim_time)
    else:
        proof.invalidate(boundary)
    with pytest.raises(RuntimeError):
        proof.consume_payload(raw)


@pytest.mark.parametrize("changed", ["scene", "snapshot", "time", "identity", "clock_backwards", "stale"])
def test_binding_changes_and_stale_or_regressed_clock_refuse(changed):
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)["reset_ok"]
    if changed == "scene": adapter.scene_sha256 = "b"*64
    elif changed == "snapshot": reset.reset_snapshot_sha256 = "c"*64
    elif changed == "time": adapter.sim_time += .01
    elif changed == "identity": identity.append("replacement")
    elif changed == "clock_backwards": clock[0] -= 1
    else: clock[0] += 250_000_001
    with pytest.raises(RuntimeError): proof.sample()


def test_notice_during_full_read_latches_failed_neutral_gate():
    adapter, reset, proof, clock, identity = proof_fixture()
    read = adapter.read_state
    def changed_read():
        result = read(); proof.notify_mutation(); return result
    adapter.read_state = changed_read
    result = reset.verify_current(capture=proof)
    assert result["reset_ok"] is False and reset.exposure_ready is False
    with pytest.raises(RuntimeError): proof.sample()


def test_failed_raw_neutral_read_never_issues_proof():
    adapter, reset, proof, clock, identity = proof_fixture()
    adapter.state["robot"]["joint_positions_rad"][0] += .2
    assert reset.verify_current(capture=proof)["reset_ok"] is False
    with pytest.raises(RuntimeError): proof.sample()


def test_different_manager_cannot_bless_wrong_neutral_authority():
    adapter, reset, proof, clock, identity = proof_fixture()
    other_adapter, other_snapshot, other_events, other = manager()
    other_snapshot['state']['robot']['joint_positions_rad'][0] = .2
    other = type(other)(other_adapter, other_snapshot, sha256(other_snapshot), other_events.append)
    assert other.reset()['reset_ok']
    # Even equal scene hashes do not make a different reset authority valid.
    result = other.verify_current(capture=proof)
    assert result['reset_ok'] is False and other.exposure_ready is False
    assert proof._capture is None and proof._reading is None
    with pytest.raises(RuntimeError): proof.sample()
    assert reset.exposure_ready is True


def test_identity_read_failure_cannot_be_restored_into_valid_token():
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)['reset_ok']
    original=proof.identity
    def failed(): raise RuntimeError('stage detached')
    proof.identity=failed
    with pytest.raises(RuntimeError):proof.sample()
    proof.identity=original
    with pytest.raises(RuntimeError):proof.sample()


def test_exact_complete_sample_required_in_neutral_guard():
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)["reset_ok"]
    _, _, state = proof.sample()
    with pytest.raises(RuntimeError, match="identity/content"):
        proof.neutral_check(copy.deepcopy(state))


@pytest.mark.parametrize('field', ['joint_velocity','root_velocity','collision','object_velocity','frame','material','light'])
def test_nonpublic_full_state_fields_remain_bound_at_transport(field):
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)['reset_ok']
    raw,state=payload(proof,reset,clock)
    obj=state['objects'][next(iter(state['objects']))]
    if field=='joint_velocity':state['robot']['joint_velocities_rad_s'][0]=1.
    elif field=='root_velocity':state['robot']['root_linear_velocity_m_s'][0]=1.
    elif field=='collision':obj['collision_enabled']=True
    elif field=='object_velocity':obj['linear_velocity_m_s'][0]=1.
    elif field=='frame':state['frames']['head']['position_m'][0]=1.
    elif field=='material':state['environment']['materials']['fixture_material']['color_rgb'][0]=1.
    else:state['environment']['lights']['fixture_light']['intensity']=1.
    with pytest.raises(RuntimeError,match='Sample changed'):
        proof.consume_payload(raw)


@pytest.mark.parametrize("changed", ["step", "time", "scene", "snapshot", "joint", "object"])
def test_transport_checks_exact_binding_and_public_values(changed):
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)["reset_ok"]
    raw, _ = payload(proof, reset, clock); frame = json.loads(raw)
    if changed == "step": frame["sim_step"] += 1
    elif changed == "time": frame["sim_time"] += .1
    elif changed == "scene": frame["scene_sha256"] = "d"*64
    elif changed == "snapshot": frame["reset_snapshot_sha256"] = "d"*64
    elif changed == "joint": frame["joint_positions"][0] += .01
    else: frame["objects"][0]["visible"] = not frame["objects"][0]["visible"]
    with pytest.raises(RuntimeError, match="not bound"):
        proof.consume_payload(encode(frame))


def test_encoding_time_notice_rejected_before_transport():
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)["reset_ok"]
    raw, _ = payload(proof, reset, clock)
    outputs = []
    transport = VerifiedTransport(SimpleNamespace(submit=outputs.append), proof)
    proof.notify_mutation()
    with pytest.raises(RuntimeError): transport.submit(raw)
    assert outputs == []


def test_notice_cannot_enter_check_to_queue_gap():
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)['reset_ok']
    raw,_=payload(proof,reset,clock)
    attempted=threading.Event();finished=threading.Event();start=threading.Event();rows=[]
    def notice():
        assert start.wait(2)
        attempted.set();proof.notify_mutation();finished.set()
    worker=threading.Thread(target=notice);worker.start()
    def enqueue(value):
        start.set();assert attempted.wait(2)
        # A callback arriving in this exact old check/enqueue gap cannot finish
        # until the bounded insertion is complete and the capture lock releases.
        assert not finished.wait(.02)
        rows.append(value)
    try:
        VerifiedTransport(SimpleNamespace(submit=enqueue),proof).submit(raw)
        worker.join(2)
        assert not worker.is_alive() and finished.is_set() and rows==[raw]
        with pytest.raises(RuntimeError):proof.consume_payload(raw)
    finally:start.set();worker.join(2)


def test_wrong_thread_cannot_use_but_can_invalidate():
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)["reset_ok"]
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(RuntimeError, match="owner thread"):
            executor.submit(proof.sample).result()
        executor.submit(proof.notify_mutation).result()
    with pytest.raises(RuntimeError): proof.sample()


def test_exact_wire_bytes_match_existing_encoder_and_detach_all_fields():
    adapter, reset, proof, clock, identity = proof_fixture()
    assert reset.verify_current(capture=proof)["reset_ok"]
    baseline = StateEncoder(registry(reset), clock_ns=lambda: clock[0])
    candidate = StateEncoder(registry(reset), clock_ns=lambda: clock[0])
    candidate.session_id = baseline.session_id
    state = adapter.read_state()
    expected = baseline.build(state["robot"]["joint_positions_rad"], state["objects"], adapter.sim_time, 1)
    q, objects, complete = proof.sample()
    assert proof.neutral_check(complete)
    actual = candidate.build(q, objects, adapter.sim_time, 1)
    assert encode(actual) == encode(expected)
    proof.consume_payload(encode(actual))
    q[0] += .1; objects[next(iter(objects))]["position_m"][0] += .1
    assert encode(actual) == encode(expected)


def test_250_randomized_valid_readbacks_preserve_exact_wire_bytes():
    rng = random.Random(54481)
    for _ in range(250):
        adapter, reset, proof, clock, identity = proof_fixture()
        for i in range(43):
            adapter.state['robot']['joint_positions_rad'][i] += rng.uniform(-1e-4, 1e-4)
            adapter.state['robot']['joint_velocities_rad_s'][i] += rng.uniform(-1e-6, 1e-6)
        for item in adapter.state['objects'].values():
            item['position_m'] = [x+rng.uniform(-1e-6,1e-6) for x in item['position_m']]
            pairs=list(item['state'].items());rng.shuffle(pairs);item['state']=dict(pairs)
        assert reset.verify_current(capture=proof)['reset_ok']
        baseline=StateEncoder(registry(reset),clock_ns=lambda:clock[0])
        candidate=StateEncoder(registry(reset),clock_ns=lambda:clock[0]);candidate.session_id=baseline.session_id
        original=adapter.read_state()
        expected=baseline.build(original['robot']['joint_positions_rad'],original['objects'],adapter.sim_time,1)
        q,objects,complete=proof.sample();assert proof.neutral_check(complete)
        actual=candidate.build(q,objects,adapter.sim_time,1)
        assert encode(actual)==encode(expected)
        proof.consume_payload(encode(actual))


class MemoryTransport:
    def __init__(self): self.rows=[]
    def submit(self, payload): self.rows.append(payload)
    def metrics(self): return dict(connected_clients=1, queue_overwrites=0, failed=False)
    def close(self): pass


def loop_fixture(tmp_path):
    adapter, snapshot, reset, dispatcher, events, motions = setup(demo=False)
    calls=[]; ticks=[1_000_000_000]
    adapter.robot=SimpleNamespace(write_data_to_sim=lambda:calls.append("flush"),update=lambda dt:None)
    def step(**_):
        calls.append("physics");adapter.sim_time += 1/60;ticks[0] += 40_000_000
    adapter.sim=SimpleNamespace(step=step,get_physics_dt=lambda:1/60)
    capture=SameIterationCapture(reset,lambda:("same-stage",),clock_ns=lambda:ticks[0])
    handoff=CommandQueue(dispatcher); transport=MemoryTransport()
    publisher=StatePublisher(registry(reset),capture.sample,VerifiedTransport(transport,capture),
        tmp_path/'publish.csv',neutral_check=capture.neutral_check,clock_ns=lambda:ticks[0])
    dispatcher.publisher=publisher;publisher.require_neutral(True)
    return adapter,reset,dispatcher,handoff,publisher,capture,transport,events,calls


@pytest.mark.parametrize("name,args", [("set_mode",{"mode":"teaching"}), ("set_mode",{"mode":"test"}),
    ("reset",{}),("pause",{}),("stop",{})])
@pytest.mark.parametrize("when", ["before", "during_capture"])
def test_control_interleavings_are_before_physics_or_after_exact_publication(tmp_path,name,args,when):
    a,r,d,h,p,c,t,events,calls=loop_fixture(tmp_path)
    future=[]
    def admit(): future.append(h.submit(command(d,name,args),"127.0.0.1"))
    if when == "before": admit()
    else:
        original=a.read_state
        def read():
            value=original()
            if not future: admit()
            return value
        a.read_state=read
    try:
        step,frame,stopped=advance_once_verified(a,d,h,p,c,0)
        reply=future[0].result(timeout=.1)
        assert reply['accepted'] is True
        if when == "before" and name == "stop":
            assert step==0 and frame is None and t.rows==[]
        else:
            assert step==1 and len(t.rows)==1
            assert json.loads(t.rows[0])==frame
        assert stopped == (name == "stop")
        with pytest.raises(RuntimeError): c.sample()
    finally: p.close()


def test_complete_reader_runs_once_per_step_and_failed_guard_never_publishes(tmp_path):
    a,r,d,h,p,c,t,events,calls=loop_fixture(tmp_path)
    original=a.read_state;reads=[]
    def read(): reads.append(1); return original()
    a.read_state=read
    try:
        assert advance_once_verified(a,d,h,p,c,0)[0]==1
        assert len(reads)==1 and len(t.rows)==1
        a.state['objects'][next(iter(a.state['objects']))]['visible']=False
        with pytest.raises(RuntimeError,match='NEUTRAL_HOLD_FAULT'):
            advance_once_verified(a,d,h,p,c,1)
        assert len(reads)==2 and len(t.rows)==1
    finally: p.close()


@pytest.mark.parametrize('unsafe', ['demo_factory','active_demo','nonneutral_hold','wrong_manager'])
def test_non_neutral_or_misbound_candidate_refuses_before_physics(tmp_path,unsafe):
    a,r,d,h,p,c,t,events,calls=loop_fixture(tmp_path)
    if unsafe=='demo_factory':d.demo_factory=lambda *_:iter(())
    elif unsafe=='active_demo':d.active={'arbitrary':'active'}
    elif unsafe=='nonneutral_hold':d.neutral_hold=False
    else:d.reset_manager=manager()[-1]
    try:
        with pytest.raises(RuntimeError,match='serial neutral-hold'):
            advance_once_verified(a,d,h,p,c,0)
        assert calls==[] and t.rows==[]
    finally:p.close()


@pytest.mark.parametrize('request_kind', ['denied_demo', 'malformed'])
def test_rejected_command_does_not_bypass_full_capture(tmp_path,request_kind):
    a,r,d,h,p,c,t,events,calls=loop_fixture(tmp_path)
    raw=command(d,'demo',{'action':'ADD_ONE','target':'tray_A'}) if request_kind=='denied_demo' else '{}'
    pending=h.submit(raw,'127.0.0.1')
    try:
        advance_once_verified(a,d,h,p,c,0)
        assert pending.result()['accepted'] is False
        assert len(t.rows)==1 and events[-1]['reply']['accepted'] is False
    finally:p.close()


def test_not_due_capture_expires_and_long_interstep_gap_stays_measured(tmp_path):
    a,r,d,h,p,c,t,events,calls=loop_fixture(tmp_path)
    try:
        p.deadline_index=1000
        assert advance_once_verified(a,d,h,p,c,0)[1] is None
        with pytest.raises(RuntimeError):c.sample()
        p.deadline_index=0
        assert advance_once_verified(a,d,h,p,c,1)[1] is not None
        assert len(t.rows)==1 and p.missed>0
    finally:p.close()


def test_fault_during_encoding_never_reaches_network(tmp_path):
    a,r,d,h,p,c,t,events,calls=loop_fixture(tmp_path)
    original=p.encoder.build
    def changed(*args):
        frame=original(*args);c.notify_mutation();return frame
    p.encoder.build=changed
    try:
        with pytest.raises(RuntimeError,match='PUBLIC_PUBLISHER_FAULT'):
            advance_once_verified(a,d,h,p,c,0)
        assert p.fault=='PUBLISHER_FAILURE' and t.rows==[]
    finally:p.close()


def notice_runtime(monkeypatch, missing=None):
    registered=[]
    class Handle:
        revoked=False
        def Revoke(self): self.revoked=True
    def register(kind,callback,*sender):
        handle=Handle();registered.append((kind,callback,sender,handle));return handle
    usd_names=('ObjectsChanged','StageContentsChanged','StageEditTargetChanged','LayerMutingChanged')
    sdf_names=('LayersDidChange','LayerDidReplaceContent','LayerDidReloadContent',
               'LayerIdentifierDidChange','LayerInfoDidChange','LayerMutenessChanged')
    usd=SimpleNamespace(**{n:n for n in usd_names if n!=missing})
    sdf=SimpleNamespace(**{n:n for n in sdf_names if n!=missing})
    monkeypatch.setitem(sys.modules,'pxr',SimpleNamespace(Usd=SimpleNamespace(Notice=usd),
        Sdf=SimpleNamespace(Notice=sdf),Tf=SimpleNamespace(Notice=SimpleNamespace(Register=register,RegisterGlobally=register))))
    return registered


@pytest.mark.parametrize('notice', ['ObjectsChanged','StageContentsChanged','StageEditTargetChanged','LayerMutingChanged',
    'LayersDidChange','LayerDidReplaceContent','LayerDidReloadContent','LayerIdentifierDidChange','LayerInfoDidChange','LayerMutenessChanged'])
def test_each_required_notice_invalidates_even_restored_state(monkeypatch,notice):
    registered=notice_runtime(monkeypatch)
    a,r,c,clock,identity=proof_fixture();a.accessors=SimpleNamespace(stage=object())
    watch=StageMutationWatch(a,c.notify_mutation)
    assert r.verify_current(capture=c)['reset_ok']
    callback=next(row[1] for row in registered if row[0]==notice)
    callback(None,a.accessors.stage)
    with pytest.raises(RuntimeError):c.sample()
    watch.close();assert all(row[3].revoked for row in registered)


def test_missing_notice_binding_revokes_partial_registration(monkeypatch):
    registered=notice_runtime(monkeypatch,missing='LayerInfoDidChange')
    a,r,c,clock,identity=proof_fixture();a.accessors=SimpleNamespace(stage=object())
    with pytest.raises(AttributeError):StageMutationWatch(a,c.notify_mutation)
    assert registered and all(row[3].revoked for row in registered)
