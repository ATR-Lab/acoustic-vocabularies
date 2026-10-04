import importlib.util
from pathlib import Path
import sys
import zipfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evidence import hash_files, public_name, stats, verify_loopback_only
from fetch_assets import safe_members
from pose_evidence import target_vectors


def test_network_fails_closed(tmp_path):
    (tmp_path / "lo").mkdir()
    assert verify_loopback_only(tmp_path) == ["lo"]
    (tmp_path / "eth0").mkdir()
    with pytest.raises(RuntimeError):
        verify_loopback_only(tmp_path)
    with pytest.raises(RuntimeError):
        verify_loopback_only(tmp_path / "missing")


def test_hashes_never_export_local_paths(tmp_path):
    source = tmp_path / "config.py"
    source.write_text("synthetic config\n")
    output = tmp_path / "hashes.csv"
    rows = hash_files([source], output, {"fixture": tmp_path})
    assert rows[0]["path"] == "fixture/config.py"
    assert len(rows[0]["sha256"]) == 64
    assert str(tmp_path) not in output.read_text()
    with pytest.raises(ValueError):
        public_name(source, {})


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/../../escape", "a\\..\\escape"])
def test_archive_traversal_rejected(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(name, "x")
    with zipfile.ZipFile(archive) as stream, pytest.raises(ValueError):
        list(safe_members(stream, tmp_path / "extract"))


def test_empty_stats_are_missing_not_zero():
    assert stats([])["mean"] is None
    assert stats(range(1, 101)) == {"count": 100, "mean": 50.5, "p95": 95., "max": 100.}
    with pytest.raises(ValueError):
        stats([float("nan")])


def test_pose_targets_cover_each_joint_without_crossing_limits():
    limits = [(-1, 2), (-2, 0), (.2, .8)]
    targets = list(target_vectors([0, -1, .5], limits))
    assert len(targets) == 1 + 2 * len(limits) + 5
    assert targets == list(target_vectors([0, -1, .5], limits))
    for _, vector in targets:
        assert all(lo <= value <= hi for value, (lo, hi) in zip(vector, limits))
