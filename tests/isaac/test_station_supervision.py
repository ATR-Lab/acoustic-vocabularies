"""Supervised station entrypoint, lifecycle records, gateway and unit text.

Pure logic runs everywhere. Unix sockets, SO_PEERCRED and real signal delivery
skip with explicit reasons off Linux. Nothing here runs Isaac, Docker or systemd.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import tempfile
import threading
from types import SimpleNamespace

import pytest

from isaac.stations.config import canonical_bytes, cyclone_xml, digest
from isaac.stations.gateway import (StationGateway, admit_peer, directory_problem, prepare_runtime,
                                    runtime_layout, socket_problem)
from isaac.stations.lifecycle import LifecycleJournal, read_history, record_refusal, restart_identity
from isaac.stations.service import ENV_HASH, expected_environment, main as service_main, preflight
from isaac.stations.supervisor import Component, StationSupervisor
from isaac.stations.units import host_path, render, service_plan, systemd_quote, unit_text
from isaac.workcell.layout import digest as layout_digest

ROOT = Path(__file__).resolve().parents[2]
LINUX_SOCKETS = not (hasattr(socket, 'AF_UNIX') and hasattr(socket, 'SO_PEERCRED') and hasattr(os, 'geteuid'))
LINUX_REASON = 'Linux Unix sockets with SO_PEERCRED and geteuid required'


def config(number=1, **changes):
    value = dict(version=1, station_id='engineering-'+str(number), logical_host='fixture-host', gpu_index=number-1,
        publisher_port=20000+number*2, command_port=20001+number*2, dds_domain_id=40+number, ros_domain_id=None,
        allowed_client='127.0.0.1', allowed_uid=10000+number, scene_sha256='a'*64, reset_snapshot_sha256='b'*64,
        layout_sha256='c'*64, image_digest='sha256:'+'d'*64, source_revision='e'*40,
        publisher_hz=30, network_mode='none', ipc_mode='private', unitree_dds_enabled=False)
    value.update(changes)
    return value


# Lifecycle journal -----------------------------------------------------------

def journal(tmp_path, value=None, **kwargs):
    value = value or config()
    return LifecycleJournal(tmp_path/'lifecycle.jsonl', station_id=value['station_id'],
                            config_sha256=digest(value), **kwargs)


def test_restart_identity_chains_runs_and_reports_unclean_previous_stop(tmp_path):
    first = journal(tmp_path)
    assert first.identity == dict(start_index=0, restart=False, previous_run_id=None,
                                  previous_stop_recorded=None, previous_exit_code=None)
    first.record('starting'); first.record('stopped', exit_code=0); first.close()
    second = journal(tmp_path)
    assert second.identity['restart'] and second.identity['previous_run_id'] == first.run_id
    assert second.identity['previous_stop_recorded'] and second.identity['previous_exit_code'] == 0
    second.record('starting'); second.close()  # Killed: no stopped record.
    third = journal(tmp_path)
    assert third.identity['start_index'] == 2 and third.identity['previous_stop_recorded'] is False
    third.close()
    rows = read_history(tmp_path/'lifecycle.jsonl')
    assert [row['sequence'] for row in rows] == [0, 1, 2]


def test_changed_config_refuses_restart_and_refusal_is_journaled(tmp_path):
    original = journal(tmp_path); original.record('starting'); original.close()
    changed = config(publisher_hz=60)
    with pytest.raises(ValueError, match='configuration changed'):
        journal(tmp_path, changed)
    assert record_refusal(tmp_path/'lifecycle.jsonl', station_id=changed['station_id'],
                          config_sha256=digest(changed), reason='changed')
    rows = read_history(tmp_path/'lifecycle.jsonl')
    assert rows[-1]['kind'] == 'start_refused' and rows[-1]['config_sha256'] == digest(changed)
    # A refusal never poisons the original pinned identity.
    assert restart_identity(rows, config()['station_id'], digest(config()))['restart']


def test_foreign_tampered_or_partial_journal_refuses(tmp_path):
    original = journal(tmp_path); original.record('starting'); original.record('stopped', exit_code=0); original.close()
    other = config(2)
    with pytest.raises(ValueError, match='another station'):
        journal(tmp_path, other)
    assert not record_refusal(tmp_path/'lifecycle.jsonl', station_id=other['station_id'],
                              config_sha256=digest(other), reason='x')
    path = tmp_path/'lifecycle.jsonl'
    raw = path.read_bytes()
    # Editing any record before the last one breaks the following link.
    path.write_bytes(raw.replace(b'"payload":{}', b'"payload":{"edited":1}', 1))
    with pytest.raises(ValueError, match='chain is broken'):
        read_history(path)
    path.write_bytes(raw[:-5])
    with pytest.raises(ValueError, match='partial record'):
        read_history(path)


# Supervisor ordering ----------------------------------------------------------

class Recorder:
    def __init__(self, names, fail_start=None, fail_close=None):
        self.events, self.components = [], []
        for name in names:
            def start(name=name):
                if name == fail_start:
                    raise RuntimeError('start failed '+name)
                self.events.append(('start', name))
            def close(name=name):
                self.events.append(('close', name))
                if name == fail_close:
                    raise RuntimeError('close failed '+name)
            self.components.append(Component(name, start, close))


def fake_signals(log):
    def install(handler):
        log.append('installed')
        return lambda: log.append('restored')
    return install


def kinds(path):
    return [row['kind'] for row in read_history(path)]


def test_sigterm_stops_at_step_boundary_and_closes_in_reverse_order(tmp_path):
    record = Recorder(['isaac_app', 'scene', 'publisher', 'commands', 'gateway'])
    log, holder = [], {}
    def step():
        if len(log) == 4:
            holder['supervisor'].handle_signal(signal.SIGTERM)
        log.append('step')
        return True
    j = journal(tmp_path)
    supervisor = StationSupervisor(j, record.components, step, identity=dict(station_id='engineering-1'),
                                   install_signals=fake_signals(log))
    holder['supervisor'] = supervisor
    assert supervisor.run() == 0
    j.close()
    assert [e for e in record.events if e[0] == 'close'] == [('close', n) for n in
        ['gateway', 'commands', 'publisher', 'scene', 'isaac_app']]
    assert log[0] == 'installed' and log[-1] == 'restored'
    rows = read_history(tmp_path/'lifecycle.jsonl')
    assert [r['kind'] for r in rows][:7] == ['starting']+['component_started']*5+['started']
    stopped = rows[-1]['payload']
    assert rows[-1]['kind'] == 'stopped' and stopped['signal'] == 'SIGTERM' and stopped['reason'] == 'signal'
    assert stopped['shutdown_order'] == ['gateway', 'commands', 'publisher', 'scene', 'isaac_app']
    assert stopped['clean'] and stopped['exit_code'] == 0


def test_failed_start_closes_only_started_components_and_reports_fault(tmp_path):
    record = Recorder(['isaac_app', 'scene', 'publisher', 'commands'], fail_start='publisher')
    j = journal(tmp_path)
    code = StationSupervisor(j, record.components, lambda: True, install_signals=None).run()
    j.close()
    assert code == 1
    assert record.events == [('start', 'isaac_app'), ('start', 'scene'), ('close', 'scene'), ('close', 'isaac_app')]
    names = kinds(tmp_path/'lifecycle.jsonl')
    assert 'started' not in names and names[-1] == 'stopped'
    assert 'start failed publisher' in read_history(tmp_path/'lifecycle.jsonl')[-1]['payload']['fault']


def test_cleanup_error_continues_shutdown_and_is_not_clean(tmp_path):
    record = Recorder(['a', 'b', 'c'], fail_close='b')
    j = journal(tmp_path)
    code = StationSupervisor(j, record.components, lambda: False, install_signals=None).run()
    j.close()
    stopped = read_history(tmp_path/'lifecycle.jsonl')[-1]['payload']
    assert code == 1 and stopped['reason'] == 'service_stopped' and stopped['fault'] is None
    assert stopped['shutdown_order'] == ['c', 'a'] and stopped['cleanup_errors'][0]['component'] == 'b'
    assert [e for e in record.events if e[0] == 'close'] == [('close', 'c'), ('close', 'b'), ('close', 'a')]


def test_signal_during_startup_starts_nothing_further(tmp_path):
    holder = {}
    events = []
    def start_first():
        events.append('first')
        holder['s'].handle_signal(signal.SIGINT)
    components = [Component('first', start_first, lambda: events.append('close first')),
                  Component('second', lambda: events.append('second'), lambda: None)]
    j = journal(tmp_path)
    holder['s'] = StationSupervisor(j, components, lambda: pytest.fail('no step after stop'), install_signals=None)
    assert holder['s'].run() == 0
    j.close()
    assert events == ['first', 'close first']
    assert read_history(tmp_path/'lifecycle.jsonl')[-1]['payload']['signal'] == 'SIGINT'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX signal delivery to the main thread required')
def test_real_sigterm_is_handled_by_installed_handler(tmp_path):
    record = Recorder(['only'])
    sent = []
    def step():
        if not sent:
            sent.append(True)
            os.kill(os.getpid(), signal.SIGTERM)
        return True
    j = journal(tmp_path)
    previous = signal.getsignal(signal.SIGTERM)
    assert StationSupervisor(j, record.components, step).run() == 0
    j.close()
    assert signal.getsignal(signal.SIGTERM) is previous
    assert read_history(tmp_path/'lifecycle.jsonl')[-1]['payload']['signal'] == 'SIGTERM'


# Entrypoint preflight -------------------------------------------------------------

def pinned(tmp_path, **changes):
    layout = json.loads((ROOT/'apparatus/workcell_layout.json').read_bytes())
    snapshot = ROOT/'isaac/snapshots/neutral_v1.json'
    value = config(**{'layout_sha256': layout_digest(layout),
                      'reset_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(), **changes})
    path = tmp_path/'station.json'
    path.write_bytes(canonical_bytes(value))
    environ = {k: v for k, v in expected_environment(value).items() if v is not None}
    environ[ENV_HASH] = digest(value)
    kwargs = dict(reset_snapshot=snapshot, source=ROOT, geteuid=lambda: value['allowed_uid'],
                  namespace_check=lambda: dict(namespace_route_check_passed=True),
                  revision=lambda source: (value['source_revision'], False))
    return value, path, environ, kwargs


def test_preflight_accepts_only_the_complete_pinned_identity(tmp_path):
    value, path, environ, kwargs = pinned(tmp_path, ros_domain_id=41)
    assert environ['ROS_DOMAIN_ID'] == '41' and environ['CYCLONEDDS_URI'] == cyclone_xml(value).strip()
    result = preflight(path, environ, **kwargs)
    assert result['config'] == value and result['config_sha256'] == digest(value)


@pytest.mark.parametrize('mutation,message', [
    (lambda e, k: e.pop(ENV_HASH), 'STATION_CONFIG_SHA256'),
    (lambda e, k: e.update({ENV_HASH: 'f'*64}), 'hash mismatch'),
    (lambda e, k: e.update(CYCLONEDDS_URI='<CycloneDDS/>'), 'CYCLONEDDS_URI'),
    (lambda e, k: e.update(ROS_LOCALHOST_ONLY='0'), 'ROS_LOCALHOST_ONLY'),
    (lambda e, k: e.update(ROS_DOMAIN_ID='7'), 'ROS_DOMAIN_ID'),
    (lambda e, k: k.update(geteuid=lambda: 0), 'station UID'),
    (lambda e, k: k.update(revision=lambda s: ('0'*40, False)), 'pinned clean revision'),
    (lambda e, k: k.update(revision=lambda s: ('e'*40, True)), 'pinned clean revision'),
    (lambda e, k: k.update(reset_snapshot=ROOT/'apparatus/workcell_layout.json'), 'snapshot SHA-256 mismatch'),
])
def test_preflight_refuses_any_pin_mismatch(tmp_path, mutation, message):
    _, path, environ, kwargs = pinned(tmp_path)
    mutation(environ, kwargs)
    with pytest.raises(ValueError, match=message):
        preflight(path, environ, **kwargs)


def test_preflight_refuses_changed_layout_and_routable_namespace(tmp_path):
    _, path, environ, kwargs = pinned(tmp_path, layout_sha256='c'*64)
    with pytest.raises(ValueError, match='layout'):
        preflight(path, environ, **kwargs)
    _, path, environ, kwargs = pinned(tmp_path)
    def routable():
        raise ValueError('Station namespace has a non-loopback interface')
    kwargs['namespace_check'] = routable
    with pytest.raises(ValueError, match='non-loopback'):
        preflight(path, environ, **kwargs)


def test_entrypoint_exits_2_before_any_side_effect_on_refusal(tmp_path, monkeypatch, capsys):
    value, path, _, _ = pinned(tmp_path)
    monkeypatch.delenv(ENV_HASH, raising=False)
    output = tmp_path/'out'
    code = service_main(['--station-config', str(path), '--reset-snapshot', str(ROOT/'isaac/snapshots/neutral_v1.json'),
                         '--runtime-dir', str(tmp_path/'run'), '--output', str(output)])
    assert code == 2 and not output.exists() and not (tmp_path/'run').exists()
    assert json.loads(capsys.readouterr().err)['event'] == 'start_refused'


# Gateway -------------------------------------------------------------------------

def test_gateway_pure_ownership_mode_and_peer_rules():
    directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=10001)
    assert directory_problem(directory, 10001) is None
    assert 'owned' in directory_problem(directory, 10002)
    assert '0700' in directory_problem(SimpleNamespace(st_mode=stat.S_IFDIR | 0o750, st_uid=10001), 10001)
    assert 'symlink' in directory_problem(directory, 10001, is_symlink=True)
    sock = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=10001)
    assert socket_problem(sock, 10001) is None
    assert '0600' in socket_problem(SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=10001), 10001)
    assert 'not a Unix socket' in socket_problem(SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=10001), 10001)
    assert admit_peer(10001, 10001) is None
    assert admit_peer(0, 10001) == 'UNKNOWN_PEER_UID'  # Root is refused too.
    assert admit_peer(None, 10001) == 'PEER_CREDENTIALS_UNAVAILABLE'
    root = Path(tempfile.gettempdir())/'station'
    layout = runtime_layout(root)
    assert layout['external'] == dict(state=root/'state.sock', commands=root/'commands.sock')
    assert layout['internal_sockets'] == dict(state=root/'internal/state.sock', commands=root/'internal/commands.sock')
    with pytest.raises(ValueError):
        runtime_layout('relative/run')


def test_gateway_refuses_wrong_process_uid():
    with pytest.raises((ValueError, OSError)):
        prepare_runtime(tempfile.gettempdir(), 10001, geteuid=lambda: 10002)


class Echo:
    """Internal endpoint stand-in: echoes bytes on a 0600 Unix socket."""

    def __init__(self, path):
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path)); os.chmod(path, 0o600); self.listener.listen(4)
        self.thread = threading.Thread(target=self.serve, daemon=True); self.thread.start()

    def serve(self):
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            with connection:
                while data := connection.recv(4096):
                    connection.sendall(data)

    def close(self):
        self.listener.close()


@pytest.mark.skipif(LINUX_SOCKETS, reason=LINUX_REASON)
def test_gateway_relays_station_uid_and_journals_refused_peer(tmp_path):
    uid = os.geteuid()
    if uid == 0:
        pytest.skip('Running as root: the gateway refuses root by design')
    root = Path(tempfile.mkdtemp(prefix='avgw', dir='/tmp'))
    os.chmod(root, 0o700)
    layout = prepare_runtime(root, uid)
    assert stat.S_IMODE(os.stat(layout['internal']).st_mode) == 0o700
    echoes = [Echo(path) for path in layout['internal_sockets'].values()]
    j = journal(tmp_path, config(allowed_uid=uid))
    refused_uid = {'value': None}
    def credentials(connection):
        from isaac.stations.gateway import peer_uid
        actual = peer_uid(connection)
        return refused_uid['value'] if refused_uid['value'] is not None else actual
    gateway = StationGateway(layout, uid, j.record, credentials=credentials)
    try:
        for path in layout['external'].values():
            info = os.stat(path)
            assert stat.S_ISSOCK(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == uid
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(path)); client.sendall(b'station-bytes')
                assert client.recv(64) == b'station-bytes'
        refused_uid['value'] = uid+1
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(layout['external']['commands']))
            client.settimeout(5)
            assert client.recv(64) == b''
    finally:
        counts = gateway.close()
        for echo in echoes: echo.close()
        j.close()
    assert counts['state']['admitted'] == 1 and counts['commands']['refused'] == 1
    assert not any(path.exists() for path in layout['external'].values())
    refused = [row for row in read_history(tmp_path/'lifecycle.jsonl') if row['kind'] == 'gateway_refused']
    assert refused[0]['payload'] == dict(endpoint='commands', peer_uid=uid+1, reason='UNKNOWN_PEER_UID')
    with pytest.raises(FileExistsError):
        prepare_runtime(root, uid)  # Stale internal endpoints need operator inspection.


# Service units -------------------------------------------------------------------

MOUNTS = {'/assets': '/srv/isaac/assets', '/lab': '/srv/isaac/lab', '/unitree': '/srv/isaac/unitree'}


def plan(tmp_path, value=None, **kwargs):
    value = value or config()
    return service_plan(value, config_file=tmp_path/'c.json', config_sha256=digest(value),
                        snapshot_file='/etc/acoustic-vocab/stations/neutral.json', source_directory=tmp_path/'source',
                        output_directory=tmp_path/'output', readonly_mounts=MOUNTS, **kwargs)


def systemd_split(line):
    """Minimal ExecStart= tokenizer for the subset systemd_quote emits."""
    words, word, quoted, index, active = [], '', False, 0, False
    while index < len(line):
        char = line[index]
        if quoted:
            if char == '\\':
                index += 1; word += line[index]
            elif char == '"':
                quoted = False
            else:
                word += char
        elif char == '"':
            quoted, active = True, True
        elif char == ' ':
            if active or word: words.append(word)
            word, active = '', False
        else:
            word += char; active = True
        index += 1
    if active or word: words.append(word)
    return [w.replace('%%', '%').replace('$$', '$') for w in words]


def test_service_plan_extends_reviewed_containment_without_executing(tmp_path):
    result = plan(tmp_path, observer_camera=True)
    argv = result['argv']
    assert argv[argv.index('--network')+1] == 'none' and argv[argv.index('--ipc')+1] == 'private'
    assert '--init' in argv and argv[argv.index('--stop-signal')+1] == 'SIGTERM'
    assert 'type=bind,src=/run/acoustic-vocab/engineering-1,dst=/run/station' in argv
    assert 'type=bind,src=/srv/isaac/lab,dst=/lab,readonly' in argv
    assert argv.index('--init') < argv.index(config()['image_digest']) < argv.index('/isaac-sim/python.sh')
    assert argv[argv.index('/source/isaac/stations/service.py')+1:] == [
        '--station-config', '/station/config.json', '--output', '/results', '--reset-snapshot',
        '/station/neutral.json', '--runtime-dir', '/run/station', '--headless', '--observer-camera']
    assert not result['executed'] and not result['installed'] and result['gateway_implemented']
    assert not result['runtime_entrypoint_validated']
    assert '-p' not in argv and '--publish' not in argv and '--privileged' not in argv
    with pytest.raises(ValueError, match='read-only mounts'):
        service_plan(config(), config_file=tmp_path/'c.json', config_sha256=digest(config()),
                     snapshot_file='/etc/n.json', source_directory=tmp_path/'s', output_directory=tmp_path/'o',
                     readonly_mounts={'/lab': '/srv/lab'})
    with pytest.raises(ValueError, match='hash mismatch'):
        service_plan(config(), config_file=tmp_path/'c.json', config_sha256='f'*64, snapshot_file='/etc/n.json',
                     source_directory=tmp_path/'s', output_directory=tmp_path/'o', readonly_mounts=MOUNTS)


@pytest.mark.parametrize('arg', ['plain', 'with space', 'quote"inside', 'percent%n', 'dollar$HOME',
                                 'back\\slash', '<CycloneDDS><Domain Id="41"/></CycloneDDS>', ';'])
def test_systemd_quoting_round_trips(arg):
    assert systemd_split(systemd_quote(arg)) == [arg]


@pytest.mark.parametrize('path', ['relative/x', '/with space', '/a,b', '/a/../b', '/a/..', '/quote"'])
def test_host_paths_are_absolute_and_reviewable(path):
    with pytest.raises(ValueError):
        host_path(path, 'x')


@pytest.mark.skipif(os.name != 'posix', reason='POSIX host paths required for generated unit files')
def test_units_cli_writes_fresh_review_files_and_refuses_unpinned_snapshot(tmp_path):
    from isaac.stations.units import main as units_main
    snapshot = tmp_path/'neutral.json'
    snapshot.write_bytes((ROOT/'isaac/snapshots/neutral_v1.json').read_bytes())
    value = config(reset_snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest())
    station = tmp_path/'station.json'
    station.write_bytes(canonical_bytes(value))
    args = ['--station-config', str(station), '--config-sha256', digest(value), '--reset-snapshot',
            str(snapshot), '--source', str(tmp_path/'source'),
            '--evidence', str(tmp_path/'evidence'), '--output', str(tmp_path/'units')]
    args += [item for d, s in MOUNTS.items() for item in ('--mount-readonly', d+'='+s)]
    assert units_main(args) == 0
    assert sorted(p.name for p in (tmp_path/'units').iterdir()) == [
        'av-station-engineering-1.service', 'engineering-1.plan.json', 'engineering-1.sha256', 'units-manifest.json']
    with pytest.raises(FileExistsError):
        units_main(args)
    (tmp_path/'other.json').write_bytes(b'{}\n')
    args[args.index('--reset-snapshot')+1] = str(tmp_path/'other.json')
    args[-1 - 2*len(MOUNTS)] = str(tmp_path/'units-2')
    with pytest.raises(ValueError, match='Reset snapshot'):
        units_main(args)


def test_unit_text_runs_exact_plan_with_pinned_hashes_and_explicit_restart_policy(tmp_path):
    value = config()
    files = render(value, config_file='/etc/acoustic-vocab/stations/engineering-1.json', config_sha256=digest(value),
                   snapshot_file='/etc/acoustic-vocab/stations/neutral.json', source_directory=tmp_path/'source',
                   output_directory=tmp_path/'output', readonly_mounts=MOUNTS)
    unit = files['av-station-engineering-1.service']
    lines = unit.splitlines()
    start = next(line for line in lines if line.startswith('ExecStart='))[len('ExecStart='):]
    plan_value = json.loads(files['engineering-1.plan.json'])
    assert systemd_split(start) == ['/usr/bin/docker']+plan_value['argv'][1:]
    assert 'ExecStop=/usr/bin/docker stop --time 60 av-engineering-1' in lines
    assert 'Restart=no' in lines and 'TimeoutStopSec=90' in lines
    assert 'ExecStartPre=/usr/bin/sha256sum --check --strict --status /etc/acoustic-vocab/stations/engineering-1.sha256' in lines
    assert 'ExecStartPre=/usr/bin/install -d -m 0700 -o 10001 -g 10001 /run/acoustic-vocab/engineering-1' in lines
    assert files['engineering-1.sha256'] == (digest(value)+'  /etc/acoustic-vocab/stations/engineering-1.json\n'
                                            +'b'*64+'  /etc/acoustic-vocab/stations/neutral.json\n')
    assert f'config_sha256={digest(value)}' in unit and 'GENERATED FOR REVIEW ONLY' in unit
    manifest = json.loads(files['units-manifest.json'])
    assert manifest['installed'] is False and manifest['executed'] is False
    assert manifest['files']['av-station-engineering-1.service'] == hashlib.sha256(unit.encode()).hexdigest()
    assert unit_text(value, plan_value, check_path='/etc/x.sha256') != unit
