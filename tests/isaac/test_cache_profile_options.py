"""Reject ambiguous opt-in measurements before simulator/network startup."""
from types import SimpleNamespace

import pytest

from isaac.workcell.run_scene import validate_cache_profile


def options(**changes):
    values = dict(e2e_handle_cache=True, reset_check=True, skip_reach=True,
                  capture=False, e2e_seconds=40., publisher_seconds=0.,
                  command_check=False, published_command_check=False,
                  disconnect_check=False, demo_check=False, demo_preflight=False,
                  grip_check=False, protected_stream_seconds=0., same_iteration_check=False)
    return SimpleNamespace(**(values | changes))


def test_normal_runner_does_not_require_a_cache_profile():
    validate_cache_profile(SimpleNamespace(e2e_handle_cache=False))


@pytest.mark.parametrize('capture', [False, True])
def test_explicit_joined_profile_preserves_precaptured_camera_choice(capture):
    validate_cache_profile(options(capture=capture))


@pytest.mark.parametrize('changes', [
    {'reset_check': False}, {'skip_reach': False},
    *({'e2e_seconds': value} for value in (0, 4, 3601, float('nan'), float('inf'))),
    *({key: True} for key in ('command_check', 'published_command_check',
       'disconnect_check', 'demo_check', 'demo_preflight', 'grip_check', 'same_iteration_check')),
    {'publisher_seconds': 30}, {'protected_stream_seconds': 30},
])
def test_ambiguous_or_unbounded_cache_profiles_refuse(changes):
    with pytest.raises(ValueError, match='Experimental handle cache'):
        validate_cache_profile(options(**changes))
