import hashlib
import json
from pathlib import Path


def test_materialized_lfs_fixture_hashes():
    root = Path(__file__).parent / "fixtures"
    expected = json.loads((root / "sha256.json").read_text())
    for name, digest in expected.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
