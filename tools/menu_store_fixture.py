"""Provision a fresh private DEMO fixture from existing pinned bank/package files.

Never generate a bank, change an option or write the source configuration/store.
The output is private operator material and must remain outside public commits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.menu_store_bridge import (
    BridgeError, EMPTY_SNAPSHOT, MenuStoreBridge, _atomic_new, _local_path,
    read_json, require, serve_mailbox, strict_loads, write_new,
)


def prepare_fixture(config_path, config_sha256, output, *, mode):
    require(mode in ('codec', 'service'), 'FIXTURE_MODE')
    raw = Path(config_path).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == config_sha256, 'CONFIG_FILE_HASH')
    output = _local_path(output, directory=True)
    require(not output.exists(), 'FIXTURE_EXISTS')
    config = strict_loads(raw)
    require(isinstance(config, dict), 'CONFIG_SHAPE')
    # Resolve existing source paths before establishing the new private store.
    for key in ('bank_path', 'package_path'):
        require(isinstance(config.get(key), str) and config[key], 'CONFIG_PATH')
        config[key] = str(Path(config[key]).absolute())
    require(isinstance(config.get('store_root'), str) and config['store_root'], 'CONFIG_PATH')
    for protected in (Path(config['package_path']).resolve(), Path(config['store_root']).resolve()):
        require(output != protected and protected not in output.parents, 'FIXTURE_SOURCE_OVERLAP')
    config['store_root'] = str(output/'store')
    bridge = MenuStoreBridge(config)
    bridge._inputs()  # Full DEMO/bank/package pins and admissibility-bound assets.
    output.mkdir(parents=True, exist_ok=False)
    write_new(output/'config.json', config)
    initial = bridge.verified_snapshot(expected_head=None, expected_snapshot_sha256=EMPTY_SNAPSHOT)
    write_new(output/'initial-snapshot.json', initial)
    mailbox = output/'mailbox'
    (mailbox/'requests').mkdir(parents=True)
    (mailbox/'responses').mkdir()
    timings = []
    latest = initial
    if mode == 'codec':
        for operation in ('profile', 'atom', 'verify'):
            request = dict(schema_version=1, request_id=uuid.uuid4().hex, operation=operation,
                           unit_id=config['unit_id'], book_id=config['book_id'],
                           bank_sha256=config['bank_sha256'], package_sha256=config['package_sha256'],
                           expected_head=latest['book_head'], expected_snapshot_sha256=latest['snapshot_sha256'],
                           profile='P1', menu_key='K-a1' if operation == 'atom' else operation,
                           rank=1 if operation == 'atom' else None)
            _atomic_new(mailbox/'requests'/(request['request_id']+'.json'), request)
            started = time.monotonic()
            serve_mailbox(bridge, mailbox, seconds=30, max_requests=1)
            response = read_json(mailbox/'responses'/(request['request_id']+'.json'))
            require(response['error'] is None and (response['receipt'] is None or response['receipt']['accepted']),
                    'FIXTURE_SELECTION_REFUSED')
            latest = response['snapshot']
            for kind, value in (('request', request), ('response', response), ('snapshot', latest)):
                write_new(output/(operation+'-'+kind+'.json'), value)
            timings.append(dict(operation=operation, seconds=time.monotonic()-started))
    summary = dict(schema_version=1, mode=mode,
                   source_kind='actual_store_with_existing_producer_DEMO_package',
                   participant_ready=False, timing_qualified=False, audio_played=False,
                   config_sha256=bridge.config_sha256,
                   config_file_sha256=hashlib.sha256((output/'config.json').read_bytes()).hexdigest(),
                   package_path=config['package_path'], package_sha256=config['package_sha256'],
                   bank_sha256=config['bank_sha256'], initial_manifest_sha256=initial['manifest_sha256'],
                   final_manifest_sha256=latest['manifest_sha256'], timings=timings,
                   files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob('*.json'))})
    write_new(output/'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mode', choices=('codec','service'), required=True)
    args = parser.parse_args()
    result = prepare_fixture(args.config, args.config_sha256, args.out, mode=args.mode)
    print(json.dumps({'status':'prepared', 'mode':result['mode'],
                      'config_sha256':result['config_sha256'],
                      'config_file_sha256':result['config_file_sha256'],
                      'initial_manifest_sha256':result['initial_manifest_sha256']}))


if __name__ == '__main__':
    try:
        main()
    except BridgeError as error:
        print(json.dumps({'error':error.code}), file=sys.stderr)
        raise SystemExit(1)
