"""Synthetic projection comparator tests; actual run is separate evidence."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from isaac.commands.published_benchmark import public_neutral
from isaac.publisher.protocol import PublicRegistry
from isaac.reset.manager import Tolerances


def fixture():
    frame=json.loads((Path(__file__).parents[1]/'fixtures/publisher-state.json').read_text())
    objects={o['id']:{k:v for k,v in o.items() if k!='id'} for o in frame['objects']}
    registry=PublicRegistry(frame['station_id'],frame['scene_sha256'],frame['reset_snapshot_sha256'],tuple(frame['joint_names']),
                            tuple((o['id'],tuple(sorted(o['state']))) for o in frame['objects']))
    neutral=dict(robot=dict(joint_positions_rad=deepcopy(frame['joint_positions'])),objects=deepcopy(objects))
    return frame,registry,neutral


def test_every_public_field_compared_and_dynamic_envelope_ignored():
    frame,registry,neutral=fixture();frame['seq']=99;frame['sim_step']=100
    result=public_neutral(frame,registry,neutral,Tolerances())
    assert result['neutral'] and result['max_deviation']==dict(joint_rad=0.,position_m=0.,orientation_rad=0.)


@pytest.mark.parametrize('mutate',[
    lambda f:f['joint_positions'].__setitem__(0,1.),
    lambda f:f['objects'][0]['position_m'].__setitem__(1,.01),
    lambda f:f['objects'][0].__setitem__('rotation_xyzw',[0,0,1,0]),
    lambda f:f['objects'][0].__setitem__('visible',not f['objects'][0]['visible']),
    lambda f:f['objects'][0].__setitem__('enabled',not f['objects'][0]['enabled']),
    lambda f:f['objects'][0]['state'].__setitem__('card_face',1-f['objects'][0]['state']['card_face']),
])
def test_public_drift_never_passes(mutate):
    frame,registry,neutral=fixture();mutate(frame)
    assert not public_neutral(frame,registry,neutral,Tolerances())['neutral']


def test_missing_object_is_refused_and_quaternion_double_cover_is_same():
    frame,registry,neutral=fixture();frame['objects'][0]['rotation_xyzw']=[-v for v in frame['objects'][0]['rotation_xyzw']]
    assert public_neutral(frame,registry,neutral,Tolerances())['neutral']
    frame['objects']=[]
    with pytest.raises(ValueError):public_neutral(frame,registry,neutral,Tolerances())
