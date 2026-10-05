import copy
import csv
import io
import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from av_schedules.masking import find_method_strings
from av_schedules.planning import RUN_SHEET_COLUMNS
from ops.console.core import Audit, Console, ConsoleFault, digest, encoded, health_view, load_bundle, strict_json, visit_window
from ops.console.server import demo_catalog, make_server, writer_locks
from ops.console.transport import DemoEngine, Mailbox

NOW = datetime(2030, 1, 8, 12, tzinfo=timezone.utc)


@pytest.fixture
def console(tmp_path):
    c = Console(demo_catalog(), DemoEngine(), Audit(tmp_path/'audit.local.jsonl', 'protocol-test'))
    c.load('demo-a', 'ops-01')
    return c


@pytest.mark.parametrize('study,visit,anchor,offset,tolerance', [
    ('A','D7','D0',7,1), ('B','V2','V1',2,1), ('B','V3','V1',4,1),
    ('B','W1','V3',7,1), ('B','W4','V3',28,2)])
def test_calendar_window_edges_and_outside(study,visit,anchor,offset,tolerance):
    base = date(2030, 1, 1)
    for delta in (-tolerance, tolerance):
        assert not visit_window(study,visit,{anchor:base.isoformat()},base+timedelta(days=offset+delta))['faults']
    for delta in (-tolerance-1,tolerance+1):
        assert visit_window(study,visit,{anchor:base.isoformat()},base+timedelta(days=offset+delta))['faults']==['visit_window']


@pytest.mark.parametrize('seconds,passes',[(0,True),(86400,True),(-1,False),(86401,False)])
def test_yoked_order_and_24_hours(seconds,passes):
    view = visit_window('B','V1',{'active':NOW.isoformat()},NOW.date(),'yoked',NOW+timedelta(seconds=seconds))
    assert (not view['faults']) is passes


def test_missing_anchor_is_not_overrideable(console):
    console.visit = replace(console.visit, anchors={})
    with pytest.raises(ConsoleFault,match='anchor_missing'):
        console.snapshot()


def test_window_override_never_overrides_hash_or_lock(console):
    console.visit = replace(console.visit, anchors={'D0':'2000-01-01'})
    console.command('checks','ops-01',dict(comfort=True,phone=True))
    assert not console.snapshot()['can_start']
    console.command('deviation','ops-01',dict(reason='visit_window',note='Late return recorded'))
    assert console.snapshot()['can_start']
    original = console.engine.snapshot
    def broken():
        state=original();state['admission']['verified']=False;state['admission']['locks_ok']=False
        return state
    console.engine.snapshot=broken
    assert set(console.snapshot()['faults']) >= {'hash_mismatch','locks_unavailable'}
    with pytest.raises(ConsoleFault,match='start_blocked'):
        console.command('start','ops-01',{})
    console.command('stop','ops-01',{})
    assert console.engine.state=='stopped'


def test_override_expires_next_calendar_day(console):
    console.clock=lambda:NOW
    console.visit=replace(console.visit,anchors={'D0':'2000-01-01'})
    console.command('deviation','ops-01',dict(reason='visit_window',note='Late return'))
    assert 'visit_window' not in console.snapshot()['faults']
    console.clock=lambda:NOW+timedelta(days=1)
    assert 'visit_window' in console.snapshot()['faults']


def test_demo_pause_holds_next_slot_and_exports_exact_header(console):
    now=[100.0];console.engine.mono=lambda:now[0]
    console.command('checks','ops-01',dict(comfort=True,phone=True))
    console.command('start','ops-01',{})
    console.command('pause','ops-01',{})
    assert console.snapshot()['state']=='running'
    now[0]=103.0
    view=console.snapshot()
    assert view['state']=='paused' and view['rows'][0]['actual']==1
    now[0]=999
    assert console.snapshot()['rows'][0]['actual']==1
    console.command('stop','ops-01',{})
    console.command('signoff','ops-01',{})
    rows=list(csv.reader(io.StringIO(console.run_sheet().decode())))
    assert tuple(rows[0])==RUN_SHEET_COLUMNS and rows[1][4]=='1'
    assert rows[1][5:7]==['','']  # No invented per-block timestamps.
    assert rows[1][-1]=='ops-01'
    assert {r['event'] for r in console.audit.rows} >= {'pause_requested','pause_acknowledged'}


@pytest.mark.parametrize('key,value,fault',[('bridge_age_ms',250.001,'bridge_stale'),
    ('max_gap_ms',300,'frame_freeze'),('headset',False,'headset_unavailable')])
def test_named_health_faults_and_no_hidden_data(console,key,value,fault):
    state=console.engine.snapshot()
    state['health'][key]=value
    state['intended_tuple']='ADD_ONE A'
    state['correctness']='correct'
    state['restricted_label']='A1'
    console.engine.snapshot=lambda:state
    view=console.snapshot()
    assert fault in view['faults']
    assert not find_method_strings(encoded(view).decode())
    assert not {'intended_tuple','correctness','restricted_label'} & view.keys()


def test_health_closed_types_and_threshold(console):
    health=console.engine.snapshot()['health']
    health['bridge_age_ms']=health['max_gap_ms']=250
    assert not health_view(health)[1]
    for value in (True,float('nan'),-1):
        with pytest.raises(ConsoleFault):health_view(dict(health,bridge_age_ms=value))
    with pytest.raises(ConsoleFault):health_view(dict(health,answer='hidden'))


@pytest.mark.parametrize('note',['A1','A2','A3','designer','Transformer','human voice','candidate method'])
def test_masking_blocks_free_text_without_echo(console,note):
    before=len(console.audit.rows)
    with pytest.raises(ConsoleFault,match='masked_value_rejected'):
        console.command('deviation','ops-01',dict(reason='procedure',note=note))
    assert len(console.audit.rows)==before


def test_csv_formula_neutralized_and_audit_tamper_rejected(console):
    console.command('deviation','ops-01',dict(reason='procedure',note='=1+1'))
    assert "'=1+1" in console.deviation_csv().decode()
    p=console.audit.path
    data=p.read_bytes()
    p.write_bytes(data[:-1])
    with pytest.raises(ConsoleFault,match='audit_damaged'):Audit(p,'protocol-test')
    last=json.loads(data.splitlines()[-1]);last['details']['note']='Edited last complete note'
    p.write_bytes(b'\n'.join(data.splitlines()[:-1])+b'\n'+encoded(last)+b'\n')
    with pytest.raises(ConsoleFault,match='audit_damaged'):Audit(p,'protocol-test')
    p.write_bytes(data.replace(b'ops-01',b'ops-02',1))
    with pytest.raises(ConsoleFault,match='audit_damaged'):Audit(p,'protocol-test')


def test_audit_failure_blocks_playback(console,monkeypatch):
    def fail(_):raise OSError('private path must never echo')
    monkeypatch.setattr('ops.console.core.os.fsync',fail)
    with pytest.raises(ConsoleFault,match='audit_failed'):
        console.command('checks','ops-01',dict(comfort=True,phone=True))
    assert console.audit.failed and not console.comfort
    assert 'audit_failed' in console.snapshot()['faults']


def test_duplicate_json_and_demo_non_demo_rejected(console):
    with pytest.raises(ConsoleFault):strict_json(b'{"x":1,"x":2}')
    with pytest.raises(ConsoleFault,match='demo_only'):
        DemoEngine().bind(replace(console.visit,demo=False))


def test_http_origin_csrf_masking_and_export(console):
    server=make_server(console,demo=True)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    def request(path,body=None,origin_header=None,token=None):
        headers={}
        if body is not None:headers['Content-Type']='application/json'
        if origin_header:headers['Origin']=origin_header
        if token:headers['X-Console-Token']=token
        return urllib.request.urlopen(urllib.request.Request(origin+path,data=encoded(body) if body is not None else None,headers=headers))
    try:
        with request('/api/state') as response:
            data=json.load(response)
            assert response.headers['Cache-Control']=='no-store'
        body=dict(action='checks',staff='ops-01',payload=dict(comfort=True,phone=True))
        for source,token in [('https://outside.invalid',data['token']),(origin,'wrong')]:
            with pytest.raises(urllib.error.HTTPError) as error:request('/api/command',body,source,token)
            assert error.value.code==409
        with request('/api/command',body,origin,data['token']) as response:
            assert json.load(response)['can_start']
        for path in ('/','/api/state','/api/run-sheet.csv','/api/deviations.csv'):
            with request(path) as response:assert not find_method_strings(response.read().decode())
        with pytest.raises(urllib.error.HTTPError):request('/api/command',dict(body,payload=dict(comfort=True,phone=True,answer='A1')),origin,data['token'])
    finally:
        server.shutdown();server.server_close();worker.join()


def packet(visit):
    state=DemoEngine();state.bind(visit)
    return dict(state.snapshot(),version=1,session_nonce='1'*32,sequence=1,utc=NOW.isoformat(),receipt=None)


def test_mailbox_restart_replay_and_stale_fail_closed(console,tmp_path):
    p=tmp_path/'state.json';state=packet(console.visit);p.write_bytes(encoded(state))
    elapsed=[1.0]
    box=Mailbox(tmp_path,clock=lambda:NOW,mono=lambda:elapsed[0])
    assert box.snapshot()['sequence']==1
    elapsed[0]=3.01
    with pytest.raises(ConsoleFault,match='engine_stale'):box.snapshot()
    state['sequence']=2;p.write_bytes(encoded(state));box.snapshot()
    state['sequence']=1;p.write_bytes(encoded(state))
    with pytest.raises(ConsoleFault,match='engine_replayed'):box.snapshot()
    state['sequence']=3;state['utc']=(NOW-timedelta(seconds=3)).isoformat();p.write_bytes(encoded(state))
    with pytest.raises(ConsoleFault,match='engine_stale'):box.snapshot()
    state['utc']=NOW.isoformat();state['session_nonce']='2'*32;p.write_bytes(encoded(state));box.visit=console.visit
    with pytest.raises(ConsoleFault,match='engine_restarted_reload'):box.snapshot()
    assert box.visit is None
    with pytest.raises(ConsoleFault,match='engine_restarted_reload'):box.snapshot()


def test_mailbox_exact_receipt_no_duplicate_side_effect(console,tmp_path):
    import time
    state=packet(console.visit);state['utc']=datetime.now(timezone.utc).isoformat()
    p=tmp_path/'state.json';p.write_bytes(encoded(state))
    outcomes=[]
    box=Mailbox(tmp_path,timeout=.25,receipt_sink=outcomes.append);box.snapshot();box.visit=console.visit
    with pytest.raises(ConsoleFault,match='command_uncertain'):box.command('pause')
    command=strict_json((tmp_path/'command.json').read_bytes())
    with pytest.raises(ConsoleFault,match='command_uncertain'):box.command('pause')
    assert strict_json((tmp_path/'command.json').read_bytes())==command
    state['receipt']={k:command[k] for k in ('request_id','sequence')}
    state['receipt'].update(status='accepted',code='none');state['sequence']+=1
    p.write_bytes(encoded(state));box.snapshot();assert box.pending is None
    assert len(outcomes)==1 and outcomes[0]['command']=='pause' and outcomes[0]['status']=='accepted'
    box.snapshot();assert len(outcomes)==1


def test_stop_supersedes_uncertain_pause_but_never_replays_start(console,tmp_path):
    state=packet(console.visit);state['utc']=datetime.now(timezone.utc).isoformat()
    (tmp_path/'state.json').write_bytes(encoded(state))
    box=Mailbox(tmp_path,timeout=.03,receipt_sink=lambda _:None);box.snapshot();box.visit=console.visit
    with pytest.raises(ConsoleFault,match='command_uncertain'):box.command('pause')
    with pytest.raises(ConsoleFault,match='command_uncertain'):box.command('start')
    with pytest.raises(ConsoleFault,match='command_uncertain'):box.command('stop')
    request=strict_json((tmp_path/'command.json').read_bytes())
    assert request['command']=='stop' and request['sequence']==2


def test_mailbox_lock_blocks_second_port_or_audit_path(tmp_path):
    mailbox=tmp_path/'mailbox';mailbox.mkdir()
    with writer_locks(tmp_path/'one.local.jsonl',mailbox):
        with pytest.raises(FileExistsError):
            with writer_locks(tmp_path/'two.local.jsonl',mailbox):
                pytest.fail('second writer admitted')
        assert (mailbox/'console-writer.lock').exists()
        assert not (tmp_path/'two.local.jsonl.lock').exists()
    assert not (mailbox/'console-writer.lock').exists()


def test_failed_late_receipt_audit_preserves_uncertainty(console,tmp_path):
    state=packet(console.visit);state['utc']=datetime.now(timezone.utc).isoformat()
    p=tmp_path/'state.json';p.write_bytes(encoded(state))
    def fail(_):raise ConsoleFault('audit_failed')
    box=Mailbox(tmp_path,timeout=.01,receipt_sink=fail);box.snapshot();box.visit=console.visit
    with pytest.raises(ConsoleFault,match='command_uncertain'):box.command('pause')
    state['receipt']=dict(request_id=box.pending['request_id'],sequence=1,status='accepted',code='none')
    state['sequence']+=1;p.write_bytes(encoded(state))
    with pytest.raises(ConsoleFault,match='audit_failed'):box.snapshot()
    assert box.pending is not None


def test_console_explicit_reload_clears_restart_gate_without_preserving_checks(console,tmp_path):
    state=packet(console.visit);p=tmp_path/'state.json';p.write_bytes(encoded(state))
    box=Mailbox(tmp_path,clock=lambda:NOW)
    calls=[]
    box.command=lambda action:calls.append(action)
    c=Console({'visit':lambda:console.visit},box,Audit(tmp_path/'reload.local.jsonl','protocol-test'))
    c.load('visit','ops-01');c.comfort=c.phone=True
    state['session_nonce']='2'*32;p.write_bytes(encoded(state))
    for _ in range(2):
        with pytest.raises(ConsoleFault,match='engine_restarted_reload'):c.snapshot()
    c.load('visit','ops-01')
    assert calls==['load','load'] and not c.comfort and not c.phone
    assert not c.snapshot()['can_start']


def test_producer_chain_and_revealed_allocation(tmp_path):
    from av_schedules.run_sheet_output import generate_run_sheets, EXAMPLE_RUN_SHEET_SEED
    from av_schedules.run_sheets import placeholder_package_hashes, parse_package_hashes
    from av_schedules.schedule_output import render_schedules
    from av_schedules.design import build_units
    from av_schedules.seeds import demo_seed
    from av_schedules.reveal import RevealLog
    root=Path(__file__).resolve().parents[3]
    for study in ('A','B'):
        folder=tmp_path/study;folder.mkdir()
        seed=demo_seed(EXAMPLE_RUN_SHEET_SEED);units=build_units(seed,study,'pilot')
        mapping=strict_json(placeholder_package_hashes(seed,units));mapping['placeholder']=False
        packages=encoded(mapping);hashes=parse_package_hashes(packages)
        sheets=generate_run_sheets(seed,study,'pilot',package_hashes=hashes)
        schedules=render_schedules(units,seed)
        reveals=RevealLog(root/'schedules/examples/demo-allocation'/study/('pilot-slots.json' if study=='A' else 'pilot-dyads.json'),folder/'reveal.local.jsonl')
        checks=dict(consent=True,compatibility=True,orientation=True) if study=='A' else dict(consent=True,screening=True,compatibility=True,scheduling=True)
        people=['p-01'] if study=='A' else ['p-01','p-02']
        eligibility=reveals.log_eligibility(people,staff='ops-01',checks=checks)
        entry=reveals.reveal_next(eligibility,staff='ops-01')
        person=entry['slot_id'] if study=='A' else entry['members'][0]['slot_id']
        visit='D0' if study=='A' else 'V1';unit=entry['unit_id']
        manifest=sheets['pilot-run-sheets-manifest.json']
        inputs=dict(manifest=manifest,schedules=schedules['pilot-schedules-manifest.json'],
                    schedule=schedules[f'{unit}/schedules/{person}/{visit}.json'],sheet=sheets[f'{unit}/run-sheets/{person}/{visit}.csv'],packages=packages)
        config=dict(participant='p-01',anchors={},manifest_sha256=digest(manifest),allocation_list_sha256=reveals.list_sha256)
        for key,data in inputs.items():
            path=folder/(key+'.local.bin');path.write_bytes(data);config[key]=str(path)
        loaded=load_bundle(config,reveals)
        assert loaded.study==study and loaded.participant=='p-01' and loaded.demo
        assert not find_method_strings(encoded(loaded.rows).decode())
        with pytest.raises(ConsoleFault,match='allocation_source_mismatch'):
            load_bundle(dict(config,allocation_list_sha256='0'*64),reveals)
        assert not reveals.matches_source(study=study,set_name='pilot',demo=True,
                                          seed_label='DEMO-other-seed',list_sha256=reveals.list_sha256)
        with pytest.raises(ConsoleFault,match='allocation_unbound'):load_bundle(dict(config,participant='p-99'),reveals)
        Path(config['sheet']).write_bytes(inputs['sheet']+b'bad')
        with pytest.raises(ConsoleFault,match='file_hash_mismatch'):load_bundle(config,reveals)
