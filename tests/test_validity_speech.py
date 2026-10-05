import copy
import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('validity_speech',Path(__file__).parents[1]/'tools/validity_speech.py')
speech=importlib.util.module_from_spec(spec);spec.loader.exec_module(speech)
SELECTION=[f'{"K" if i<4 else "Q"}-{a}-{speech.TARGETS[i]}' for i,a in enumerate(speech.ACTIONS)]

def test_commands_match_exact_public_panel_labels_and_balance():
    rows=speech.records(SELECTION)
    assert len(rows)==64 and len({r['speech_id'] for r in rows})==64
    selected=[r for r in rows if r['chosen']]
    assert len(selected)==8 and len({r['action'] for r in selected})==8 and len({r['target'] for r in selected})==8
    panel=(Path(__file__).parents[1]/'unity/Assets/ExperimentApp/Runtime/ResponsePanel/ResponseState.cs').read_text()
    for action in speech.ACTIONS:assert f'"{action}"' in panel
    for row in rows:assert row['text']==row['action'].replace('_',' ')+', '+row['target']+'.'

@pytest.mark.parametrize('values',[[0]*100,[1,-1]*100,[33,-33]*100])
def test_silent_threshold_rejected(values):
    with pytest.raises(ValueError,match='SPEECH_SILENT'):speech.normalize(values)

def test_trim_preserves_fixed_padding_and_digital_peak():
    values=[0]*1000+[100,-200,100]+[0]*1000
    out,meta=speech.normalize(values)
    assert meta==dict(first_kept_sample=520,end_exclusive_sample=1483,raw_samples=2003,raw_peak_pcm=200)
    assert len(out)==963 and out[481]==-23170 and out[480]==11585
    assert speech.pcm_read(speech.wav_bytes(out))==out

def synthetic_bank(root):
    raw=root/'raw';raw.mkdir();plan=root/'plan';plan.mkdir()
    selection=dict(format='av-schedules/speech-list',format_version=1,demo=True,study='A',set='confirmatory',commands=[dict(position=i+1,speech_id=sid,family=sid[0],semantic_action=speech.ACTIONS[i],semantic_referent=speech.TARGETS[i]) for i,sid in enumerate(SELECTION)])
    selection_bytes=speech.canonical(selection);(plan/'selection.local.json').write_bytes(selection_bytes)
    request=dict(version=1,status='engineering_unreviewed',voice=speech.VOICE,rule=speech.RULE,requests=speech.records(SELECTION),demo=True,study='A',set='confirmatory',speech_list_sha256=speech.digest(selection_bytes))
    request_path=plan/'requests.local.json';request_path.write_bytes(speech.canonical(request))
    binary=speech.wav_bytes([0]*600+[50,-100,100,-50]*1000+[0]*600)
    files={r['speech_id']+'.wav':speech.digest(binary) for r in speech.records()}
    for name in files:(raw/name).write_bytes(binary)
    (raw/'synthesis.local.json').write_bytes(speech.canonical(dict(voice=speech.VOICE,requests_sha256=speech.digest(request_path.read_bytes()),files=files)))
    output=root/'.local'/'bank';speech.process(request_path,raw,output)
    return output,speech.read_json(output/'manifest.local.json')

def test_private_bank_hash_duration_and_changed_byte(tmp_path):
    bank,value=synthetic_bank(tmp_path);speech.validate_manifest(value,bank)
    path=bank/(value['items'][0]['speech_id']+'.wav');data=bytearray(path.read_bytes());data[100]^=1;path.write_bytes(data)
    with pytest.raises(ValueError,match='HASH_MISMATCH'):speech.validate_manifest(value,bank)

def test_unreviewed_template_cannot_freeze(tmp_path):
    bank,_=synthetic_bank(tmp_path)
    with pytest.raises(ValueError,match='REVIEWER_CODE_REQUIRED'):
        speech.freeze(bank/'manifest.local.json',bank/'listening-review.template.local.json',tmp_path/'.local'/'frozen')
    assert not (tmp_path/'.local'/'frozen').exists()

def test_synthetic_review_binding_and_no_overwrite(tmp_path):
    bank,value=synthetic_bank(tmp_path);review=speech.read_json(bank/'listening-review.template.local.json')
    review['reviewer_code']='SYNTHETIC_TEST';review['reviewed_utc']='2026-01-01T00:00:00Z'
    for item in review['items']:item['wording_correct']=True;item['acceptable_clarity']=True
    bad=copy.deepcopy(review);bad['items'][0]['sha256']='0'*64
    with pytest.raises(ValueError,match='REVIEW_FILE_MISMATCH'):speech.validate_review(bad,value)
    path=tmp_path/'synthetic-review.local.json';path.write_bytes(speech.canonical(review))
    output=tmp_path/'.local'/'frozen';result=speech.freeze(bank/'manifest.local.json',path,output)
    assert result['status']=='reviewed_frozen'
    with pytest.raises(FileExistsError):speech.freeze(bank/'manifest.local.json',path,output)

def test_private_output_and_duplicate_json_guards(tmp_path):
    with pytest.raises(ValueError,match='PRIVATE_OUTPUT_REQUIRED'):speech.private_path(tmp_path/'public')
    path=tmp_path/'duplicate.json';path.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError,match='DUPLICATE_JSON_FIELD'):speech.read_json(path)
