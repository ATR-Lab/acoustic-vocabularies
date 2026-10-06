"""Read-only exact header qualification against explicitly supplied reviewed bytes."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read_header(data: bytes) -> list[str]:
    return data.decode('utf-8-sig').split('\n', 1)[0].rstrip('\r').split(',')


def validate_headers(trial: bytes, exposure: bytes, trial_sha256: str, exposure_sha256: str, review_sha256: str) -> dict:
    for value in (trial_sha256, exposure_sha256, review_sha256):
        if not re.fullmatch(r'[0-9a-f]{64}', value):
            raise ValueError('DATA_TEMPLATE_REVIEW_REQUIRED')
    result = {'qualified': True, 'review_evidence_sha256': review_sha256}
    for label, data, expected, public in (
        ('trial', trial, trial_sha256, 'trial-log.provisional.csv'),
        ('exposure', exposure, exposure_sha256, 'exposure-ledger.provisional.csv'),
    ):
        if len(data) >= 1048576 or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('DATA_TEMPLATE_HASH')
        actual = read_header(data)
        supported = read_header((ROOT / 'apparatus/data' / public).read_bytes())
        if len(actual) != len(supported) or len(set(actual)) != len(actual) or set(actual) != set(supported):
            raise ValueError('DATA_TEMPLATE_HEADERS')
        result[label + '_headers'] = actual
        result[label + '_template_sha256'] = expected
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('trial-template', 'exposure-template', 'trial-sha256', 'exposure-sha256', 'review-sha256'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    result = validate_headers(Path(args.trial_template).read_bytes(), Path(args.exposure_template).read_bytes(), args.trial_sha256, args.exposure_sha256, args.review_sha256)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
