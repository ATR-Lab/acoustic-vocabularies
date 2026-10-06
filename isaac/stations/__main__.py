"""Validate a provisioned private fleet without starting services or changing networks."""
import argparse
import json
from pathlib import Path

from .config import load, validate_fleet, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True,
                        help='Private JSON list of {file: basename, sha256: expected raw file hash}')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if not isinstance(manifest, list):
        raise ValueError('Explicit fleet manifest list required')
    configs = []
    for item in manifest:
        if not isinstance(item, dict) or set(item) != {'file', 'sha256'}:
            raise ValueError('Exact manifest fields required')
        name = item['file']
        if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name or '..' in name:
            raise ValueError('Config paths must be basenames inside the private directory')
        configs.append(load(args.manifest.parent/name, item['sha256']))
    validate_fleet(configs)
    print(json.dumps(dict(station_count=len(configs), config_hashes=[digest(v) for v in configs],
                         configuration_valid=True, runtime_started=False, hardware_qualified=False)))


if __name__ == '__main__':
    main()
