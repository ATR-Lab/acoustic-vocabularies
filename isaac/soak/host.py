"""Read-only Linux resource sampler plus explicit simulation-step instrumentation.

The sampler follows one already running PID, rejects PID reuse, and never starts
or stops Isaac, changes its network, or estimates missing simulation durations.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time

from .analyze import load_json, require
from .collect import encoded, write_new
from .native import read_bounded, safe_path


class StepTrace:
    """Use `with trace.measure(): sim.step(...)` on the simulation owner thread.

    Durations are actual wall clock. One-second aggregates are written/fsynced
    outside the measured step and their overhead remains part of overall pacing.
    The trace does not alter physics, targets, GC, neutral checks or thresholds.
    """
    def __init__(self, path, *, clock=time.monotonic_ns):
        self.path = safe_path(path, missing=True)
        self.stream = self.path.open('xb'); self.clock = clock
        self.start = self.interval_start = clock()
        self.total = self.count = self.sum_ns = self.max_ns = self.sequence = 0
        self.failed = self.closed = self.active = False; self.owner_pid = os.getpid(); self.owner_thread=threading.get_ident()
        self._write('start', {'pid': self.owner_pid})

    def _write(self, kind, values):
        row = dict(version=1, seq=self.sequence, kind=kind, host_monotonic_ns=str(self.clock()), **values)
        self.stream.write(encoded(row)); self.stream.flush(); os.fsync(self.stream.fileno()); self.sequence += 1

    @contextmanager
    def measure(self):
        require(not self.closed and not self.failed and not self.active and os.getpid() == self.owner_pid and
                threading.get_ident()==self.owner_thread, 'Step trace inactive/wrong owner')
        self.active=True
        before = self.clock()
        try:
            yield
        except BaseException:
            self.failed = True
            raise
        finally:
            after = self.clock()
            self.active=False
            require(after >= before, 'Step clock regressed')
            elapsed = after-before
            self.count += 1; self.total += 1; self.sum_ns += elapsed; self.max_ns = max(self.max_ns, elapsed)
            if self.failed or after-self.interval_start >= 1_000_000_000:
                self.flush(after)

    def flush(self, now=None):
        now = self.clock() if now is None else now
        if not self.count: return
        self._write('steps', dict(interval_start_ns=str(self.interval_start), interval_end_ns=str(now),
                    step_count=self.count, total_steps=self.total, sum_step_ns=str(self.sum_ns),
                    max_step_ns=str(self.max_ns), step_failed=self.failed))
        self.interval_start=now; self.count=self.sum_ns=self.max_ns=0

    def close(self):
        if self.closed: return
        self.closed=True
        try:
            self.flush(); self._write('end', {'total_steps': self.total, 'step_failed': self.failed})
        finally: self.stream.close()


def process_identity(pid, proc=Path('/proc')):
    require(type(pid) is int and pid > 0, 'Explicit positive PID required')
    raw=(proc/str(pid)/'stat').read_text()
    end=raw.rfind(')')
    require(end >= 0, 'Malformed proc stat')
    fields=raw[end+2:].split()
    require(len(fields) > 19 and fields[19].isdigit(), 'Missing process start ticks')
    command=(proc/str(pid)/'cmdline').read_bytes()
    require(command, 'Empty process command')
    return dict(pid=pid, start_ticks=fields[19], command_sha256=hashlib.sha256(command).hexdigest())


def memory_sample(pid, gpu_index, *, proc=Path('/proc'), run=subprocess.run):
    def kilobytes(path):
        values={}
        for line in path.read_text().splitlines():
            key, _, rest=line.partition(':'); parts=rest.split()
            if len(parts)==2 and parts[1]=='kB': values[key]=int(parts[0])
        return values
    mem=kilobytes(proc/'meminfo'); status=kilobytes(proc/str(pid)/'status')
    require(mem['MemTotal'] > 0 and 0 <= mem['MemAvailable'] <= mem['MemTotal'], 'Host RAM invalid')
    result=run(['nvidia-smi','--id='+str(gpu_index),'--query-gpu=uuid,memory.total,memory.used',
                '--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5,check=True)
    fields=next(csv.reader([result.stdout.strip()]))
    require(len(fields)==3 and '\n' not in result.stdout.strip(), 'One explicit GPU required')
    capacity, used=map(float,fields[1:])
    require(math.isfinite(capacity) and math.isfinite(used) and 0 <= used <= capacity and capacity > 0, 'GPU memory invalid')
    return dict(ram_mb=(mem['MemTotal']-mem['MemAvailable'])/1024, ram_capacity_mb=mem['MemTotal']/1024,
                process_rss_mb=status['VmRSS']/1024, vram_mb=used, vram_capacity_mb=capacity, gpu_uuid=fields[0].strip())


class StepTail:
    def __init__(self, path, pid):
        self.path, self.pid=Path(path),pid
        self.offset=0;self.pending=b'';self.sequence=0;self.total=0;self.latest=None;self.ended=False

    def read(self, now_ns):
        safe_path(self.path)
        with self.path.open('rb') as stream:
            require(os.fstat(stream.fileno()).st_size >= self.offset, 'Step trace truncated')
            stream.seek(self.offset);chunk=stream.read(1024*1024)
        self.offset+=len(chunk);self.pending+=chunk
        while b'\n' in self.pending:
            line,self.pending=self.pending.split(b'\n',1);require(len(line)<=4096,'Step line size')
            r=load_json(line)
            require(r.get('version')==1 and type(r.get('seq')) is int and r['seq']==self.sequence,'Step sequence')
            self.sequence+=1;require(not self.ended,'Rows after step terminal')
            if r['kind']=='start':require(self.sequence==1 and r['pid']==self.pid,'Step PID binding')
            elif r['kind']=='steps':
                require(self.sequence>1 and type(r['step_count']) is int and r['step_count']>0 and
                        r['total_steps']==self.total+r['step_count'] and r['step_failed'] is False,'Step count/failure')
                start,end,total,maximum=map(int,(r['interval_start_ns'],r['interval_end_ns'],r['sum_step_ns'],r['max_step_ns']))
                require(0<=start<=end<=now_ns and 0<=maximum<=total,'Step time invalid')
                self.latest=r;self.total=r['total_steps']
            elif r['kind']=='end':
                require(r['total_steps']==self.total and r['step_failed'] is False,'Step terminal');self.ended=True
            else:raise ValueError('Unknown step event')
        require(len(self.pending)<=4096,'Step pending line size')
        require(self.latest is not None and 0 <= now_ns-int(self.latest['interval_end_ns']) <= 3_000_000_000,'Step measurements absent/stale')
        return self.latest


def sample(pid, gpu_index, steps_path, output, seconds, interval=1., *, source_kind, clock=time.monotonic_ns, sleep=time.sleep,
           identity=process_identity, memory=memory_sample):
    require(type(gpu_index) is int and gpu_index>=0 and 0<seconds<=36000 and .1<=interval<=30,'Sampler bounds')
    require(source_kind in ('isaac_runtime','synthetic_diagnostic'),'Explicit resource source kind required')
    output=safe_path(output,directory=True,missing=True);require(not output.exists(),'Fresh host output required');output.mkdir(parents=True)
    bound=identity(pid);tail=StepTail(steps_path,pid);started=clock();rows=0;error=None
    fields=['t_s','host_monotonic_ns','ram_mb','ram_capacity_mb','vram_mb','vram_capacity_mb',
            'step_ms','process_rss_mb','gpu_uuid','step_trace_seq','step_total']
    try:
        with (output/'resources.csv').open('x',newline='',encoding='utf-8') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
            while True:
                require(identity(pid)==bound,'Source PID reused/changed')
                now=clock();step=tail.read(now);values=memory(pid,gpu_index)
                require(identity(pid)==bound,'Source PID changed during resource read')
                writer.writerow(dict(t_s=(now-started)/1e9,host_monotonic_ns=str(now),**values,
                    step_ms=int(step['max_step_ns'])/1e6,step_trace_seq=step['seq'],step_total=step['total_steps']))
                stream.flush();os.fsync(stream.fileno());rows+=1
                if now-started>=seconds*1e9:break
                require(not tail.ended,'Simulation stopped during resource window')
                sleep(interval)
    except (Exception,KeyboardInterrupt) as caught:error=type(caught).__name__+': '+str(caught)
    result=dict(version=1,scope='read_only_host_resource_capture',source_kind=source_kind,process=bound,requested_seconds=seconds,
                elapsed_seconds=(clock()-started)/1e9,samples=rows,error=error,g2_signed=False,
                ram_scope='whole_host_used_plus_separate_source_rss',vram_scope='whole_selected_device_includes_other_workloads',
                step_scope='maximum_actual_step_duration_in_latest_complete_trace_interval',
                resource_sha256=hashlib.sha256(read_bounded(output/'resources.csv')).hexdigest())
    write_new(output/'host-summary.json',encoded(result));return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pid',type=int,required=True);p.add_argument('--gpu-index',type=int,required=True)
    p.add_argument('--steps',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seconds',type=float,required=True);p.add_argument('--interval',type=float,default=1.)
    p.add_argument('--source-kind',choices=('isaac_runtime','synthetic_diagnostic'),required=True)
    a=p.parse_args();result=sample(a.pid,a.gpu_index,a.steps,a.output,a.seconds,a.interval,source_kind=a.source_kind)
    print(json.dumps(result));return 1 if result['error'] else 0


if __name__=='__main__':raise SystemExit(main())
