"""Station-independent command matrix; clients are explicit provisioned adapters.

Each adapter supplies a strict PublicRegistry, public_state() and synchronous
command(name,args)->final reply. No generic network discovery or auto-rebinding.
An optional continuous trace uses separate per-station sampler adapters.
"""
import hashlib
import json
import time

from isaac.commands.protocol import LEGAL_PAIRS
from isaac.publisher.protocol import validate_frame
from .config import validate_fleet, digest
from .trace import ContinuousTrace, summarize, validate_policy


def render_hash(frame):
    value = {key: frame[key] for key in ('joint_names', 'joint_positions', 'objects')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def command_matrix():
    result = [('health', {}), ('hold_neutral', {}), ('reset', {}), ('set_mode', {'mode':'test'}),
              ('set_mode', {'mode':'teaching'}), ('pause', {}), ('resume', {})]
    for action, target in sorted(LEGAL_PAIRS):
        result += [('demo', dict(action=action, target=target)), ('reset', {})]
    result += [('set_mode', {'mode':'post_endpoint'}), ('set_mode', {'mode':'test'}), ('stop', {})]
    return result


def run_cross_talk(configurations, clients, event_sink, *, trace_samplers=None, trace_policy=None,
                   trace_sample_sink=None, clock_ns=time.perf_counter_ns):
    configurations = validate_fleet(configurations)
    expected = {value['station_id']: value for value in configurations}
    if set(clients) != set(expected) or len(clients) < 2 or not callable(event_sink):
        raise ValueError('Explicit two-or-more station clients and durable sink required')
    if (trace_samplers is None) != (trace_policy is None):
        raise ValueError('A continuous trace needs both per-station samplers and a declared policy')
    if trace_samplers is not None:
        validate_policy(trace_policy)
        if set(trace_samplers) != set(expected):
            raise ValueError('One independent trace sampler per station required')
        if any(trace_samplers[s] is clients[s] for s in expected):
            raise ValueError('Trace samplers must be independent of the command clients')

    def check_registry(station, registry):
        configuration = expected[station]
        if (registry.station_id != station or registry.scene_sha256 != configuration['scene_sha256']
                or registry.reset_snapshot_sha256 != configuration['reset_snapshot_sha256']):
            raise ValueError('Client registry is bound to another station or asset')

    def read(station):
        client = clients[station]
        check_registry(station, client.registry)
        frame = validate_frame(client.public_state(), client.registry)
        return frame

    baselines, frames, kinds = {}, {}, set()
    for station, client in clients.items():
        reset = client.command('reset', {})
        if reset.get('reset_ok') is not True:
            raise ValueError('Every station must explicitly reset before cross-talk audit')
        frame = read(station)
        baselines[station] = render_hash(frame)
        frames[station] = frame
        kinds.add(frame['source_kind'])
    if len(kinds) != 1:
        raise ValueError('Do not mix synthetic and live station evidence')
    wrong = []
    for intended in clients:
        for other in clients:
            if intended == other: continue
            rejected = False
            try:
                validate_frame(read(other), clients[intended].registry)
            except ValueError:
                rejected = True
            row = dict(kind='wrong_station_public_binding', intended=intended, observed=other, rejected=rejected)
            event_sink(row)
            wrong.append(row)

    trace, windows, trace_summary = None, [], {}
    if trace_samplers is not None:
        for station, sampler in trace_samplers.items():
            check_registry(station, sampler.registry)
        trace = ContinuousTrace(trace_samplers, frames, trace_policy, clock_ns=clock_ns,
                                sample_sink=trace_sample_sink)

    def report(**values):
        if trace is not None:
            trace_summary.update(summarize(windows, trace_policy, trace.close()))
        else:
            trace_summary.update(continuous_trace_complete=False, trace_passed=False)
        scope = ('endpoint_snapshots_and_continuous_trace' if trace is not None
                 else 'exact_endpoint_snapshots_only')
        return dict(values, evidence_scope=scope, **trace_summary)

    cases, changed_others = 0, []
    try:
        if trace is not None:
            trace.start()
        for station, client in clients.items():
            for command, args in command_matrix():
                before = {other: render_hash(read(other)) for other in clients if other != station}
                if any(value != baselines[other] for other, value in before.items()):
                    raise ValueError('Non-target station was not neutral before command')
                start_ns = clock_ns()
                reply = client.command(command, args)
                reply_ns = clock_ns()
                after = {other: render_hash(read(other)) for other in before}
                changed = [other for other in before if before[other] != after[other]]
                event = dict(kind='cross_talk_case', station_id=station, command=command, arguments=args,
                             accepted=reply.get('accepted') is True, other_station_count=len(before),
                             changed_stations=changed, before=before, after=after)
                exceeded = False
                if trace is not None:
                    window = trace.window(station, start_ns, reply_ns)
                    window.update(command=command, arguments=args)
                    windows.append(window)
                    exceeded = any(row['exceedances'] for row in window['stations'])
                    event['trace'] = dict(stations=window['stations'], sampler_errors=window['sampler_errors'])
                event_sink(event)
                cases += 1
                changed_others.extend(changed)
                if changed or exceeded or not event['accepted']:
                    # Preserve this failed event; do not continue and silently reset
                    # the other stations to hide the observed cross-talk.
                    return report(passed=False, cases=cases, changed_other_count=len(changed_others),
                                  snapshot_contract_passed=False if changed else None,
                                  trace_exceedance_stopped=exceeded,
                                  source_kind=next(iter(kinds)),
                                  wrong_station_rejected=sum(x['rejected'] for x in wrong))
            if render_hash(read(station)) != baselines[station]:
                raise ValueError('Station did not return to neutral after stop')
        snapshot_ok = not changed_others and all(row['rejected'] for row in wrong)
        result = report(cases=cases, snapshot_contract_passed=snapshot_ok, changed_other_count=0,
                        source_kind=next(iter(kinds)), station_count=len(clients),
                        config_hashes={key:digest(value) for key,value in expected.items()},
                        wrong_station_rejected=sum(row['rejected'] for row in wrong),
                        wrong_station_connection_authentication_tested=False, packet_capture_complete=False,
                        one_hour_capacity_run_complete=False)
    finally:
        if trace is not None and not trace.stopping:
            trace.close()
    # Endpoint hashes cannot detect a transient excursion that returns before
    # the synchronous reply. Live evidence therefore also requires a complete
    # continuous trace that meets its declared coverage and tolerance.
    if trace is None:
        result['passed'] = snapshot_ok and kinds == {'synthetic'}
    else:
        result['passed'] = snapshot_ok and result['trace_passed']
    return result
