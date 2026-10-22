"""Reviewable systemd start/stop/restart units for one supervised station.

Generates text only: a ``docker run`` service plan built on ``container_plan``,
a ``sha256sum --check`` file for the pinned config and neutral snapshot, and a
unit that runs that plan. Nothing here installs, enables, starts or stops a
unit, executes Docker, creates accounts or changes host networking.

    python -m isaac.stations.units --station-config <private>/st1.json --config-sha256 <hash> \
        --reset-snapshot <private>/neutral.json --source <checkout> --evidence <private>/st1 \
        --mount-readonly /lab=<lab> --mount-readonly /unitree=<unitree> --mount-readonly /assets=<assets> \
        --output <private>/units-st1
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .config import container_plan, load, validate

ENTRYPOINT = 'isaac/stations/service.py'
RUNTIME_ROOT = '/run/acoustic-vocab'
CHECK_ROOT = '/etc/acoustic-vocab/stations'
READONLY_DESTINATIONS = ('/assets', '/lab', '/unitree')
SAFE_WORD = re.compile(r'[A-Za-z0-9_@+=:,./-]+\Z')
HOST_PATH = re.compile(r'/[A-Za-z0-9_@+=:./-]*\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')


def host_path(value, label):
    """Absolute POSIX host path without whitespace, commas, quotes or traversal."""
    text = str(value)
    if not HOST_PATH.fullmatch(text) or '/../' in text+'/' or '//' in text or ',' in text:
        raise ValueError('Absolute reviewed host path required: '+label)
    return PurePosixPath(text)


def systemd_quote(arg):
    """Quote one argv element for ExecStart= (whitespace, quotes, % specifiers, $ variables)."""
    if not isinstance(arg, str) or not arg or any(ord(c) < 32 or ord(c) == 127 for c in arg):
        raise ValueError('Printable nonempty argument required')
    if SAFE_WORD.fullmatch(arg):
        return arg
    escaped = arg.replace('%', '%%').replace('$', '$$').replace('\\', '\\\\').replace('"', '\\"')
    return '"'+escaped+'"'


def unit_name(station_id):
    return 'av-station-'+validate_id(station_id)+'.service'


def validate_id(station_id):
    if not isinstance(station_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', station_id):
        raise ValueError('Safe logical station identifier required')
    return station_id


def service_plan(value, *, config_file, config_sha256, snapshot_file, source_directory, output_directory,
                 readonly_mounts, runtime_root=RUNTIME_ROOT, stop_timeout_s=60, observer_camera=False):
    """Docker argv for the supervised entrypoint; extends the reviewed base plan."""
    value = validate(value)
    if type(stop_timeout_s) is not int or not 10 <= stop_timeout_s <= 600:
        raise ValueError('Explicit stop timeout 10..600 s required')
    if set(readonly_mounts) != set(READONLY_DESTINATIONS):
        raise ValueError('Exactly the reviewed /assets, /lab and /unitree read-only mounts are required')
    base = container_plan(value, config_file=config_file, config_sha256=config_sha256,
                          source_directory=source_directory, output_directory=output_directory,
                          entrypoint=ENTRYPOINT)
    argv = list(base['argv'])
    runtime = host_path(runtime_root, 'runtime root')/value['station_id']
    snapshot = host_path(snapshot_file, 'reset snapshot')
    options = ['--init', '--stop-signal', 'SIGTERM', '--stop-timeout', str(stop_timeout_s),
               '--mount', f'type=bind,src={snapshot},dst=/station/neutral.json,readonly',
               '--mount', f'type=bind,src={runtime},dst=/run/station']
    for destination in READONLY_DESTINATIONS:
        source = host_path(readonly_mounts[destination], destination)
        options += ['--mount', f'type=bind,src={source},dst={destination},readonly']
    image = argv.index(value['image_digest'])
    argv = argv[:image]+options+argv[image:]
    argv += ['--reset-snapshot', '/station/neutral.json', '--runtime-dir', '/run/station', '--headless']
    if observer_camera:
        argv.append('--observer-camera')
    if '--privileged' in argv or '--network' not in argv or argv[argv.index('--network')+1] != 'none':
        raise ValueError('Unsafe station plan')
    plan_sha256 = hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode()).hexdigest()
    return dict(base, argv=argv, plan_sha256=plan_sha256, runtime_directory=str(runtime),
                stop_timeout_s=stop_timeout_s, gateway_implemented=True, runtime_entrypoint_validated=False,
                installed=False, executed=False,
                warning='Reviewed text only. Not installed or executed; remote-host validation is pending.')


def check_file(config_file, config_sha256, snapshot_file, snapshot_sha256):
    if not HASH.fullmatch(config_sha256) or not HASH.fullmatch(snapshot_sha256):
        raise ValueError('Pinned SHA-256 values required')
    return (f'{config_sha256}  {host_path(config_file, "config")}\n'
            f'{snapshot_sha256}  {host_path(snapshot_file, "reset snapshot")}\n')


def unit_text(value, plan, *, check_path):
    value = validate(value)
    station = value['station_id']
    runtime = host_path(plan['runtime_directory'], 'runtime directory')
    check = host_path(check_path, 'check file')
    uid = value['allowed_uid']
    command = ' '.join(systemd_quote(arg) for arg in ['/usr/bin/docker']+plan['argv'][1:])
    sockets = ' '.join(str(runtime/p) for p in ('state.sock', 'commands.sock', 'internal/state.sock',
                                                'internal/commands.sock'))
    return '\n'.join([
        '# GENERATED FOR REVIEW ONLY by isaac.stations.units. This repository never installs,',
        '# enables, starts or stops units. An operator installs it after independent review.',
        f'# station_id={station} config_sha256={plan["config_sha256"]}',
        f'# reset_snapshot_sha256={value["reset_snapshot_sha256"]} plan_sha256={plan["plan_sha256"]}',
        f'# image={value["image_digest"]} source_revision={value["source_revision"]}',
        f'# start:   systemctl start {unit_name(station)}',
        f'# stop:    systemctl stop {unit_name(station)}     (SIGTERM; ordered shutdown; journal "stopped")',
        f'# restart: systemctl restart {unit_name(station)}  (new run id; restart identity journaled)',
        '# Restart=no: a latched fault or private stop needs an explicit operator restart.',
        '# The docker CLI runs as root; the station process runs only as the station UID in a',
        '# --network none container. The station account and UID are created by the operator.',
        '',
        '[Unit]',
        f'Description=Acoustic Vocabularies Isaac station {station} (config {plan["config_sha256"][:12]})',
        'Requires=docker.service',
        'After=docker.service',
        '',
        '[Service]',
        'Type=exec',
        f'ExecStartPre=/usr/bin/sha256sum --check --strict --status {check}',
        f'ExecStartPre=/usr/bin/install -d -m 0700 -o {uid} -g {uid} {runtime}',
        'ExecStart='+command,
        f'ExecStop=/usr/bin/docker stop --time {plan["stop_timeout_s"]} av-{station}',
        f'ExecStopPost=/bin/rm -f -- {sockets}',
        'Restart=no',
        'TimeoutStartSec=600',
        f'TimeoutStopSec={plan["stop_timeout_s"]+30}',
        'KillMode=mixed',
        '',
        '[Install]',
        'WantedBy=multi-user.target',
        ''])


def render(value, *, config_file, config_sha256, snapshot_file, source_directory, output_directory,
           readonly_mounts, runtime_root=RUNTIME_ROOT, check_root=CHECK_ROOT, stop_timeout_s=60,
           observer_camera=False):
    plan = service_plan(value, config_file=config_file, config_sha256=config_sha256, snapshot_file=snapshot_file,
                        source_directory=source_directory, output_directory=output_directory,
                        readonly_mounts=readonly_mounts, runtime_root=runtime_root,
                        stop_timeout_s=stop_timeout_s, observer_camera=observer_camera)
    station = value['station_id']
    check_path = host_path(check_root, 'check root')/(station+'.sha256')
    files = {unit_name(station): unit_text(value, plan, check_path=check_path),
             station+'.sha256': check_file(config_file, config_sha256, snapshot_file, value['reset_snapshot_sha256']),
             station+'.plan.json': json.dumps(plan, indent=2, sort_keys=True)+'\n'}
    manifest = dict(version=1, station_id=station, config_sha256=config_sha256, plan_sha256=plan['plan_sha256'],
                    check_file_install_path=str(check_path), installed=False, executed=False,
                    files={name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()})
    files['units-manifest.json'] = json.dumps(manifest, indent=2, sort_keys=True)+'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--station-config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--reset-snapshot', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True, help='Private per-station output directory')
    parser.add_argument('--mount-readonly', action='append', default=[], metavar='DST=SRC')
    parser.add_argument('--runtime-root', default=RUNTIME_ROOT)
    parser.add_argument('--check-root', default=CHECK_ROOT)
    parser.add_argument('--stop-timeout', type=int, default=60)
    parser.add_argument('--observer-camera', action='store_true')
    parser.add_argument('--output', type=Path, required=True, help='Fresh directory for generated text')
    args = parser.parse_args(argv)
    value = load(args.station_config, args.config_sha256)
    if hashlib.sha256(args.reset_snapshot.read_bytes()).hexdigest() != value['reset_snapshot_sha256']:
        raise ValueError('Reset snapshot does not match the pinned station config')
    mounts = {}
    for item in args.mount_readonly:
        destination, separator, source = item.partition('=')
        if not separator or destination in mounts:
            raise ValueError('Use each --mount-readonly DST=SRC once')
        mounts[destination] = source
    files = render(value, config_file=args.station_config.resolve(), config_sha256=args.config_sha256,
                   snapshot_file=args.reset_snapshot.resolve(), source_directory=args.source,
                   output_directory=args.evidence, readonly_mounts=mounts, runtime_root=args.runtime_root,
                   check_root=args.check_root, stop_timeout_s=args.stop_timeout,
                   observer_camera=args.observer_camera)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, text in files.items():
        with (args.output/name).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
    print(json.dumps(dict(station_id=value['station_id'], files=sorted(files), installed=False, executed=False)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
