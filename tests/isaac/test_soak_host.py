"""Injected clocks/proc facts are synthetic, not resource qualification."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from isaac.soak.host import StepTrace, StepTail, memory_sample, process_identity, sample


def test_step_trace_measures_actual_clock_intervals_and_fsyncs(tmp_path):
    clock=[0];trace=StepTrace(tmp_path/'steps.jsonl',clock=lambda:clock[0])
    for _ in range(10):
        with trace.measure():clock[0]+=10_000_000
        clock[0]+=90_000_000
    trace.flush();trace.close()
    lines=[json.loads(x) for x in (tmp_path/'steps.jsonl').read_bytes().splitlines()]
    assert [r['kind'] for r in lines]==['start','steps','end']
    assert lines[1]['step_count']==10 and lines[1]['max_step_ns']=='10000000'
    assert lines[1]['sum_step_ns']=='100000000'
    row=StepTail(tmp_path/'steps.jsonl',os.getpid()).read(clock[0])
    assert row['total_steps']==10


def test_exception_and_nested_measure_fail_closed(tmp_path):
    clock=[0];trace=StepTrace(tmp_path/'steps.jsonl',clock=lambda:clock[0])
    with pytest.raises(ValueError):
        with trace.measure():
            with trace.measure():pass
    trace.close()
    with pytest.raises(ValueError,match='failure'):StepTail(tmp_path/'steps.jsonl',os.getpid()).read(0)


def test_stale_or_wrong_pid_not_a_zero_step_measurement(tmp_path):
    clock=[0];trace=StepTrace(tmp_path/'steps.jsonl',clock=lambda:clock[0])
    with trace.measure():clock[0]=1_000_000_000
    trace.close()
    with pytest.raises(ValueError,match='PID'):StepTail(tmp_path/'steps.jsonl',999999).read(clock[0])
    with pytest.raises(ValueError,match='stale'):StepTail(tmp_path/'steps.jsonl',os.getpid()).read(5_000_000_000)


def test_proc_identity_handles_spaces_and_parens_in_comm(tmp_path):
    p=tmp_path/'7';p.mkdir();(p/'stat').write_text('7 (a name (with paren)) '+' '.join(['S']+['0']*18+['123']+['0']*5))
    (p/'cmdline').write_bytes(b'/private/python\x00private-script\x00')
    result=process_identity(7,tmp_path)
    assert result['start_ticks']=='123' and set(result)=={'pid','start_ticks','command_sha256'}
    assert 'private' not in str(result)


def test_actual_memory_fields_keep_whole_device_scope(tmp_path):
    (tmp_path/'meminfo').write_text('MemTotal: 8192 kB\nMemAvailable: 4096 kB\n')
    p=tmp_path/'7';p.mkdir();(p/'status').write_text('VmRSS: 1024 kB\n')
    def run(args,**kwargs):
        assert args[0]=='nvidia-smi' and '--id=0' in args and kwargs['timeout']==5
        return SimpleNamespace(stdout='GPU-test, 24, 10\n')
    result=memory_sample(7,0,proc=tmp_path,run=run)
    assert result['ram_mb']==4 and result['ram_capacity_mb']==8
    assert result['process_rss_mb']==1 and result['vram_mb']==10


def test_pid_reuse_stops_sampler_and_preserves_partial(tmp_path):
    clock=[0];trace=StepTrace(tmp_path/'steps.jsonl',clock=lambda:clock[0])
    with trace.measure():clock[0]=1_000_000_000
    trace.flush()
    calls=[0]
    def identity(pid):
        calls[0]+=1
        return {'pid':pid,'start_ticks':'first' if calls[0]<4 else 'reused'}
    def memory(*_):return dict(ram_mb=1,ram_capacity_mb=10,vram_mb=1,vram_capacity_mb=10,process_rss_mb=1,gpu_uuid='test')
    result=sample(os.getpid(),0,tmp_path/'steps.jsonl',tmp_path/'out',10,source_kind='synthetic_diagnostic',clock=lambda:clock[0],
                  sleep=lambda seconds:clock.__setitem__(0,clock[0]+int(seconds*1e9)),identity=identity,memory=memory)
    trace.close()
    assert result['samples']==1 and 'reused' in result['error']
    assert result['g2_signed'] is False and (tmp_path/'out'/'resources.csv').exists()
