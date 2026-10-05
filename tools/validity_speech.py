"""Private validity-speech preparation; no study freeze without a listening review."""
from __future__ import annotations
import argparse
import array
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
import sys
import re
import wave

ACTIONS = ('ADD_ONE','REMOVE_ONE','FLIP_CARD','ALIGN_ARROW','SCAN','TAG','CLOSE','QUARANTINE')
TARGETS = 'ABCDEFGH'
VOICE = dict(name='Microsoft Zira Desktop', id='TTS_MS_EN-US_ZIRA_11.0', culture='en-US', version='11.0', rate=0, volume=100)
RULE = dict(version=1, sample_rate_hz=48000, channels=1, bits=16, trim_threshold_pcm=33,
            edge_padding_samples=480, peak_pcm=23170, rounding='nearest_ties_away_from_zero')
FIELDS = ('speech_id','action','target','text','take','duration_ms','samples','sha256','pcm_sha256','chosen','raw_sha256')

def require(ok, code):
    if not ok: raise ValueError(code)

def digest(data): return hashlib.sha256(data).hexdigest()
def canonical(value): return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode()
def read_json(path):
    def unique(pairs):
        result={}
        for key,value in pairs:
            require(key not in result,'DUPLICATE_JSON_FIELD');result[key]=value
        return result
    return json.loads(path.read_text(encoding='utf-8-sig'),object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))

def private_path(path):
    path=path.absolute()
    require('.local' in path.parts,'PRIVATE_OUTPUT_REQUIRED')
    require(not any(p.is_symlink() or p.exists() and getattr(p.stat(),'st_file_attributes',0)&0x400 for p in (path,*path.parents)), 'PRIVATE_PATH_LINK')
    return path

def create(path, data):
    with path.open('xb') as stream: stream.write(data);stream.flush();os.fsync(stream.fileno())

def records(selection=()):
    result=[]
    for ai,action in enumerate(ACTIONS):
        for target in TARGETS[(ai//4)*4:(ai//4+1)*4]:
            for take in (1,2):
                result.append(dict(speech_id=f'speech-{action.lower()}-{target.lower()}-t{take}',
                                   action=action,target=target,text=f'{action.replace("_"," ")}, {target}.',take=take,
                                   chosen=(f'{"K" if ai<4 else "Q"}-{action}-{target}' in selection and take==1)))
    return result

def selection_from(value):
    require(value['format']=='av-schedules/speech-list' and type(value['format_version']) is int and value['format_version']==1,'SPEECH_LIST_FORMAT')
    require(type(value['demo']) is bool and value['study'] in ('A','B') and value['set'] in ('pilot','confirmatory'),'SPEECH_LIST_SCOPE')
    require(isinstance(value['commands'],list) and len(value['commands'])==8,'SPEECH_LIST_COUNT')
    result=[];targets=set();actions=set()
    for position,row in enumerate(value['commands'],1):
        require(set(row)=={'position','speech_id','family','semantic_action','semantic_referent'},'SPEECH_LIST_FIELDS')
        require(type(row['position']) is int and row['position']==position,'SPEECH_LIST_POSITION')
        action=row['semantic_action'];target=row['semantic_referent']
        require(action in ACTIONS and target in TARGETS and len(target)==1,'SPEECH_LIST_COMMAND')
        ai=ACTIONS.index(action);family='K' if ai<4 else 'Q'
        require((ord(target)-ord('A'))//4==ai//4 and row['family']==family and row['speech_id']==f'{family}-{action}-{target}','SPEECH_LIST_COMMAND')
        require(action not in actions and target not in targets,'SPEECH_LIST_BALANCE')
        result.append(row['speech_id']);actions.add(action);targets.add(target)
    return result

def plan(output,speech_list,expected_hash):
    source=read_json(speech_list);selection=selection_from(source)
    require(digest(speech_list.read_bytes())==expected_hash,'SPEECH_LIST_HASH_MISMATCH')
    output=private_path(output);output.mkdir(parents=True,exist_ok=False)
    value=dict(version=1,status='engineering_unreviewed',voice=VOICE,rule=RULE,requests=records(selection),
               speech_list_sha256=expected_hash,demo=source['demo'],study=source['study'],set=source['set'])
    create(output/'requests.local.json',canonical(value))
    create(output/'selection.local.json',speech_list.read_bytes())
    return dict(requests=len(value['requests']),status=value['status'])

def pcm_read(raw):
    require(44<=len(raw)<=2_000_000,'WAV_SIZE')
    with wave.open(io.BytesIO(raw),'rb') as reader:
        require(reader.getparams()[:3]==(1,2,48000) and reader.getcomptype()=='NONE','WAV_FORMAT')
        pcm=reader.readframes(reader.getnframes())
        require(0<len(pcm)<=960000 and len(pcm)==reader.getnframes()*2,'WAV_LENGTH')
    values=array.array('h');values.frombytes(pcm)
    if sys.byteorder!='little':values.byteswap()
    return list(values)

def normalize(samples):
    active=[i for i,x in enumerate(samples) if abs(x)>RULE['trim_threshold_pcm']]
    require(bool(active),'SPEECH_SILENT')
    start=max(0,active[0]-RULE['edge_padding_samples'])
    end=min(len(samples),active[-1]+1+RULE['edge_padding_samples'])
    values=samples[start:end];peak=max(abs(x) for x in values)
    # Integer rational rounding is independent of platform floating-point rules.
    result=[(1 if x>=0 else -1)*((abs(x)*RULE['peak_pcm']*2+peak)//(2*peak)) for x in values]
    require(max(abs(x) for x in result)==RULE['peak_pcm'],'NORMALIZATION_PEAK')
    return result,dict(first_kept_sample=start,end_exclusive_sample=end,raw_samples=len(samples),raw_peak_pcm=peak)

def wav_bytes(samples):
    data=struct.pack('<'+'h'*len(samples),*samples)
    return struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',len(data)+36,b'WAVE',b'fmt ',16,1,1,48000,96000,2,16,b'data',len(data))+data

def validate_manifest(value, directory):
    require(set(value)=={'version','status','voice','rule','source','items','balanced_list','listening_review_sha256','demo','study','set'},'MANIFEST_FIELDS')
    require(type(value['demo']) is bool and value['study'] in ('A','B') and value['set'] in ('pilot','confirmatory'),'MANIFEST_SCOPE')
    require(value['version']==1 and value['status'] in ('engineering_unreviewed','reviewed_frozen'),'MANIFEST_STATUS')
    require(value['voice']==VOICE and value['rule']==RULE,'SPEECH_PIN_CHANGED')
    require(isinstance(value['source'],dict) and set(value['source'])=={'requests_sha256','synthesis_evidence_sha256','speech_list_sha256'},'SOURCE_FIELDS')
    require(all(isinstance(x,str) and len(x)==64 and set(x)<=set('0123456789abcdef') for x in value['source'].values()),'SOURCE_HASH')
    selection_bytes=(directory/'selection.local.json').read_bytes()
    require(digest(selection_bytes)==value['source']['speech_list_sha256'],'SPEECH_LIST_HASH_MISMATCH')
    selection_value=read_json(directory/'selection.local.json');selection=selection_from(selection_value)
    require(all(value[k]==selection_value[k] for k in ('study','set','demo')),'SELECTION_SCOPE_CHANGED')
    expected={r['speech_id']:r for r in records()}
    require(isinstance(value['items'],list) and len(value['items'])==64,'COMMAND_COUNT')
    ids=set();chosen=[]
    for item in value['items']:
        require(set(item)==set(FIELDS)|{'trim'},'ITEM_FIELDS')
        sid=item['speech_id'];require(sid in expected and sid not in ids,'SPEECH_ID');ids.add(sid)
        for key in ('action','target','text','take'):require(item[key]==expected[sid][key], 'WORDING_OR_SELECTION_CHANGED')
        require(type(item['chosen']) is bool and (not item['chosen'] or item['take']==1),'CHOSEN_TAKE')
        path=directory/(sid+'.wav');require(not path.is_symlink(),'SPEECH_LINK')
        raw=path.read_bytes();samples=pcm_read(raw)
        require(raw==wav_bytes(samples),'NONCANONICAL_WAV')
        require(digest(raw)==item['sha256'] and digest(raw[44:])==item['pcm_sha256'],'HASH_MISMATCH')
        require(item['samples']==len(samples) and item['duration_ms']==len(samples)*1000/48000,'DURATION_MISMATCH')
        require(0<item['duration_ms']<10000 and max(abs(x) for x in samples)==RULE['peak_pcm'],'SPEECH_LEVEL_OR_LENGTH')
        require(set(item['trim'])=={'first_kept_sample','end_exclusive_sample','raw_samples','raw_peak_pcm'},'TRIM_FIELDS')
        if item['chosen']:chosen.append(sid)
    require(value['balanced_list']==chosen and len(chosen)==8,'BALANCED_LIST')
    selected_schedule_ids={f'{"K" if ACTIONS.index(x["action"])<4 else "Q"}-{x["action"]}-{x["target"]}' for x in value['items'] if x['chosen']}
    require(selected_schedule_ids==set(selection),'STORED_SELECTION_MISMATCH')
    selected=[next(x for x in value['items'] if x['speech_id']==sid) for sid in chosen]
    require(len({x['action'] for x in selected})==8 and len({x['target'] for x in selected})==8,'BALANCE')
    require(set(p.name for p in directory.glob('*.wav'))=={sid+'.wav' for sid in ids},'EXTRA_SPEECH_WAV')
    review=value['listening_review_sha256']
    require(review is None if value['status']=='engineering_unreviewed' else isinstance(review,str) and len(review)==64 and set(review)<=set('0123456789abcdef'),'REVIEW_REQUIRED')
    if value['status']=='reviewed_frozen':
        raw=(directory/'listening-review.local.json').read_bytes()
        require(digest(raw)==review,'REVIEW_HASH_MISMATCH')
        original=dict(value,status='engineering_unreviewed',listening_review_sha256=None)
        validate_review(read_json(directory/'listening-review.local.json'),original)

def process(plan_path, raw_dir, output):
    request=read_json(plan_path)
    require(set(request)=={'version','status','voice','rule','requests','speech_list_sha256','demo','study','set'} and request['version']==1 and request['status']=='engineering_unreviewed' and request['voice']==VOICE and request['rule']==RULE,'REQUESTS_CHANGED')
    require(type(request['demo']) is bool and request['study'] in ('A','B') and request['set'] in ('pilot','confirmatory'),'REQUEST_SCOPE')
    selected=[f'{"K" if ACTIONS.index(r["action"])<4 else "Q"}-{r["action"]}-{r["target"]}' for r in request['requests'] if r['chosen']]
    require(request['requests']==records(selected),'REQUEST_WORDING_CHANGED')
    source=read_json(raw_dir/'synthesis.local.json')
    require(source['voice']==VOICE and source['requests_sha256']==digest(plan_path.read_bytes()),'SYNTHESIS_PROVENANCE')
    raw_files=source['files'];require(set(raw_files)=={x['speech_id']+'.wav' for x in records()},'RAW_INVENTORY')
    output=private_path(output);output.mkdir(parents=True,exist_ok=False)
    create(output/'selection.local.json',(plan_path.parent/'selection.local.json').read_bytes())
    items=[]
    for item in request['requests']:
        raw=(raw_dir/(item['speech_id']+'.wav')).read_bytes()
        require(digest(raw)==raw_files[item['speech_id']+'.wav'],'RAW_HASH_MISMATCH')
        samples,trim=normalize(pcm_read(raw));binary=wav_bytes(samples)
        create(output/(item['speech_id']+'.wav'),binary)
        items.append(dict(item,duration_ms=len(samples)*1000/48000,samples=len(samples),sha256=digest(binary),pcm_sha256=digest(binary[44:]),raw_sha256=digest(raw),trim=trim))
    result=dict(version=1,status='engineering_unreviewed',voice=VOICE,rule=RULE,
                demo=request['demo'],study=request['study'],set=request['set'],
                source=dict(requests_sha256=digest(plan_path.read_bytes()),synthesis_evidence_sha256=digest((raw_dir/'synthesis.local.json').read_bytes()),speech_list_sha256=request['speech_list_sha256']),
                items=items,balanced_list=[x['speech_id'] for x in items if x['chosen']],listening_review_sha256=None)
    validate_manifest(result,output)
    data=canonical(result);create(output/'manifest.local.json',data)
    create(output/'manifest.sha256', (digest(data)+'\n').encode())
    buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=FIELDS,lineterminator='\n');writer.writeheader()
    writer.writerows({k:item[k] for k in FIELDS} for item in items)
    create(output/'manifest.local.csv',buffer.getvalue().encode())
    review=dict(version=1,manifest_sha256=digest(data),reviewer_code=None,reviewed_utc=None,
                items=[dict(speech_id=x['speech_id'],sha256=x['sha256'],wording_correct=None,acceptable_clarity=None) for x in items])
    create(output/'listening-review.template.local.json',canonical(review))
    return dict(status=result['status'],files=64,chosen=8,manifest_sha256=digest(data),
                duration_min_ms=min(x['duration_ms'] for x in items),duration_max_ms=max(x['duration_ms'] for x in items),
                distinct_normalized_files=len({x['sha256'] for x in items}))

def validate_review(review, value):
    require(set(review)=={'version','manifest_sha256','reviewer_code','reviewed_utc','items'},'REVIEW_FIELDS')
    require(review['version']==1 and review['manifest_sha256']==digest(canonical(value)),'REVIEW_MANIFEST_MISMATCH')
    require(isinstance(review['reviewer_code'],str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}',review['reviewer_code']),'REVIEWER_CODE_REQUIRED')
    require(isinstance(review['reviewed_utc'],str),'REVIEW_TIME_REQUIRED')
    when=datetime.strptime(review['reviewed_utc'],'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    require(when<=datetime.now(timezone.utc),'REVIEW_TIME_FUTURE')
    expected={x['speech_id']:x['sha256'] for x in value['items']};seen=set()
    require(isinstance(review['items'],list) and len(review['items'])==64,'REVIEW_INCOMPLETE')
    for item in review['items']:
        require(set(item)=={'speech_id','sha256','wording_correct','acceptable_clarity'},'REVIEW_ITEM_FIELDS')
        require(item['speech_id'] in expected and item['speech_id'] not in seen and item['sha256']==expected[item['speech_id']],'REVIEW_FILE_MISMATCH')
        require(item['wording_correct'] is True and item['acceptable_clarity'] is True,'LISTENING_REVIEW_NOT_PASSED')
        seen.add(item['speech_id'])

def freeze(manifest, review_path, output):
    data=manifest.read_bytes();value=read_json(manifest)
    require(data==canonical(value) and value['status']=='engineering_unreviewed','UNREVIEWED_CANONICAL_MANIFEST_REQUIRED')
    validate_manifest(value,manifest.parent);review=read_json(review_path);validate_review(review,value)
    output=private_path(output);output.mkdir(parents=True,exist_ok=False)
    create(output/'selection.local.json',(manifest.parent/'selection.local.json').read_bytes())
    for item in value['items']:
        create(output/(item['speech_id']+'.wav'),(manifest.parent/(item['speech_id']+'.wav')).read_bytes())
    # Preserve the exact reviewed bytes and their binding to the unreviewed bank.
    review_bytes=review_path.read_bytes();create(output/'listening-review.local.json',review_bytes)
    value['status']='reviewed_frozen';value['listening_review_sha256']=digest(review_bytes)
    validate_manifest(value,output);data=canonical(value);create(output/'manifest.local.json',data)
    create(output/'manifest.sha256',(digest(data)+'\n').encode())
    return dict(status=value['status'],manifest_sha256=digest(data),listening_review_sha256=value['listening_review_sha256'])

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('plan');p.add_argument('--output',type=Path,required=True);p.add_argument('--speech-list',type=Path,required=True);p.add_argument('--speech-list-sha256',required=True)
    p=sub.add_parser('process');p.add_argument('--requests',type=Path,required=True);p.add_argument('--raw',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('validate');p.add_argument('--manifest',type=Path,required=True)
    p=sub.add_parser('freeze');p.add_argument('--manifest',type=Path,required=True);p.add_argument('--review',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='plan':result=plan(args.output,args.speech_list,args.speech_list_sha256)
    elif args.command=='process':result=process(args.requests,args.raw,args.output)
    elif args.command=='freeze':result=freeze(args.manifest,args.review,args.output)
    else:
        value=read_json(args.manifest);validate_manifest(value,args.manifest.parent)
        result=dict(status=value['status'],files=len(value['items']),manifest_sha256=digest(args.manifest.read_bytes()))
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
