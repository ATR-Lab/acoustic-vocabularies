"""Real USD accessor round-trip and tamper checks; requires pinned pxr runtime."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from pxr import Gf, Usd, UsdGeom
from isaac.workcell.layout import neutral_layout,preconditions
from isaac.workcell.build_usd import build_workcell
from isaac.workcell.state import StateAccessors


def round_trip(*, cached):
    layout=neutral_layout();stage=Usd.Stage.CreateInMemory()
    access=build_workcell(stage,layout)
    assert not access.handle_cache_enabled
    if cached: access.enable_handle_cache()
    baseline=access.read_state();environment=access.read_environment()
    detached=access.read_state();detached['tray_A']['position_m'][0]=99.
    detached['tray_A/card']['state']['card_face']=1
    detached=access.read_public_state();detached['tray_A']['position_m'][0]=99.
    detached['tray_A/card']['state']['card_face']=1
    assert access.read_state()==baseline
    # Cache authority is not the caller's mutable layout dictionary.
    layout['objects'][0]['state']['injected']='bad'
    assert access.read_state()==baseline
    layout=neutral_layout()
    assert preconditions(layout,baseline)['possible_count']==32
    public=access.read_public_state()
    assert all(public[k]=={field:value for field,value in baseline[k].items() if field not in ('collision_enabled','linear_velocity_m_s','angular_velocity_rad_s')} for k in baseline)
    changed=deepcopy(baseline)
    for value in changed.values():
        value['visible']=False;value['enabled']=False;value['collision_enabled']=True
        value['position_m'][0]+=.001;value['linear_velocity_m_s']=[.1,0.,0.]
        value['rotation_xyzw']=[0.,0.,1.,0.];value['angular_velocity_rad_s']=[0.,.1,0.]
    changed['tray_A/card']['state']['card_face']=1
    changed['tray_A/arrow']['state']['arrow_angle_rad']=.5
    changed['container_E/lid']['state']['lid_open_fraction']=.25
    changed['container_E/tag']['state']['tag_attached']=True
    changed['supply/washer_0']['state']['location']='return_cup'
    access.apply_state(changed)
    measured=access.read_state()
    for identifier,value in changed.items(): assert measured[identifier]==value
    invalid=deepcopy(changed);invalid['tray_D']['state']['unexpected']=1
    try: access.apply_state(invalid)
    except ValueError: pass
    else: raise AssertionError('Invalid state accepted')
    assert access.read_state()==measured
    access.apply_state(baseline)
    single=deepcopy(baseline['container_E']);single['position_m'][0]+=.01
    access.apply_subset({'container_E':single})
    subset_expected=deepcopy(baseline);subset_expected['container_E']=single
    assert access.read_state()==subset_expected
    try: access.apply_subset({'unknown':single})
    except ValueError: pass
    else: raise AssertionError('Unknown subset accepted')
    try: access.apply_subset({'container_E':{'position_m':[0.,0.,0.]}})
    except ValueError: pass
    else: raise AssertionError('Partial object field patch accepted')
    assert access.read_state()==subset_expected
    access.apply_state(baseline)
    root=stage.GetPrimAtPath('/World/Workcell/Objects/tray_A')
    root.GetAttribute('visibility').Set('unsupported-token')
    for read in (access.read_state,access.read_public_state):
        try: read()
        except ValueError: pass
        else: raise AssertionError('Invalid visibility token projected as visible')
    access.apply_state(baseline)
    visual=stage.GetPrimAtPath('/World/Workcell/Objects/tray_A__card/Visual')
    visual.GetAttribute('xformOp:rotateX').Set(180.)
    try: access.read_state()
    except ValueError: pass
    else: raise AssertionError('Stale visual pose accepted')
    access.apply_state(baseline)
    shader=stage.GetPrimAtPath('/World/Workcell/Materials/surface/Shader')
    shader.GetAttribute('inputs:diffuseColor').Set(Gf.Vec3f(.1,.2,.3))
    assert access.read_environment()!=environment
    appearance=deepcopy(environment)
    for value in appearance['materials'].values():
        value['diffuse_color']=[.25,.5,.75];value['roughness']=.5
    for value in appearance['lights'].values():
        value['color']=[.75,.5,.25];value['intensity']+=1.
    access.apply_environment(appearance)
    assert access.read_environment()==appearance
    assert access.read_state()==baseline
    access.apply_environment(environment);assert access.read_environment()==environment
    with tempfile.TemporaryDirectory() as folder:
        scene=Path(folder)/'workcell.usda';stage.GetRootLayer().Export(str(scene))
        reopened=Usd.Stage.Open(str(scene));assert StateAccessors(reopened,layout,enable_handle_cache=cached).read_state()==baseline
        again=Path(folder)/'again.usda';reopened.GetRootLayer().Export(str(again));assert scene.read_bytes()==again.read_bytes()
    # Hidden child geometry is not represented by root visibility and must fail.
    UsdGeom.Imageable(stage.GetPrimAtPath('/World/Workcell/Objects/tray_A__card/Visual/Card')).GetVisibilityAttr().Set('invisible')
    try: access.read_public_state()
    except ValueError: pass
    else: raise AssertionError('Unexpected child visibility accepted')
    access.close()


def run():
    for cached in (False,True): round_trip(cached=cached)
    print(json.dumps({'passed':True,'semantic_objects':60,'preconditions':32,
        'cache_modes':[False,True], 'checks':['actual USD state mutation',
        'atomic invalid rejection','stale card visual rejection','detached read values',
        'detached layout authority','appearance readback and restore',
        'stable USD save/reload bytes']}))


if __name__=='__main__':run()
