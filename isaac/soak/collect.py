"""Bounded observer-only multi-station native capture and causal clock driver.

Run after the real joined application and isolated source have been provisioned.
This process does not create schedule authority, drive participant responses,
reset a robot, inject faults, or automatically resume an exposure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from .analyze import load_json, require
from .native import ID, SHA, NativeReader, MAX_BYTES, plan_bytes, read_bounded, receiver_events, receiver_event, safe_path


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def write_new(path, content):
    safe_path(path, missing=True)
    with Path(path).open('xb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def load_plan(path, pin):
    raw = read_bounded(path, 65536)
    require(isinstance(pin, str) and SHA.fullmatch(pin) and hashlib.sha256(raw).hexdigest() == pin, 'Collector plan pin mismatch')
    p = load_json(raw)
    require(isinstance(p, dict) and set(p) == {'version', 'scope', 'participants', 'seconds', 'stations'}, 'Collector fields')
    require(type(p['version']) is int and p['version'] == 1 and p['scope'] == 'synthetic_nonstudy' and
            p['participants'] is False and type(p['seconds']) is int and 1 <= p['seconds'] <= 35000, 'Collector scope/duration')
    require(isinstance(p['stations'], list) and 1 <= len(p['stations']) <= 64, 'Station inventory')
    root = Path(path).absolute().parent
    seen, paths = set(), set()
    for station in p['stations']:
        require(isinstance(station, dict) and set(station) == {'station_id', 'plan', 'capture_directory', 'source_logs'}, 'Station fields')
        sid = station['station_id']
        require(isinstance(sid, str) and ID.fullmatch(sid) and sid.casefold() not in seen, 'Station identity duplicate/invalid')
        seen.add(sid.casefold())
        ref = station['plan']
        require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'} and isinstance(ref['path'], str), 'Station plan reference')
        ref['path'] = str(safe_path(root / ref['path']))
        station['loaded_plan'] = plan_bytes(read_bounded(ref['path'], 16384), ref['sha256'])
        require(station['loaded_plan']['station_id'] == sid and
                station['loaded_plan']['seconds'] >= p['seconds'] + 5, 'Station plan lacks matching identity/duration margin')
        require(isinstance(station['capture_directory'], str), 'Capture directory')
        capture = safe_path(root / station['capture_directory'], directory=True, missing=True)
        require(str(capture).casefold() not in paths, 'Duplicate capture directory')
        paths.add(str(capture).casefold())
        station['capture_directory'] = str(capture)
        logs = station['source_logs']
        require(isinstance(logs, dict) and set(logs) == {'publisher', 'command', 'host'}, 'Native source roles')
        for role, value in logs.items():
            require(isinstance(value, str) and value, 'Native source path')
            logs[role] = str(safe_path(root / value, missing=True))
        require(len({x.casefold() for x in logs.values()}) == 3, 'Source roles must be distinct')
    return p, raw


class CoordinatorJournal:
    def __init__(self, path, clock_id, clock=time.monotonic_ns):
        self.stream = Path(path).open('xb')
        self.clock, self.clock_id = clock, clock_id
        self.sequence, self.previous = 0, '0' * 64

    def write(self, kind, payload):
        row = dict(version=1, seq=self.sequence, clock_domain='coordinator_monotonic_ns',
                   clock_id=self.clock_id, monotonic_ns=str(self.clock()), kind=kind,
                   payload=payload, previous_sha256=self.previous)
        digest = hashlib.sha256(encoded(row)).hexdigest()
        row['sha256'] = digest
        self.stream.write(encoded(row)); self.stream.flush(); os.fsync(self.stream.fileno())
        self.sequence += 1; self.previous = digest

    def close(self):
        self.stream.close()


class Capture:
    def __init__(self, station, clock_id, journal, clock=time.monotonic_ns, retention_directory=None):
        self.station, self.directory = station, Path(station['capture_directory'])
        self.clock_id, self.journal, self.clock = clock_id, journal, clock
        self.reader = NativeReader(station['loaded_plan'], station['plan']['sha256'],retain_rows=retention_directory is None)
        self.retention_directory=retention_directory;self.event_stream=None;self.event_sequence=0
        if retention_directory is not None:
            retention_directory.mkdir();self.event_stream=(retention_directory/'receiver-events.jsonl').open('xb')
        self.offset = 0; self.hasher = hashlib.sha256()
        self.lock_token = uuid.uuid4().hex
        self.owned_lock = False; self.pending = None; self.windows = {}; self.probe_written = False

    def acquire(self):
        safe_path(self.directory, directory=True)
        write_new(self.directory / 'soak-collector.lock', self.lock_token.encode())
        self.owned_lock = True
        require(not (self.directory / 'coordinator-probe.json').exists(), 'Existing coordinator probe requires operator review')

    def poll(self):
        path = safe_path(self.directory / 'soak-native.jsonl')
        with path.open('rb') as stream:
            require(os.fstat(stream.fileno()).st_size >= self.offset, 'Native file truncated during capture')
            stream.seek(self.offset)
            chunk = stream.read(min(MAX_BYTES - self.offset + 1, 1024 * 1024))
        self.offset += len(chunk); self.hasher.update(chunk)
        fresh = self.reader.feed(chunk)
        for row in fresh:
            if self.event_stream is not None:
                event=receiver_event(row,self.event_sequence)
                if event is not None:self.event_stream.write(encoded(event));self.event_sequence+=1
            if row['kind'] == 'coordinator_probe' and self.pending:
                phase, probe = self.pending
                if row['payload'].get('request_id') != probe['request_id']:
                    continue
                require(row['payload'] == probe, 'Causal probe binding differs')
                received = self.clock()
                sent = int(probe['sent_ns'])
                require(received >= sent, 'Coordinator clock regressed')
                window = dict(request_id=probe['request_id'], sent_ns=str(sent), received_ns=str(received),
                              native_seq=row['seq'], native_t_s=row['t_s'], native_hash=row['sha256'])
                self.windows[phase] = window
                self.journal.write('probe_received', dict(station_id=self.station['station_id'], phase=phase, **window))
                self.pending = None
        if fresh and self.event_stream is not None:self.event_stream.flush();os.fsync(self.event_stream.fileno())
        return fresh

    def probe(self, phase):
        require(phase in ('start', 'end') and phase not in self.windows and self.pending is None, 'Probe lifecycle')
        require(self.reader.rows and self.reader.rows[-1]['kind'] != 'session_end', 'Probe outside active capture')
        now = self.clock()
        probe = dict(version=1, request_id=uuid.uuid4().hex, coordinator_clock_id=self.clock_id, sent_ns=str(now))
        # Durable intent precedes publication. An uncertain timeout never retries
        # or overwrites its evidence as though the first request did not exist.
        self.journal.write('probe_sent', dict(station_id=self.station['station_id'], phase=phase, **probe))
        temp = self.directory / (probe['request_id'] + '.tmp')
        write_new(temp, encoded(probe))
        target = safe_path(self.directory / 'coordinator-probe.json', missing=True)
        os.replace(temp, target)
        self.pending = (phase, probe); self.probe_written = True

    def retain(self, output):
        if self.retention_directory is None:output.mkdir()
        else:require(output==self.retention_directory,'Retention directory changed')
        plan_raw = read_bounded(self.station['plan']['path'], 16384)
        plan_bytes(plan_raw, self.station['plan']['sha256'])
        write_new(output / 'station-plan.json', plan_raw)
        native = read_bounded(self.directory / 'soak-native.jsonl')
        # Re-read all retained bytes, detecting a same-size prefix rewrite that
        # would not be visible to an incremental tail alone.
        require(len(native) == self.offset and hashlib.sha256(native).digest() == self.hasher.digest(), 'Native prefix changed or collection raced an append')
        write_new(output / 'soak-native.jsonl', native)
        result = dict(station_id=self.station['station_id'], causal_windows=self.windows,
                      unity_sha256=hashlib.sha256(native).hexdigest())
        try:
            result['receiver'] = self.reader.finish()
        except (ValueError, TypeError, KeyError) as error:
            result['receiver_error'] = str(error)
        if self.event_stream is None:
            write_new(output / 'receiver-events.jsonl', b''.join(encoded(r) for r in receiver_events(self.reader)))
        else:self.event_stream.flush();os.fsync(self.event_stream.fileno())
        result['receiver_events_sha256']=hashlib.sha256(read_bounded(output/'receiver-events.jsonl')).hexdigest()
        result['source_logs'] = {}
        for role, path in self.station['source_logs'].items():
            try:
                content = read_bounded(path)
                require(content, 'Empty native source')
                name = role + '.native'
                write_new(output / name, content)
                result['source_logs'][role] = dict(path=name, sha256=hashlib.sha256(content).hexdigest(),
                                                 bytes=len(content), terminal_verified=False)
            except (OSError, ValueError) as error:
                result['source_logs'][role] = dict(error=str(error))
        return result

    def close(self):
        error=None
        try:
            if self.event_stream is not None:self.event_stream.close()
        except Exception as caught:error=caught
        finally:self.event_stream=None
        try:
            if self.owned_lock:
                lock = self.directory / 'soak-collector.lock'
                require(read_bounded(lock, 128) == self.lock_token.encode(), 'Collector lock ownership changed')
                # Retain the probe. A new run requires a fresh capture directory.
                lock.unlink(); self.owned_lock = False
        except Exception as caught:error=error or caught
        if error:raise error


def causal_overlap(results):
    require(results and all(set(r['causal_windows']) == {'start', 'end'} for r in results), 'Incomplete causal windows')
    start = max(int(r['causal_windows']['start']['received_ns']) for r in results)
    end = min(int(r['causal_windows']['end']['sent_ns']) for r in results)
    require(end >= start, 'Causal windows do not overlap')
    # Receive(start) is after Unity processed its starting marker; send(end)
    # is before Unity processed its ending marker. No epoch offset is assumed.
    return dict(start_ns=str(start), end_ns=str(end), seconds=(end-start)/1e9)


def collect(plan, raw, output, *, startup_timeout=120, probe_timeout=5, clock=time.monotonic_ns, sleep=time.sleep):
    require(0 < startup_timeout <= 300 and 0 < probe_timeout <= 30, 'Collector timeout range')
    output = safe_path(output, directory=True, missing=True)
    require(not output.exists(), 'Fresh collection output required')
    for station in plan['stations']:
        for value in [station['capture_directory'], station['plan']['path'], *station['source_logs'].values()]:
            source = Path(value)
            require(not output.is_relative_to(source) and not source.is_relative_to(output), 'Output overlaps native source')
    output.mkdir(parents=True)
    write_new(output / 'collector-plan.json', raw)
    cid = 'collector-' + uuid.uuid4().hex
    journal = CoordinatorJournal(output / 'coordinator.jsonl', cid, clock)
    captures = []
    error = None; results = []; started = clock()
    try:
        journal.write('collection_start', {'plan_sha256': hashlib.sha256(raw).hexdigest(), 'seconds': plan['seconds']})
        for station in plan['stations']:
            captures.append(Capture(station,cid,journal,clock,output/station['station_id']))
        deadline = started + int(startup_timeout * 1e9)
        waiting = list(captures)
        while waiting:
            for capture in waiting[:]:
                if (capture.directory / 'soak-native.jsonl').exists():
                    capture.acquire(); capture.poll(); capture.probe('start'); waiting.remove(capture)
            for capture in captures:
                if capture.owned_lock:
                    capture.poll()
            require(clock() <= deadline, 'Native capture startup timeout')
            if waiting: sleep(.1)
        while any('start' not in c.windows for c in captures):
            for capture in captures:
                capture.poll()
                require(capture.pending is None or clock()-int(capture.pending[1]['sent_ns']) <= probe_timeout*1e9, 'Start probe timeout')
            sleep(.05)
        coverage_start = max(int(c.windows['start']['received_ns']) for c in captures)
        coverage_end = coverage_start + plan['seconds'] * 1_000_000_000
        while clock() < coverage_end:
            for capture in captures:
                capture.poll()
                require(capture.reader.rows[-1]['kind'] != 'session_end', 'Receiver ended before requested shared coverage')
            sleep(.1)
        for capture in captures:
            capture.probe('end')
        # Wait for genuine native completion, bounded by each declared monitor
        # duration and startup allowance. No command makes it complete sooner.
        terminal_deadline = started + int((startup_timeout + max(c.station['loaded_plan']['seconds'] for c in captures) + 10) * 1e9)
        while True:
            for capture in captures:
                capture.poll()
                require(capture.pending is None or clock()-int(capture.pending[1]['sent_ns']) <= probe_timeout*1e9, 'End probe timeout')
            if all(c.reader.rows[-1]['kind'] == 'session_end' and 'end' in c.windows for c in captures):
                break
            require(clock() <= terminal_deadline, 'Native terminal timeout')
            sleep(.1)
    except (Exception, KeyboardInterrupt) as caught:
        error = type(caught).__name__ + ': ' + str(caught)
    finally:
        for capture in captures:
            try:
                if capture.owned_lock:
                    capture.poll()
                    results.append(capture.retain(output / capture.station['station_id']))
            except Exception as caught:
                results.append(dict(station_id=capture.station['station_id'], retention_error=str(caught)))
                error = error or 'Native retention failed'
            finally:
                try: capture.close()
                except Exception as caught: error = error or 'Cleanup failed: ' + str(caught)
        try:
            overlap = causal_overlap(results)
        except (ValueError, KeyError) as caught:
            overlap = {'error': str(caught)}
        if any('receiver_error' in r or not r.get('receiver',{}).get('receiver_window_complete') or
               any('error' in v for v in r.get('source_logs',{}).values()) for r in results):error=error or 'Incomplete native/source coverage'
        summary = dict(version=1, scope='native_collection_only', coordinator_clock_id=cid,
                       collector_plan_sha256=hashlib.sha256(raw).hexdigest(),
                       requested_seconds=plan['seconds'], collection_error=error, stations=results,
                       shared_causal_window=overlap, g2_signed=False, recommendation='NO_GO',
                       limitations=['Receiver events do not prove command acknowledgements, trial/reset order or audible-cue retention',
                                    'Publisher/command/host files are retained snapshots; terminal and semantic joins remain required',
                                    'This observer never creates reviewed schedules, drives dummy responses, injects faults or resumes exposure'])
        try:journal.write('collection_end', summary)
        except Exception as caught:summary['collection_error']=summary['collection_error'] or 'Coordinator terminal failed: '+str(caught)
        finally:
            try:journal.close()
            except Exception as caught:summary['collection_error']=summary['collection_error'] or 'Coordinator close failed: '+str(caught)
        summary['coordinator_sha256']=hashlib.sha256(read_bounded(output/'coordinator.jsonl')).hexdigest()
        write_new(output / 'collection-summary.json', encoded(summary))
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('plan', type=Path); p.add_argument('--sha256', required=True)
    p.add_argument('--output', type=Path); p.add_argument('--validate-only', action='store_true')
    p.add_argument('--startup-timeout', type=float, default=120)
    args = p.parse_args()
    plan, raw = load_plan(args.plan, args.sha256)
    if args.validate_only:
        print('Pinned nonstudy collector plan valid; no process or authority started.')
        return 0
    require(args.output is not None, 'Output required')
    result = collect(plan, raw, args.output, startup_timeout=args.startup_timeout)
    print(json.dumps(dict(collection_error=result['collection_error'], recommendation='NO_GO', g2_signed=False)))
    return 1 if result['collection_error'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
