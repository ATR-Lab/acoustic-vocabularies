"""Supervised long-running service for exactly one Isaac station (Linux only).

Started by the reviewed container plan / service unit as::

    /isaac-sim/python.sh /source/isaac/stations/service.py --station-config /station/config.json \
        --reset-snapshot /station/neutral.json --runtime-dir /run/station --output /results --headless

Every pin is checked before Isaac, sockets or the journal are touched: the raw
config SHA-256 from ``STATION_CONFIG_SHA256``, the derived CycloneDDS/ROS
environment, the station UID, the loopback-only namespace, the pinned source
revision, the layout hash and the neutral snapshot hash. Any mismatch exits
with code 2 and starts nothing. Exit 0 is an ordered stop (SIGTERM/SIGINT or a
private ``stop`` command); exit 1 is a fault or cleanup error. The service
never restarts itself: a latched fault requires an explicit operator restart.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

if __package__ in (None, ''):
    # Executed as a script by /isaac-sim/python.sh; make /source importable.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from isaac.stations.config import cyclone_xml, load
from isaac.stations.lifecycle import LifecycleJournal, record_refusal
from isaac.stations.supervisor import EXIT_REFUSED

ENV_HASH = 'STATION_CONFIG_SHA256'
HASH = re.compile(r'[0-9a-f]{64}\Z')


def expected_environment(value):
    """Environment the reviewed launch plan must have set for this station."""
    pins = {'CYCLONEDDS_URI': cyclone_xml(value).strip(), 'ROS_LOCALHOST_ONLY': '1', ENV_HASH: None}
    if value['ros_domain_id'] is not None:
        pins['ROS_DOMAIN_ID'] = str(value['ros_domain_id'])
    return pins


def source_revision(source):
    """Pinned clean checkout; uses a per-command safe.directory, never git config."""
    command = ['git', '-c', 'safe.directory='+str(source), '-C', str(source)]
    head = subprocess.check_output(command+['rev-parse', 'HEAD'], text=True, timeout=30).strip()
    dirty = subprocess.check_output(command+['status', '--porcelain', '--untracked-files=no'],
                                    text=True, timeout=60).strip()
    return head, bool(dirty)


def preflight(config_path, environ, *, reset_snapshot, source, geteuid=None, namespace_check=None,
              revision=source_revision):
    """Load and cross-check every pin. Raises ValueError/OSError; no side effects."""
    from isaac.reset.snapshot import load_snapshot
    from isaac.workcell.layout import canonical_bytes as layout_bytes, digest as layout_digest
    expected = environ.get(ENV_HASH)
    if not isinstance(expected, str) or not HASH.fullmatch(expected):
        raise ValueError(ENV_HASH+' must carry the pinned raw config SHA-256')
    value = load(config_path, expected)
    for key, required in expected_environment(value).items():
        if required is not None and environ.get(key) != required:
            raise ValueError('Launch environment does not match station config: '+key)
    if value['ros_domain_id'] is None and 'ROS_DOMAIN_ID' in environ:
        raise ValueError('Launch environment sets ROS_DOMAIN_ID for a station without ROS')
    geteuid = geteuid or getattr(os, 'geteuid', None)
    if geteuid is None:
        raise OSError('Supervised station service requires Linux')
    if geteuid() != value['allowed_uid']:
        raise ValueError('Service must run as the provisioned station UID')
    if namespace_check is None:
        from isaac.stations.isolation import check_namespace as namespace_check
    namespace = namespace_check()
    head, dirty = revision(source)
    if head != value['source_revision'] or dirty:
        raise ValueError('Source checkout does not match the pinned clean revision')
    layout_path = Path(source)/'apparatus/workcell_layout.json'
    layout = json.loads(layout_path.read_bytes())
    if layout_bytes(layout) != layout_path.read_bytes() or layout_digest(layout) != value['layout_sha256']:
        raise ValueError('Workcell layout does not match the pinned layout hash')
    snapshot = load_snapshot(reset_snapshot, value['reset_snapshot_sha256'])
    return dict(config=value, config_sha256=expected, layout=layout, snapshot=snapshot, namespace=namespace)


def parser():
    result = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    result.add_argument('--station-config', type=Path, required=True)
    result.add_argument('--reset-snapshot', type=Path, required=True)
    result.add_argument('--runtime-dir', type=Path, required=True)
    result.add_argument('--output', type=Path, required=True)
    result.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[2])
    result.add_argument('--observer-camera', action='store_true',
                        help='Create the observer camera prim so the scene matches a camera-bearing pinned hash')
    return result


def refuse(error, output=None, value=None, config_sha256=None):
    reason = type(error).__name__+': '+str(error)
    journaled = False
    if output is not None and value is not None and config_sha256 is not None:
        journaled = record_refusal(Path(output)/'lifecycle.jsonl', station_id=value['station_id'],
                                   config_sha256=config_sha256, reason=reason)
    print(json.dumps(dict(event='start_refused', reason=reason, journaled=journaled)), file=sys.stderr, flush=True)
    return EXIT_REFUSED


def main(argv=None):
    cli = parser()
    args, isaac_args = cli.parse_known_args(argv)
    try:
        pins = preflight(args.station_config, os.environ, reset_snapshot=args.reset_snapshot, source=args.source)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        return refuse(error)
    value, config_sha256 = pins['config'], pins['config_sha256']
    try:
        if not args.output.is_dir() or args.output.is_symlink():
            raise ValueError('Existing private output directory required')
        from isaac.stations.gateway import prepare_runtime
        layout = prepare_runtime(args.runtime_dir, value['allowed_uid'])
        journal = LifecycleJournal(args.output/'lifecycle.jsonl', station_id=value['station_id'],
                                   config_sha256=config_sha256)
    except (ValueError, OSError) as error:
        return refuse(error, args.output, value, config_sha256)
    try:
        from isaac.stations.runtime import build_station
        from isaac.stations.supervisor import StationSupervisor
        components, step, identity = build_station(pins, journal=journal, runtime=layout, output=args.output,
                                                    isaac_args=isaac_args, observer_camera=args.observer_camera)
        identity.update(restart=journal.identity, namespace=pins['namespace'])
        return StationSupervisor(journal, components, step, identity=identity).run()
    finally:
        journal.close()


if __name__ == '__main__':
    raise SystemExit(main())
