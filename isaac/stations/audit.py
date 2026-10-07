"""Station-independent command matrix; clients are explicit provisioned adapters.

Each adapter supplies a strict PublicRegistry, public_state() and synchronous
command(name,args)->final reply. No generic network discovery or auto-rebinding.
"""
import hashlib
import json

from isaac.commands.protocol import LEGAL_PAIRS
from isaac.publisher.protocol import validate_frame
from .config import validate_fleet, digest


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


def run_cross_talk(configurations, clients, event_sink):
    configurations = validate_fleet(configurations)
    expected = {value['station_id']: value for value in configurations}
    if set(clients) != set(expected) or len(clients) < 2 or not callable(event_sink):
        raise ValueError('Explicit two-or-more station clients and durable sink required')

    def read(station):
        client = clients[station]
        configuration = expected[station]
        registry = client.registry
        if (registry.station_id != station or registry.scene_sha256 != configuration['scene_sha256']
                or registry.reset_snapshot_sha256 != configuration['reset_snapshot_sha256']):
            raise ValueError('Client registry is bound to another station or asset')
        frame = validate_frame(client.public_state(), registry)
        return frame

    baselines, kinds = {}, set()
    for station, client in clients.items():
        reset = client.command('reset', {})
        if reset.get('reset_ok') is not True:
            raise ValueError('Every station must explicitly reset before cross-talk audit')
        frame = read(station)
        baselines[station] = render_hash(frame)
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
    cases, changed_others = 0, []
    for station, client in clients.items():
        for command, args in command_matrix():
            before = {other: render_hash(read(other)) for other in clients if other != station}
            if any(value != baselines[other] for other, value in before.items()):
                raise ValueError('Non-target station was not neutral before command')
            reply = client.command(command, args)
            after = {other: render_hash(read(other)) for other in before}
            changed = [other for other in before if before[other] != after[other]]
            event = dict(kind='cross_talk_case', station_id=station, command=command, arguments=args,
                         accepted=reply.get('accepted') is True, other_station_count=len(before),
                         changed_stations=changed, before=before, after=after)
            event_sink(event)
            cases += 1
            changed_others.extend(changed)
            if changed or not event['accepted']:
                # Preserve this failed event; do not continue and silently reset
                # the other stations to hide the observed cross-talk.
                return dict(passed=False, cases=cases, changed_other_count=len(changed_others),
                            source_kind=next(iter(kinds)), wrong_station_rejected=sum(x['rejected'] for x in wrong))
        if render_hash(read(station)) != baselines[station]:
            raise ValueError('Station did not return to neutral after stop')
    snapshot_ok = not changed_others and all(row['rejected'] for row in wrong)
    # Endpoint hashes cannot detect a transient excursion that returns before
    # the synchronous reply. A live audit needs a continuous independent trace.
    return dict(passed=snapshot_ok and kinds == {'synthetic'}, cases=cases,
                snapshot_contract_passed=snapshot_ok, continuous_trace_complete=False,
                evidence_scope='exact_endpoint_snapshots_only',
                changed_other_count=0, source_kind=next(iter(kinds)),
                station_count=len(clients), config_hashes={key:digest(value) for key,value in expected.items()},
                wrong_station_rejected=sum(row['rejected'] for row in wrong),
                wrong_station_connection_authentication_tested=False, packet_capture_complete=False,
                one_hour_capacity_run_complete=False)
