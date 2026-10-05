"""Public projection must stay detached and byte-identical for accepted states."""
from copy import deepcopy
import json
import math
from pathlib import Path
import random

import pytest

from isaac.publisher.benchmark import registry_from_snapshot
from isaac.publisher.protocol import StateEncoder, encode

ROOT=Path(__file__).parents[2]


def fixture():
    layout=json.loads((ROOT/'apparatus/workcell_layout.json').read_text())
    snap=json.loads((ROOT/'isaac/snapshots/neutral_v1.json').read_text())
    registry=registry_from_snapshot(layout,snap,'b'*64,'synthetic-projection',ROOT/'docs/spikes/isaac/joint_inventory.csv')
    return registry,snap['state']


def test_seeded_typed_public_projection_matches_original_deepcopy_bytes():
    registry,neutral=fixture();rng=random.Random(58)
    encoder=StateEncoder(registry,'synthetic',lambda:1234567);encoder.session_id='c'*32
    fields=('position_m','rotation_xyzw','visible','enabled','state')
    for sequence in range(250):
        objects=deepcopy(neutral['objects']);positions=[rng.uniform(-1,1) for _ in range(43)]
        for record in objects.values():
            record['position_m']=[rng.uniform(-2,2) for _ in range(3)]
            yaw=rng.uniform(-math.pi,math.pi);record['rotation_xyzw']=[0.,0.,math.sin(yaw/2),math.cos(yaw/2)]
            record['visible']=rng.choice((True,False));record['enabled']=rng.choice((True,False))
            for key in record['state']:
                record['state'][key]={'card_face':rng.randrange(2),'arrow_angle_rad':rng.uniform(-math.tau,math.tau),
                    'lid_open_fraction':rng.random(),'tag_attached':rng.choice((True,False)),
                    'location':rng.choice(sorted(registry.anchors))}[key]
        frame=encoder.build(positions,objects,sequence/30,sequence*2)
        # This independent contract projection retains the prior deepcopy
        # semantics as an oracle, including field order and float spellings.
        expected={**frame,'objects':[{'id':identifier,**{k:deepcopy(objects[identifier][k]) for k in fields}}
                                   for identifier,_ in registry.object_states]}
        assert encode(frame)==encode(expected)
        assert frame['joint_positions']==positions and frame['seq']==sequence
        objects[next(iter(objects))]['position_m'][0]+=100
        positions[0]+=100
        assert encode(frame)==encode(expected)
    assert len(frame['objects'])==60


@pytest.mark.parametrize('field,value',[('position_m',(0.,0.,0.)),('rotation_xyzw',(0.,0.,0.,1.)),('state',[])])
def test_projection_does_not_broaden_container_acceptance(field,value):
    registry,state=fixture();objects=deepcopy(state['objects']);objects[next(iter(objects))][field]=value
    encoder=StateEncoder(registry,'synthetic')
    with pytest.raises(ValueError):encoder.build(state['robot']['joint_positions_rad'],objects,0,0)
    assert encoder.sequence==0


def test_accepted_frame_and_source_are_detached_in_both_directions():
    registry,state=fixture();objects=deepcopy(state['objects']);positions=state['robot']['joint_positions_rad'][:]
    baseline=deepcopy(objects);encoder=StateEncoder(registry,'synthetic');frame=encoder.build(positions,objects,0,0)
    for record in frame['objects']:
        record['position_m'][0]+=9;record['rotation_xyzw'][3]*=-1
        record['state'].clear();record['visible']=not record['visible']
    frame['joint_positions'][0]+=9
    assert objects==baseline and positions==state['robot']['joint_positions_rad']


@pytest.mark.parametrize('mutation',[
    lambda r:r['state'].__setitem__('card_face',[]),
    lambda r:r['state'].__setitem__('extra',{'secret':True}),
    lambda r:r['position_m'].__setitem__(0,{'secret':True}),
    lambda r:r.__setitem__('visible',[]),
])
def test_nested_values_cannot_escape_as_shared_accepted_state(mutation):
    registry,state=fixture();objects=deepcopy(state['objects']);record=next(x for x in objects.values() if 'card_face' in x['state'])
    mutation(record);encoder=StateEncoder(registry,'synthetic')
    with pytest.raises(ValueError):encoder.build(state['robot']['joint_positions_rad'],objects,0,0)
    assert encoder.sequence==0
