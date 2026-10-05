"""Real #13 DEMO package/bank and #11 store, with default admissibility intact."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import uuid

import pytest

# Root repository checks have no sound runtime. The sound workflow explicitly
# collects this module on Linux/macOS/Windows with its existing locked NumPy.
pytest.importorskip('numpy', reason='actual #11 bridge tests run in locked sound CI on all three OS')

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'sound/src')]
from tools.menu_store_bridge import MenuStoreBridge, BridgeError, EMPTY_SNAPSHOT, digest, store_lock, serve_mailbox, _atomic_new
from av_sound.dyad_bank import synthetic_dyad_bank
from av_sound.package import build_dyad_package
from av_sound.store import VocabularyStore, canonical_json


@pytest.fixture(scope='module')
def producer(tmp_path_factory):
    root = tmp_path_factory.mktemp('actual-producer-demo')
    bank = synthetic_dyad_bank()
    path = root/'bank.json'; path.write_bytes(canonical_json(bank.to_dict()))
    result = build_dyad_package(bank, root/'package')
    return dict(schema_version=1, demo_only=True, unit_id='DEMO-UNIT-01', book_id='DEMO-BOOK-01',
                bank_path=str(path), bank_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bank_sha256=bank.bank_sha256(), package_path=str(result.path), package_sha256=result.package_sha256)


@pytest.fixture
def bridge(producer, tmp_path):
    return MenuStoreBridge(dict(producer, store_root=str(tmp_path/'store')))


def request(bridge, previous=None, *, atom=None, rank=1, profile='P1'):
    return dict(schema_version=1, request_id=uuid.uuid4().hex, operation='atom' if atom else 'profile',
                unit_id=bridge.config['unit_id'], book_id=bridge.book_id,
                bank_sha256=bridge.config['bank_sha256'], package_sha256=bridge.config['package_sha256'],
                expected_head=previous['after_head'] if previous else None,
                expected_snapshot_sha256=previous['after_snapshot_sha256'] if previous else EMPTY_SNAPSHOT,
                profile=profile, menu_key=atom or 'profile', rank=rank if atom else None)


def verified(bridge, receipt=None):
    return bridge.verified_snapshot(expected_head=receipt['after_head'] if receipt else None,
                                    expected_snapshot_sha256=receipt['after_snapshot_sha256'] if receipt else EMPTY_SNAPSHOT)


def test_profile_once_persists_and_duplicate_is_noop(bridge):
    r=request(bridge); receipt=bridge.select(r)
    head=bridge.store.head(bridge.book_id)
    assert receipt['status']=='profile_selected' and receipt['pcm_sha256'] is None
    assert receipt['receipt_sha256']==digest({k:v for k,v in receipt.items() if k!='receipt_sha256'})
    restarted=MenuStoreBridge(bridge.config)
    assert restarted.select(r)==receipt and restarted.store.head(bridge.book_id)==head
    assert len(restarted.store.records(bridge.book_id))==1
    with pytest.raises(BridgeError,match='PROFILE_ALREADY_SELECTED'):
        restarted.select(request(restarted,receipt,profile='P2'))


def test_real_atom_uses_upstream_store_and_exact_pcm(bridge):
    receipt=bridge.select(request(bridge))
    r=request(bridge,receipt,atom='K-a1')
    selected=bridge.select(r)
    assert selected['status']=='committed'
    entry=bridge.store.get(bridge.book_id,'K-a1')
    assert entry.pcm_sha256==selected['pcm_sha256']
    assert entry.file_sha256==selected['file_sha256']
    assert bridge.select(r)==selected
    assert len(bridge.store.records(bridge.book_id))==2
    manifest=verified(bridge,selected)
    assert manifest['entries']==[dict(atom_id='K-a1',profile='P1',rank=1,pcm_sha256=entry.pcm_sha256,
        file_sha256=entry.file_sha256,selection_receipt_sha256=selected['receipt_sha256'])]
    assert manifest['participant_ready'] is False and manifest['profile']=='P1'


@pytest.mark.parametrize('field,value,code',[
 ('rank',4,'ATOM_SHAPE'),('rank',True,'ATOM_SHAPE'),('expected_head','0'*64,'OLD_SNAPSHOT_PIN'),
 ('expected_snapshot_sha256','0'*64,'OLD_SNAPSHOT_PIN'),('profile','P2','FIXED_PROFILE'),
 ('package_sha256','0'*64,'REQUEST_BINDING'),('menu_key','unknown','ATOM_SHAPE')])
def test_bad_selection_refused_without_store_write(bridge,field,value,code):
    receipt=bridge.select(request(bridge)); before=bridge.store.log_path(bridge.book_id).read_bytes()
    r=request(bridge,receipt,atom='K-a1');r[field]=value
    with pytest.raises(BridgeError,match=code):bridge.select(r)
    assert bridge.store.log_path(bridge.book_id).read_bytes()==before


def test_same_request_id_different_bytes_rejected(bridge):
    r=request(bridge);bridge.select(r);r['profile']='P2'
    with pytest.raises(BridgeError,match='REQUEST_ID_CONFLICT'):bridge.select(r)


def test_bank_byte_pin_rejected(producer,tmp_path):
    config=dict(producer,store_root=str(tmp_path/'store'),bank_file_sha256='0'*64)
    b=MenuStoreBridge(config)
    with pytest.raises(BridgeError,match='BANK_FILE_HASH'):b.select(request(b))
    assert not b.store.log_path(b.book_id).exists()


def test_non_demo_config_refused(producer,tmp_path):
    with pytest.raises(BridgeError,match='QUALIFIED_BANK_UNAVAILABLE'):
        MenuStoreBridge(dict(producer,store_root=str(tmp_path/'store'),demo_only=False))


def test_config_and_request_inputs_detached(bridge):
    c=copy.deepcopy(bridge.config);other=MenuStoreBridge(c);c['unit_id']='DEMO-CHANGED'
    assert other.config['unit_id']==bridge.config['unit_id']
    r=request(other);saved=copy.deepcopy(r);other.select(r);assert r==saved


def test_actual_commit_crash_recovers_without_recommit(bridge,monkeypatch):
    receipt=bridge.select(request(bridge)); r=request(bridge,receipt,atom='K-a1')
    original=bridge._append
    def crashed(*args):raise OSError('injected after actual store append')
    monkeypatch.setattr(bridge,'_append',crashed)
    with pytest.raises(OSError):bridge.select(r)
    assert bridge.pending.exists() and len(bridge.store.records(bridge.book_id))==2
    with pytest.raises(BridgeError,match='UNRESOLVED_INTENT'):
        MenuStoreBridge(bridge.config).select(dict(r,request_id=uuid.uuid4().hex))
    recovered=MenuStoreBridge(bridge.config).select(r)
    assert recovered['status']=='committed' and len(bridge.store.records(bridge.book_id))==2
    assert not bridge.pending.exists()


def test_profile_crash_recovers_without_new_book(bridge,monkeypatch):
    r=request(bridge)
    monkeypatch.setattr(bridge,'_append',lambda *a: (_ for _ in ()).throw(OSError('injected')))
    with pytest.raises(OSError):bridge.select(r)
    assert MenuStoreBridge(bridge.config).select(r)['status']=='profile_selected'
    assert len(bridge.store.records(bridge.book_id))==1


def test_journal_suffix_removal_refused(bridge):
    receipt=bridge.select(request(bridge));bridge.select(request(bridge,receipt,atom='K-a1'))
    lines=bridge.journal.read_bytes().splitlines(keepends=True);bridge.journal.write_bytes(lines[0])
    with pytest.raises(BridgeError,match='STORE_HEAD_CHANGED'):verified(bridge)


def test_changed_actual_old_blob_refused(bridge):
    receipt=bridge.select(request(bridge));bridge.select(request(bridge,receipt,atom='K-a1'))
    entry=bridge.store.get(bridge.book_id,'K-a1');blob=bridge.store.blob_path(entry.pcm_sha256)
    import os,stat
    os.chmod(blob,stat.S_IWRITE|stat.S_IREAD)
    data=bytearray(blob.read_bytes());data[-1]^=1;blob.write_bytes(data)
    with pytest.raises(BridgeError,match='STORE_INTEGRITY'):verified(bridge)


def test_other_direct_store_write_refused(bridge):
    receipt=bridge.select(request(bridge));bridge.store.freeze(bridge.book_id)
    with pytest.raises(BridgeError,match='STORE_HEAD_CHANGED'):bridge.select(request(bridge,receipt,atom='K-a1'))


def test_store_scoped_writer_lock(bridge):
    with store_lock(bridge.store.root):
        with pytest.raises(BridgeError,match='STORE_BUSY'):bridge.select(request(bridge))


def test_read_requires_latest_independent_pin(bridge):
    first=bridge.select(request(bridge));bridge.select(request(bridge,first,atom='K-a1'))
    with pytest.raises(BridgeError,match='READ_SNAPSHOT_PIN'):verified(bridge,first)


def test_receipt_written_then_crash_recovers_same_receipt(bridge,monkeypatch):
    r=request(bridge);original=bridge._append
    def interrupted(*args):
        original(*args)
        raise OSError('injected after durable journal append')
    monkeypatch.setattr(bridge,'_append',interrupted)
    with pytest.raises(OSError):bridge.select(r)
    assert bridge.pending.exists()
    result=MenuStoreBridge(bridge.config).select(r)
    assert result['status']=='profile_selected' and len(bridge.store.records(bridge.book_id))==1
    assert not bridge.pending.exists()


def test_new_request_cannot_rewrite_old_atom(bridge):
    receipt=bridge.select(request(bridge));receipt=bridge.select(request(bridge,receipt,atom='K-a1'))
    before=bridge.store.log_path(bridge.book_id).read_bytes()
    with pytest.raises(BridgeError,match='ATOM_ALREADY_COMMITTED'):
        bridge.select(request(bridge,receipt,atom='K-a1',rank=2))
    assert bridge.store.log_path(bridge.book_id).read_bytes()==before


def test_actual_default_rank_one_choices_preserve_old_entries_across_waves(bridge):
    from av_sound.package import ATOM_WAVES
    from av_sound.grammar import ATOM_IDS
    receipt=bridge.select(request(bridge));initial_hashes={};rejected=0;counts=[]
    for wave in (1,2,3):
        for atom in (a for a in ATOM_IDS if ATOM_WAVES[a]==wave):
            before=bridge.store.snapshot(bridge.book_id);head=bridge.store.head(bridge.book_id)
            r=request(bridge,receipt,atom=atom)
            receipt=bridge.select(r)
            after=bridge.store.snapshot(bridge.book_id)
            assert all(after[a]==v for a,v in before.items())
            assert all(after[a]==v for a,v in initial_hashes.items())
            if receipt['status']=='rejected':
                rejected+=1
                assert receipt['reason']=='E_REJECTED' and receipt['after_head']==head and after==before
                assert bridge.select(r)==receipt
            else:
                assert receipt['status']=='committed'
                initial_hashes[atom]=after[atom]
        counts.append(len(initial_hashes))
    assert rejected==0 and counts==[8,12,16]  # This one DEMO choice path; not all bank combinations.
    assert len(verified(bridge,receipt)['entries'])==16-rejected
    print({'synthetic_attempt_counts':[8,12,16],'actual_committed_counts':counts,'actual_rejections':rejected})


def test_actual_store_rejects_close_adversarial_demo_candidate(tmp_path):
    from av_sound.dyad_bank import DyadBank, BankOption
    from av_sound.recipe import Recipe
    from av_sound.renderer import render
    bank=synthetic_dyad_bank('DEMO-ADVERSARIAL')
    base=bank.option('P1','K-a1',1).recipe
    pitches=list(base.pitches);pitches[0]+=1 if pitches[0]<6 else -1
    close=Recipe(base.total_ms,tuple(pitches),base.rhythm_weights,base.gaps_ms,base.amplitudes)
    cells=dict(bank.cells);old=cells[('P1','K-a2')]
    cells[('P1','K-a2')]=(BankOption(1,close,render(close,'P1').pcm_sha256,'demo-negative'),*old[1:])
    bank=DyadBank(bank.bank_id,True,bank.labels,cells)
    raw=tmp_path/'bank.json';raw.write_bytes(canonical_json(bank.to_dict()))
    result=build_dyad_package(bank,tmp_path/'package')
    bridge=MenuStoreBridge(dict(schema_version=1,demo_only=True,unit_id='DEMO-NEGATIVE',book_id='DEMO-NEGATIVE',
        bank_path=str(raw),bank_file_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),bank_sha256=bank.bank_sha256(),
        package_path=str(result.path),package_sha256=result.package_sha256,store_root=str(tmp_path/'store')))
    receipt=bridge.select(request(bridge));receipt=bridge.select(request(bridge,receipt,atom='K-a1'))
    old_bytes=bridge.store.log_path(bridge.book_id).read_bytes()
    r=request(bridge,receipt,atom='K-a2');refused=bridge.select(r)
    assert refused['status']=='rejected' and refused['reason']=='E_REJECTED' and not refused['accepted']
    assert bridge.store.log_path(bridge.book_id).read_bytes()==old_bytes
    assert bridge.select(r)==refused and len(verified(bridge,refused)['entries'])==1


def test_receipts_and_handoff_match_closed_schema(bridge):
    from jsonschema import Draft202012Validator
    schema=json.loads((ROOT/'apparatus/schemas/menu-store-bridge.schema.json').read_text())
    validator=Draft202012Validator(schema)
    validator.validate(bridge.config)
    r=request(bridge);validator.validate(r);receipt=bridge.select(r);validator.validate(receipt)
    receipt=bridge.select(request(bridge,receipt,atom='K-a1'));validator.validate(receipt)
    validator.validate(verified(bridge,receipt))
    bad=dict(receipt,participant_ready=True)
    assert list(validator.iter_errors(bad))


def test_cli_real_profile_receipt_and_pinned_read(bridge,tmp_path):
    import subprocess
    config=tmp_path/'config.json';config.write_bytes(canonical_json(bridge.config))
    req=tmp_path/'request.json';req.write_bytes(canonical_json(request(bridge)))
    receipt_path=tmp_path/'receipt.json'
    base=[sys.executable,str(ROOT/'tools/menu_store_bridge.py'),'--config',str(config),
          '--config-sha256',hashlib.sha256(config.read_bytes()).hexdigest()]
    result=subprocess.run([*base,'--request',str(req),'--output',str(receipt_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    receipt=json.loads(receipt_path.read_text());assert receipt['status']=='profile_selected'
    manifest_path=tmp_path/'manifest.json'
    result=subprocess.run([*base,'--read-verified','--expected-head',receipt['after_head'],
        '--expected-snapshot-sha256',receipt['after_snapshot_sha256'],'--output',str(manifest_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    manifest=json.loads(manifest_path.read_text());assert manifest['entries']==[] and manifest['profile']=='P1'
    failed=subprocess.run([*base,'--request',str(req),'--output',str(receipt_path)],capture_output=True,text=True)
    assert failed.returncode==1 and 'RECEIPT_EXISTS' in failed.stderr


def test_pending_before_store_write_resumes_identical_request(bridge,monkeypatch):
    receipt=bridge.select(request(bridge));r=request(bridge,receipt,atom='K-a1')
    monkeypatch.setattr(bridge.store,'commit',lambda *a,**k: (_ for _ in ()).throw(OSError('injected before commit')))
    with pytest.raises(OSError):bridge.select(r)
    assert bridge.pending.exists() and len(bridge.store.records(bridge.book_id))==1
    result=MenuStoreBridge(bridge.config).select(r)
    assert result['status']=='committed' and len(bridge.store.records(bridge.book_id))==2


def test_changed_packaged_wave_refused(producer,tmp_path):
    import shutil
    from av_sound.package import PackageIntegrityError
    target=tmp_path/'package';shutil.copytree(producer['package_path'],target)
    path=next((target/'options').rglob('*.wav'))
    data=bytearray(path.read_bytes());data[-1]^=1;path.write_bytes(data)
    bridge=MenuStoreBridge(dict(producer,package_path=str(target),store_root=str(tmp_path/'store')))
    with pytest.raises(PackageIntegrityError):bridge.select(request(bridge))
    assert not bridge.store.log_path(bridge.book_id).exists()


def test_changed_journal_receipt_is_not_trusted(bridge):
    receipt=bridge.select(request(bridge))
    row=json.loads(bridge.journal.read_text());row['receipt']['profile']='P2'
    row['receipt']['receipt_sha256']=digest({k:v for k,v in row['receipt'].items() if k!='receipt_sha256'})
    row['record_sha256']=digest({k:v for k,v in row.items() if k!='record_sha256'})
    bridge.journal.write_bytes(canonical_json(row)+b'\n')
    with pytest.raises(BridgeError,match='RECEIPT_BINDING'):verified(bridge,receipt)


def test_duplicate_receipt_does_not_delete_invalid_pending_intent(bridge):
    r=request(bridge);receipt=bridge.select(r)
    intent=dict(request=r,before_snapshot={},before_head=None,config_sha256=bridge.config_sha256,intent_sha256='0'*64)
    bridge.pending.write_bytes(canonical_json(intent)+b'\n')
    with pytest.raises(BridgeError,match='INTENT_HASH'):bridge.select(r)
    assert bridge.pending.exists() and bridge.store.head(bridge.book_id)==receipt['after_head']


def test_duplicate_receipt_does_not_delete_torn_pending_intent(bridge):
    r=request(bridge);bridge.select(r)
    bridge.pending.write_bytes(b'{"request":')
    with pytest.raises(ValueError):bridge.select(r)
    assert bridge.pending.exists()


def enqueue(mailbox,value):
    (mailbox/'requests').mkdir(parents=True,exist_ok=True)
    path=mailbox/'requests'/(value['request_id']+'.json')
    _atomic_new(path,value)
    return path


def response(mailbox,value):
    result=json.loads((mailbox/'responses'/(value['request_id']+'.json')).read_text())
    assert result['response_sha256']==digest({k:v for k,v in result.items() if k!='response_sha256'})
    assert result['request_sha256']==digest(value) and result['request_id']==value['request_id']
    return result


def test_mailbox_real_profile_atom_verify_and_restart(bridge,tmp_path):
    from jsonschema import Draft202012Validator
    validator=Draft202012Validator(json.loads((ROOT/'apparatus/schemas/menu-store-bridge.schema.json').read_text()))
    mailbox=tmp_path/'mailbox';r=request(bridge);enqueue(mailbox,r)
    assert serve_mailbox(bridge,mailbox,seconds=10,max_requests=1)['processed']==1
    first=response(mailbox,r);validator.validate(first)
    assert first['snapshot']['profile_selection_receipt_sha256']==first['receipt']['receipt_sha256']
    assert first['snapshot']['book_head']==first['receipt']['after_head'] and first['error'] is None
    atom=request(bridge,first['receipt'],atom='K-a1');enqueue(mailbox,atom)
    serve_mailbox(MenuStoreBridge(bridge.config),mailbox,seconds=10,max_requests=1)
    selected=response(mailbox,atom);validator.validate(selected)
    assert selected['snapshot']['entries'][0]['selection_receipt_sha256']==selected['receipt']['receipt_sha256']
    check=dict(request(bridge,selected['receipt']),operation='verify',menu_key='verify')
    before=bridge.journal.read_bytes();enqueue(mailbox,check)
    serve_mailbox(bridge,mailbox,seconds=10,max_requests=1)
    checked=response(mailbox,check);validator.validate(check);validator.validate(checked)
    assert checked['receipt'] is None and checked['snapshot']==selected['snapshot'] and checked['error'] is None
    assert bridge.journal.read_bytes()==before and len(bridge.store.records(bridge.book_id))==2
    assert serve_mailbox(bridge,mailbox,seconds=.02,max_requests=1)['processed']==0


def test_empty_verify_profile_pin_and_no_selection_receipt(bridge,tmp_path):
    mailbox=tmp_path/'mailbox';r=dict(request(bridge),operation='verify',menu_key='verify',profile=None)
    enqueue(mailbox,r);serve_mailbox(bridge,mailbox,seconds=10,max_requests=1)
    got=response(mailbox,r)
    assert got['receipt'] is None and got['snapshot']['profile_selection_receipt_sha256'] is None
    assert got['snapshot']['profile'] is None and not bridge.journal.exists()
    with pytest.raises(BridgeError,match='REQUEST_OPERATION'):bridge.select(r)


def test_verify_rechecks_actual_blob_and_rejects_old_profile_pin(bridge,tmp_path):
    import os,stat
    first=bridge.select(request(bridge));last=bridge.select(request(bridge,first,atom='K-a1'))
    mailbox=tmp_path/'mailbox'
    wrong=dict(request(bridge,last,profile='P2'),operation='verify',menu_key='verify');enqueue(mailbox,wrong)
    serve_mailbox(bridge,mailbox,seconds=10,max_requests=1)
    assert response(mailbox,wrong)['error']=='READ_PROFILE_PIN'
    entry=bridge.store.get(bridge.book_id,'K-a1');blob=bridge.store.blob_path(entry.pcm_sha256)
    os.chmod(blob,stat.S_IWRITE|stat.S_IREAD);data=bytearray(blob.read_bytes());data[-1]^=1;blob.write_bytes(data)
    check=dict(request(bridge,last),operation='verify',menu_key='verify');enqueue(mailbox,check)
    serve_mailbox(bridge,mailbox,seconds=10,max_requests=1)
    failed=response(mailbox,check)
    assert failed['error']=='STORE_INTEGRITY' and failed['receipt'] is None and failed['snapshot'] is None


def test_mailbox_unknown_field_and_wrong_filename_id_fail_closed(bridge,tmp_path):
    mailbox=tmp_path/'mailbox';r=dict(request(bridge),unexpected='not permitted');enqueue(mailbox,r)
    serve_mailbox(bridge,mailbox,seconds=10,max_requests=1)
    assert response(mailbox,r)['error']=='REQUEST_SHAPE'
    r=request(bridge);path=enqueue(mailbox,r);path.rename(path.with_name('0'*32+'.json'))
    serve_mailbox(bridge,mailbox,seconds=10,max_requests=1)
    got=json.loads((mailbox/'responses'/('0'*32+'.json')).read_text())
    assert got['error']=='MAILBOX_REQUEST_ID' and got['receipt'] is None and not bridge.journal.exists()


def test_mailbox_multiple_pending_refuses_without_store_write(bridge,tmp_path):
    mailbox=tmp_path/'mailbox';enqueue(mailbox,request(bridge));enqueue(mailbox,request(bridge))
    with pytest.raises(BridgeError,match='MAILBOX_MULTIPLE_PENDING'):serve_mailbox(bridge,mailbox,seconds=1)
    assert not bridge.journal.exists() and not list((mailbox/'responses').iterdir())


def test_mailbox_preserves_and_checks_existing_response(bridge,tmp_path):
    mailbox=tmp_path/'mailbox';r=request(bridge);enqueue(mailbox,r)
    serve_mailbox(bridge,mailbox,seconds=10,max_requests=1)
    path=mailbox/'responses'/(r['request_id']+'.json');before=path.read_bytes()
    with pytest.raises(FileExistsError):_atomic_new(path,{'replace':True})
    assert path.read_bytes()==before
    result=json.loads(before);result['request_sha256']='0'*64;path.write_bytes(canonical_json(result)+b'\n')
    with pytest.raises(BridgeError,match='MAILBOX_RESPONSE_CHANGED'):serve_mailbox(bridge,mailbox,seconds=1)
    assert len(bridge.store.records(bridge.book_id))==1


@pytest.mark.parametrize('seconds,count',[(0,1),(float('nan'),1),(3601,1),(1,0),(1,True)])
def test_mailbox_bounded_arguments(bridge,tmp_path,seconds,count):
    with pytest.raises(BridgeError,match='MAILBOX_'):serve_mailbox(bridge,tmp_path/'mailbox',seconds=seconds,max_requests=count)


def test_mailbox_second_writer_refused(bridge,tmp_path):
    mailbox=tmp_path/'mailbox';enqueue(mailbox,request(bridge))
    with store_lock(mailbox):
        with pytest.raises(BridgeError,match='STORE_BUSY'):serve_mailbox(bridge,mailbox,seconds=1)
    assert not bridge.journal.exists()


def test_mailbox_cli_empty_bootstrap_then_live_process(bridge,tmp_path):
    import subprocess,time
    config=tmp_path/'config.json';config.write_bytes(canonical_json(bridge.config))
    base=[sys.executable,str(ROOT/'tools/menu_store_bridge.py'),'--config',str(config),
          '--config-sha256',hashlib.sha256(config.read_bytes()).hexdigest()]
    initial=tmp_path/'initial.json'
    read=subprocess.run([*base,'--read-verified','--expected-head','none','--expected-snapshot-sha256',EMPTY_SNAPSHOT,
                         '--output',str(initial)],capture_output=True,text=True)
    assert read.returncode==0,read.stderr
    assert json.loads(initial.read_text())['profile_selection_receipt_sha256'] is None
    mailbox=tmp_path/'mailbox';r=request(bridge);enqueue(mailbox,r)
    process=subprocess.Popen([*base,'--serve-mailbox',str(mailbox),'--seconds','30','--max-requests','2'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        target=mailbox/'responses'/(r['request_id']+'.json');deadline=time.monotonic()+15
        while not target.exists() and time.monotonic()<deadline and process.poll() is None:time.sleep(.02)
        assert target.exists()
        profile=response(mailbox,r)
        atom=request(bridge,profile['receipt'],atom='K-a1');enqueue(mailbox,atom)
        output,error=process.communicate(timeout=15)
        assert process.returncode==0,error
        assert json.loads(output)['processed']==2
        assert response(mailbox,atom)['receipt']['status']=='committed'
    finally:
        if process.poll() is None:process.kill();process.wait()


@pytest.mark.parametrize('mode',['codec','service'])
def test_reproducible_fixture_uses_fresh_store_and_read_only_sources(bridge,tmp_path,mode):
    from tools.menu_store_fixture import prepare_fixture
    source=tmp_path/'source-config.json';source.write_bytes(canonical_json(bridge.config))
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    root=tmp_path/'fixture'
    result=prepare_fixture(source,source_hash,root,mode=mode)
    assert result['mode']==mode and not result['participant_ready'] and not result['timing_qualified']
    assert source_hash==hashlib.sha256(source.read_bytes()).hexdigest()
    assert not bridge.store.log_path(bridge.book_id).exists()
    generated=json.loads((root/'config.json').read_text());assert Path(generated['store_root'])==root/'store'
    initial=json.loads((root/'initial-snapshot.json').read_text())
    assert initial['entries']==[] and initial['manifest_sha256']==result['initial_manifest_sha256']
    assert all(hashlib.sha256((root/name).read_bytes()).hexdigest()==sha for name,sha in result['files'].items())
    if mode=='codec':
        assert json.loads((root/'atom-response.json').read_text())['receipt']['status']=='committed'
    else:assert not list((root/'mailbox/requests').iterdir())
    with pytest.raises(BridgeError,match='FIXTURE_EXISTS'):prepare_fixture(source,source_hash,root,mode=mode)


def test_fixture_bad_source_pin_refuses_before_output(bridge,tmp_path):
    from tools.menu_store_fixture import prepare_fixture
    source=tmp_path/'source-config.json';source.write_bytes(canonical_json(bridge.config))
    with pytest.raises(BridgeError,match='CONFIG_FILE_HASH'):prepare_fixture(source,'0'*64,tmp_path/'out',mode='service')
    assert not (tmp_path/'out').exists()
