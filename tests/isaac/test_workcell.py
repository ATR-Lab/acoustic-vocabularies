from copy import deepcopy
import math
import pytest
from isaac.workcell.layout import neutral_layout, neutral_state, preconditions, canonical_bytes
from isaac.workcell.state import validate_states


def test_neutral_contract_and_deterministic_export():
    layout=neutral_layout()
    assert len(layout['objects'])==60
    assert len(set(o['prim_path'] for o in layout['objects']))==60
    assert canonical_bytes(layout)==canonical_bytes(neutral_layout())
    assert preconditions(layout,validate_states(layout,neutral_state(layout)))['possible_count']==32
    shapes={}
    for item in layout['objects']:
        shapes.setdefault(item['kind'],set()).add((tuple(item['dimensions_m']),item['material']))
    assert all(len(variants)==1 for variants in shapes.values())


def test_preconditions_are_state_sensitive():
    layout=neutral_layout(); state=neutral_state(layout)
    for key,value in state.items():
        if key.startswith('supply/'): value['visible']=False
    assert not any(p['possible'] for p in preconditions(layout,state)['pairs'] if p['action']=='ADD_ONE')
    state['tray_A/card']['enabled']=False
    state['tray_A/arrow']['state']['arrow_angle_rad']=0.
    state['container_E/lid']['state']['lid_open_fraction']=0.
    state['container_E/tag']['state']['tag_attached']=True
    state['container_E/code']['visible']=False
    state['container_F']['state']['location']='quarantine_E'
    pairs={(p['action'],p['target']):p['possible'] for p in preconditions(layout,state)['pairs']}
    for pair in [('FLIP_CARD','tray_A'),('ALIGN_ARROW','tray_A'),('CLOSE','container_E'),('TAG','container_E'),('SCAN','container_E'),('QUARANTINE','container_E')]: assert not pairs[pair]


@pytest.mark.parametrize('change',[
    lambda s:s.pop('tray_A'),
    lambda s:s['tray_A'].update(position_m=[math.nan,0,0]),
    lambda s:s['tray_A'].update(rotation_xyzw=[0,0,0,0]),
    lambda s:s['tray_A'].update(enabled=1),
    lambda s:s['tray_A/card']['state'].update(card_face=True),
    lambda s:s['container_E/lid']['state'].update(lid_open_fraction=1.1),
    lambda s:s['supply/washer_0']['state'].update(location='unregistered'),
    lambda s:s['tray_A']['state'].update(answer='forbidden'),
])
def test_invalid_full_state_rejected(change):
    layout=neutral_layout(); state=deepcopy(neutral_state(layout)); change(state)
    with pytest.raises(ValueError): validate_states(layout,state)


def test_compact_layout_and_quarantine_clearance():
    layout=neutral_layout(); objects={o['id']:o for o in layout['objects']}
    assert objects['surface_left']['dimensions_m']==objects['surface_right']['dimensions_m']
    for name in 'EFGH':
        c=objects['container_'+name]; z=objects['quarantine_'+name]
        assert abs(z['position_m'][1]-c['position_m'][1])>(c['dimensions_m'][1]+z['dimensions_m'][1])/2
    assert all(abs(a['position_m'][1])<.45 and a['position_m'][0]<.41 for a in layout['anchors'].values())


def test_external_validator_returns_detached_full_and_public_values():
    layout=neutral_layout(); source=neutral_state(layout)
    for public_only in (False, True):
        input_state=deepcopy(source)
        if public_only:
            for item in input_state.values():
                for key in ('collision_enabled','linear_velocity_m_s','angular_velocity_rad_s'):
                    item.pop(key)
        detached=validate_states(layout,input_state,public_only=public_only)
        detached['tray_A']['position_m'][0]=99.
        detached['tray_A/card']['state']['card_face']=1
        input_state['container_E']['rotation_xyzw'][0]=99.
        assert input_state['tray_A']['position_m'][0]!=99.
        assert input_state['tray_A/card']['state']['card_face']==0
        assert detached['container_E']['rotation_xyzw'][0]!=99.


@pytest.mark.parametrize('path,expected',[
    ('/',True),('/World',True),('/World.visibility',True),
    ('/World/Workcell',True),('/World/Workcell.xformOpOrder',True),
    ('/World/Workcell/Objects/tray_A.xformOp:orient',True),
    ('/World/WorkcellOther',False),('/World/WorkcellOther.visibility',False),
    ('/World/Robot',False),('/World2',False),
])
def test_invalidation_scope_includes_properties_and_ancestors(path,expected):
    from isaac.workcell.cache_guard import affects_workcell
    assert affects_workcell(path) is expected


@pytest.mark.parametrize('resync,change,fields,invalid',[
    (['/'],[],[],True),
    (['/World/Workcell/Objects/tray_A.workcell:enabled'],[],[],True),
    (['/World/Robot'],[],[],False),
    ([],['/World/Workcell/Objects/tray_A.workcell:enabled'],['default'],False),
    ([],['/World/Workcell/Objects/tray_A.workcell:enabled'],['typeName'],True),
    ([],['/World/Workcell/Objects/tray_A.workcell:enabled'],['timeSamples'],True),
    ([],['/World/Workcell/Objects/tray_A.material:binding'],['targetPaths'],True),
])
def test_notice_policy_latches_structural_fault(resync,change,fields,invalid):
    from isaac.workcell.state import StateAccessors
    class Notice:
        def GetResyncedPaths(self): return resync
        def GetChangedInfoOnlyPaths(self): return change
        def GetChangedFields(self,path): return fields
    access=StateAccessors.__new__(StateAccessors)
    access._structure_changed=False; access._structure_error=None
    access._allowed_changes={'/World/Workcell/Objects/tray_A.workcell:enabled'}
    access._on_stage_changed(Notice(),None)
    assert access._structure_changed is invalid
    if invalid:
        first_error=access._structure_error
        access._invalidate('another fault')
        assert access._structure_error==first_error


def test_cache_rejects_missing_required_notice_binding(monkeypatch):
    import sys
    from types import SimpleNamespace
    from isaac.workcell.cache_guard import HandleCacheGuard
    notices=SimpleNamespace(ObjectsChanged=SimpleNamespace(GetChangedFields=object()))
    fake=SimpleNamespace(Sdf=SimpleNamespace(Notice=SimpleNamespace()),
                         Tf=SimpleNamespace(Notice=SimpleNamespace()),Usd=SimpleNamespace(Notice=notices))
    monkeypatch.setitem(sys.modules,'pxr',fake)
    with pytest.raises(RuntimeError,match='missing USD bindings'):
        HandleCacheGuard(None,lambda reason:None)


def test_unreadable_change_notice_fails_closed():
    from isaac.workcell.state import StateAccessors
    class BrokenNotice:
        def GetResyncedPaths(self): raise RuntimeError('binding failed')
    access=StateAccessors.__new__(StateAccessors)
    access._structure_changed=False; access._structure_error=None
    access._on_stage_changed(BrokenNotice(),None)
    assert access._structure_changed
    assert access._structure_error=='USD change notice could not be validated'

