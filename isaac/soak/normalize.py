"""Join exact Unity receipts with command, driver and durable data records.

Every emitted fact is placed at the time of a verified native Unity soak row and
is justified by an independent record: a reset or lock-probe receipt must match
the driver's verbatim reply, its Unity input-feed row and exactly one durable
Isaac command terminal event; trial, response, pause, resume and cue facts must
match a hash-verified durable data-journal record. Unknown, duplicated,
unmatched or failing-but-unplaceable records are refused instead of omitted.

The output is the analyzer's normalized event stream. This module never
decides an outcome; ``isaac.soak.analyze`` still does.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from isaac.commands.protocol import decode
from isaac.reset.snapshot import canonical_bytes
from tools.mock_visit.records import SESSION_FIELDS, verify_chain
from .analyze import FAULT_TYPES, load_json, require
from .collect import encoded
from .driver import FEED_FIELDS, JOURNAL_FIELDS, JOURNAL_LIMIT, REPLY_KEYS, load_schedule, read_chain, sha
from .native import GUID, SHA, NativeReader, plan_bytes, read_bounded, receiver_event, safe_path

COMMAND_ENVELOPE = frozenset(('schema_version', 'session_id', 'apparatus_version', 'protocol_version', 'clock_id',
                              'event_type', 'event_seq', 'host_mono_ms', 'sim_time', 'payload'))
COMMAND_PAYLOAD = frozenset(('station_id', 'mode', 'client', 'raw_command', 'command', 'arguments', 'reply'))
RECEIVER_KINDS = ('session_start', 'session_end', 'heartbeat', 'stale_gap', 'frame_freeze')
IGNORED_KINDS = ('coordinator_probe', 'source_restart', 'stale_gap_started', 'frame_freeze_started')
SESSION_KEYS = frozenset(SESSION_FIELDS.split())
OBSERVATIONS = {
    'reset_receipt': {'input_seq', 'request_id', 'reply_sha256'},
    'lock_probe_receipt': {'input_seq', 'request_id', 'reply_sha256'},
    'fault_injection': {'input_seq', 'fault_id', 'last_committed_data_sha256'},
    'durable_record': {'data_event_id', 'data_sha256', 'reset_request_id'},
    'cue_observation': {'data_event_id', 'data_sha256'},
    'operator_resume': {'fault_id', 'data_event_id', 'data_sha256', 'reset_request_id', 'last_committed_data_sha256'},
}


def is_reset(event):
    p = event['payload']
    return p['command'] in ('reset', 'hold_neutral') or (p['command'] == 'set_mode' and p['arguments'] == {'mode': 'test'})


def command_events(logs, station_id):
    """Strictly index durable private command terminal events by request ID."""
    result = {}
    for raw in logs:
        require(isinstance(raw, bytes) and raw.endswith(b'\n') and len(raw) <= JOURNAL_LIMIT, 'Command log bytes')
        session = None
        for index, line in enumerate(raw.splitlines()):
            event = load_json(line)
            require(isinstance(event, dict) and set(event) == COMMAND_ENVELOPE, 'Command event envelope')
            require(event['schema_version'] == '0.3.1' and event['event_type'] == 'private_command_result' and
                    type(event['event_seq']) is int and event['event_seq'] == index, 'Command event order')
            require(isinstance(event['session_id'], str) and GUID.fullmatch(event['session_id']) and
                    session in (None, event['session_id']), 'Command log session')
            session = event['session_id']
            payload = event['payload']
            require(isinstance(payload, dict) and set(payload) == COMMAND_PAYLOAD and
                    payload['station_id'] == station_id, 'Command event payload')
            reply = payload['reply']
            require(isinstance(reply, dict) and set(reply) == REPLY_KEYS and reply['kind'] == 'private_reply',
                    'Command event reply')
            # A target-bearing command accepted in test mode is a lock failure
            # that no receipt can excuse; refuse rather than risk omitting it.
            require(not (payload['command'] == 'demo' and reply['accepted'] and
                         (reply['mode'] == 'test' or payload['mode'] == 'test')), 'Accepted protected demo in command log')
            identifier = reply['request_id']
            if identifier is None:
                continue
            require(identifier not in result, 'Duplicate command terminal for one request')
            result[identifier] = event
    return result


def driver_records(raw, schedule_sha256, commands):
    rows = read_chain(raw, frozenset(JOURNAL_FIELDS))
    require(rows and rows[0]['kind'] == 'driver_start', 'Driver journal start')
    require(all(r['payload']['schedule_sha256'] == schedule_sha256 for r in rows if r['kind'] == 'driver_start'),
            'Driver schedule binding')
    intents, replies, faults = {}, {}, {}
    for row in rows:
        p = row['payload']
        if row['kind'] == 'command_intent':
            require(p['request_id'] not in intents, 'Driver reused a request ID')
            intents[p['request_id']] = p
        elif row['kind'] == 'command_reply':
            intent = intents.get(p['request_id'])
            require(intent is not None and p['request_id'] not in replies, 'Driver reply without intent')
            event = commands.get(p['request_id'])
            require(event is not None, 'Driver reply absent from durable command log')
            require(event['payload']['raw_command'] == intent['request_utf8'] and
                    sha(p['reply_utf8'].encode('utf-8')) == p['reply_sha256'] and
                    canonical_bytes(event['payload']['reply']) == canonical_bytes(decode(p['reply_utf8'])),
                    'Driver exchange differs from durable command record')
            replies[p['request_id']] = p
        elif row['kind'] == 'fault_intent':
            require(p['fault_type'] in FAULT_TYPES and p['fault_id'] not in faults, 'Driver fault intent')
            faults[p['fault_id']] = p
    return replies, faults


def data_records(files, station_id):
    rows = verify_chain(files, 'data') if files else []
    result = {}
    for row in rows:
        require(row['identity']['station_id'] == station_id, 'Data record station')
        result[row['event_id']] = row
    return result


def normalize(*, plan_raw, plan_sha256, native_raw, schedule_raw, command_logs, driver_raw, feed_raw, data_files):
    plan = plan_bytes(plan_raw, plan_sha256)
    schedule_sha256 = sha(schedule_raw)
    require(plan['schedule_sha256'] == schedule_sha256, 'Station plan does not pin this schedule')
    schedule = load_schedule(schedule_raw, schedule_sha256)
    station = plan['station_id']
    require(schedule['station_id'] == station, 'Schedule station differs')
    reader = NativeReader(plan, plan_sha256)
    for line in native_raw.splitlines(keepends=True):
        reader.feed(line)
    receiver = reader.finish()
    commands = command_events(command_logs, station)
    replies, driver_faults = driver_records(driver_raw, schedule_sha256, commands)
    feed = read_chain(feed_raw, frozenset(FEED_FIELDS))
    require(all(r['schedule_sha256'] == schedule_sha256 and r['station_id'] == station for r in feed),
            'Unity input feed binding')
    data = data_records(data_files, station)

    events, receipts, used_data = [], set(), set()
    resets, faults = {}, {}
    context, active, committed = None, None, None
    counts = dict(nonfault_pauses=0)

    def emit(row, kind, **fields):
        events.append(dict(seq=len(events), t_s=row['t_s'], kind=kind, source_kind='live',
                           clock_domain='unity_monotonic', native_seq=row['seq'], **fields))

    def acknowledgement(p, ops):
        seq, identifier = p['input_seq'], p['request_id']
        require(type(seq) is int and 0 <= seq < len(feed), 'Receipt input sequence')
        entry = feed[seq]
        require(entry['kind'] == 'command_ack' and entry['payload']['request_id'] == identifier and
                entry['payload']['reply_sha256'] == p['reply_sha256'], 'Receipt differs from Unity input')
        require(identifier not in receipts, 'Duplicate Unity receipt')
        receipts.add(identifier)
        exchange = replies.get(identifier)
        require(exchange is not None and exchange['reply_sha256'] == p['reply_sha256'] and exchange['op'] in ops,
                'Receipt has no matching driver exchange')
        return exchange, commands[identifier]

    def record(p, events_allowed):
        identifier = p['data_event_id']
        row = data.get(identifier)
        require(row is not None and row['sha256'] == p['data_sha256'] and identifier not in used_data,
                'Observation lacks its durable data record')
        used_data.add(identifier)
        payload = row['payload']
        require(row['event_type'] == 'session' and isinstance(payload, dict) and set(payload) == SESSION_KEYS and
                payload['schedule_sha256'] == schedule_sha256, 'Durable data record is not a bound session event')
        require(payload['event'] in events_allowed, 'Unsupported durable data event for this observation')
        return payload

    for row in reader.rows:
        kind, p = row['kind'], row['payload']
        if kind in RECEIVER_KINDS:
            mapped = receiver_event(row, len(events))
            events.append(mapped)
            continue
        if kind in IGNORED_KINDS:
            continue
        if kind == 'context':
            require(set(p) == {'block', 'block_id', 'engine_state', 'trial_id'}, 'Context fields')
            context = p
            continue
        require(kind in OBSERVATIONS and set(p) == OBSERVATIONS[kind], 'Unknown or malformed native observation')
        if kind == 'reset_receipt':
            exchange, event = acknowledgement(p, ('reset', 'set_mode'))
            require(is_reset(event), 'Receipt is not a reset acknowledgement')
            reply = event['payload']['reply']
            require(type(reply['reset_ok']) is bool, 'Reset acknowledgement lacks reset_ok')
            declared = exchange['fault_id']
            require(declared is None or declared == active, 'Recovery reset outside its Unity-observed fault')
            fields = dict(reset_id=p['request_id'], reset_ok=reply['reset_ok'])
            if active is not None:
                fields['fault_id'] = active
            resets[p['request_id']] = reply['reset_ok']
            emit(row, 'reset', **fields)
        elif kind == 'lock_probe_receipt':
            _, event = acknowledgement(p, ('lock_probe',))
            require(context is not None and context['block'] == 'protected', 'Lock probe received outside protected context')
            reply = event['payload']['reply']
            rejected = (reply['accepted'] is False and reply['reason'] == 'PROTECTED_TARGET_COMMAND' and
                        reply['mode'] == 'test')
            emit(row, 'lock_probe', block='protected', block_id=context['block_id'], rejected=rejected, logged=True)
        elif kind == 'fault_injection':
            seq = p['input_seq']
            require(type(seq) is int and 0 <= seq < len(feed) and feed[seq]['kind'] == 'fault_marker' and
                    feed[seq]['payload']['fault_id'] == p['fault_id'], 'Fault observation differs from Unity input')
            intent = driver_faults.get(p['fault_id'])
            require(intent is not None and p['fault_id'] not in faults and active is None,
                    'Fault observation lacks a unique driver fault intent')
            require(isinstance(p['last_committed_data_sha256'], str) and SHA.fullmatch(p['last_committed_data_sha256']),
                    'Fault retention baseline')
            faults[p['fault_id']] = dict(paused=False)
            active = p['fault_id']
            emit(row, 'fault', fault_id=active, fault_type=intent['fault_type'],
                 last_committed_sha256=p['last_committed_data_sha256'])
        elif kind == 'durable_record':
            payload = record(p, ('response', 'state_before', 'session_paused'))
            if payload['event'] == 'response':
                require(p['reset_request_id'] is None, 'Response record has no reset binding')
                committed = p['data_sha256']
                emit(row, 'record_commit', sha256=committed)
            elif payload['event'] == 'state_before':
                require(payload['trial_id'] is not None and resets.get(p['reset_request_id']) is not None,
                        'Trial lacks a Unity-placed reset acknowledgement')
                emit(row, 'trial_begin', reset_id=p['reset_request_id'])
            else:
                require(p['reset_request_id'] is None, 'Pause record has no reset binding')
                if active is not None and not faults[active]['paused']:
                    faults[active]['paused'] = True
                    emit(row, 'pause', fault_id=active)
                else:
                    counts['nonfault_pauses'] += 1
        elif kind == 'cue_observation':
            payload = record(p, ('onset_evidence',))
            require(payload['trial_id'] is not None, 'Cue record lacks a trial')
            emit(row, 'exposure')
            emit(row, 'cue_playback', cue_id=payload['retry_of'] or payload['trial_id'],
                 audible=payload['exposure_consumed'] is True or payload['audible_status'] == 'ConfirmedAudible',
                 treated_as_unheard=payload['retry_of'] is not None)
        else:
            record(p, ('operator_resume',))
            require(p['fault_id'] == active and resets.get(p['reset_request_id']) is True, 'Resume lacks its active fault and reset')
            require(isinstance(p['last_committed_data_sha256'], str) and SHA.fullmatch(p['last_committed_data_sha256']),
                    'Resume retention baseline')
            emit(row, 'resume', fault_id=active, reset_id=p['reset_request_id'],
                 last_committed_sha256=p['last_committed_data_sha256'], operator_initiated=True)
            active = None

    # Completeness: a failure that cannot be placed is refused, never dropped.
    unplaced_resets = unplaced_probes = 0
    for identifier, exchange in replies.items():
        if identifier in receipts:
            continue
        reply = commands[identifier]['payload']['reply']
        if exchange['op'] == 'lock_probe':
            require(exchange['outcome'] == 'expected', 'Failing lock probe has no Unity receipt')
            unplaced_probes += 1
        elif is_reset(commands[identifier]):
            require(reply['reset_ok'] is True, 'Failed reset has no Unity receipt')
            unplaced_resets += 1
    for identifier, event in commands.items():
        if is_reset(event) and event['payload']['reply']['reset_ok'] is False:
            require(identifier in receipts, 'Failed reset has no Unity receipt')
    require(set(driver_faults) <= set(faults), 'Injected fault absent from the Unity journal')
    summary = dict(version=1, kind='soak_normalization', station_id=station, plan_sha256=plan_sha256,
                   schedule_sha256=schedule_sha256, unity_sha256=sha(native_raw),
                   command_log_sha256=[sha(x) for x in command_logs], driver_journal_sha256=sha(driver_raw),
                   unity_inputs_sha256=sha(feed_raw), data_journal_sha256=[sha(raw) for _, raw in data_files],
                   receiver=receiver, events=len(events),
                   counts=dict(counts, resets=sum(1 for e in events if e['kind'] == 'reset'),
                               lock_probes=sum(1 for e in events if e['kind'] == 'lock_probe'),
                               trials=sum(1 for e in events if e['kind'] == 'trial_begin'),
                               faults=len(faults), unplaced_successful_resets=unplaced_resets,
                               unplaced_rejected_lock_probes=unplaced_probes,
                               driver_exchanges=len(replies), command_terminals=len(commands)),
                   recommendation='NO_GO', g2_signed=False, analysis_required=True,
                   limitations=['Normalization verifies joins and hashes; the analyzer alone evaluates criteria',
                                'Cue identity is the original trial of a retry; audible means consumed exposure or confirmed audible',
                                'Unity-originated control-client resets are not placed without a driver input-feed binding'])
    return events, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--schedule', type=Path, required=True)
    parser.add_argument('--command-log', type=Path, action='append', required=True)
    parser.add_argument('--driver-journal', type=Path, required=True)
    parser.add_argument('--unity-inputs', type=Path, required=True)
    parser.add_argument('--data-journal', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    output = safe_path(args.output, directory=True, missing=True)
    require(not output.exists(), 'Fresh normalization output required')
    events, summary = normalize(
        plan_raw=read_bounded(args.plan, 16384), plan_sha256=args.plan_sha256,
        native_raw=read_bounded(args.native), schedule_raw=read_bounded(args.schedule, 16 * 1024 * 1024),
        command_logs=[read_bounded(p, JOURNAL_LIMIT) for p in args.command_log],
        driver_raw=read_bounded(args.driver_journal, JOURNAL_LIMIT),
        feed_raw=read_bounded(args.unity_inputs, JOURNAL_LIMIT),
        data_files=[(p.name, read_bounded(p, 32 * 1024 * 1024)) for p in args.data_journal])
    output.mkdir(parents=True)
    content = b''.join(encoded(e) for e in events)
    for name, value in (('events.jsonl', content), ('normalization.json', encoded(dict(summary, events_sha256=sha(content))))):
        with (output / name).open('xb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    print(json.dumps(dict(events=len(events), events_sha256=sha(content), recommendation='NO_GO', g2_signed=False)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
