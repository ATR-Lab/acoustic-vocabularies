"""The committed Unity fixed-step fixture is exactly what the #56 recorder writes."""
import json
from pathlib import Path

import pytest

from isaac.demos import unity_fixture
from isaac.demos.recording import read_trajectory, validate_suite, playback_index, SCHEDULE
from isaac.demos.runtime import NOMINAL_DURATION_SECONDS
from isaac.publisher.protocol import PublicRegistry

FIXTURE = Path(__file__).resolve().parent/'fixtures'/'fixed-step-demo'


def load(name):
    return json.loads((FIXTURE/name).read_bytes())


def test_committed_fixture_matches_a_fresh_recorder_run():
    assert unity_fixture.check(FIXTURE) == []


def test_generator_refuses_to_overwrite(tmp_path):
    (tmp_path/'fixture').mkdir()
    with pytest.raises(FileExistsError):
        unity_fixture.generate(tmp_path/'fixture')


def test_fixture_trajectory_passes_the_recorder_replay_check():
    manifest, index = load('manifest.json'), load('index.synthetic.json')
    registry = PublicRegistry(manifest['station_id'], manifest['scene_sha256'], manifest['reset_snapshot_sha256'],
                              tuple(manifest['joint_names']), (('card', ('card_face',)),), ())
    capture = index['rows'][0]['capture']
    frames = read_trajectory(FIXTURE/capture['file'], capture['sha256'], registry)
    assert [frame['sim_step'] for frame in frames] == [2*(i+1) for i in range(300)]
    # The retained host stamps are unpaced provenance: they overrun 10 s and
    # include a stall far above one frame. Playback must not use them.
    assert capture['capture_host_seconds'] > NOMINAL_DURATION_SECONDS
    assert capture['capture_interval_ms']['max'] > 700
    assert manifest['synthetic'] is True and manifest['isaac_evidence'] is False


def test_fixture_index_is_one_fixed_duration_suite():
    index = load('index.synthetic.json')
    assert len(index['rows']) == 40 and sum(row['group'] == 'orientation' for row in index['rows']) == 8
    suite = validate_suite([row['capture'] for row in index['rows']])
    assert suite['identical_physics_steps'] == 600
    assert all(row['capture']['playback_clock'] == 'host_monotonic_fixed_sample_period' for row in index['rows'])
    assert index['recording_complete'] is True


def test_legacy_capture_record_is_the_pre_fixed_step_field_set():
    legacy = load('legacy-capture.json')
    assert {'timing_ok', 'measured_first_to_last_host_seconds', 'interval_ms'} <= set(legacy)
    assert not {'schedule', 'schedule_ok', 'recorded_physics_steps', 'playback_clock'} & set(legacy)
    with pytest.raises(ValueError, match='(?i)fixed'):
        validate_suite([legacy]*40)


def test_playback_table_is_the_recorder_host_clock_schedule():
    table = load('playback.json')
    assert table['schedule'] == SCHEDULE
    assert table['offsets_seconds'] == [i/30 for i in range(300)]
    assert all(playback_index(elapsed) == expected for elapsed, expected in table['index_at_elapsed_seconds'])
    assert [10., None] in table['index_at_elapsed_seconds']
