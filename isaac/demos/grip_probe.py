"""One-item Dex3 mesh-derived grip diagnostic; never a study-ready demo library.

The candidate uses the pinned public G1 distal-finger meshes. Contact points are
vertices, not force measurements. All contact/collision acceptance stays false
until actual close-up review; the existing forty-item library is unchanged.
"""
from copy import deepcopy
import math
from pathlib import Path
import traceback

from .backend import IsaacMotionBackend
from .geometry import add, axis_angle, compose, inverse, mul, pose, relative, rotate
from .recording import write_json
from .runtime import DemoLibrary, SAMPLE_COUNT
from .semantics import consequence


FINGER_NAMES = ('thumb_0', 'thumb_1', 'thumb_2', 'middle_0', 'middle_1')
OPEN = (.4265275532703942, -.5351773519937454, -.9776645581707265,
        .5203292792259795, .4922250493144723)
CLOSED = (.46904501904337254, -.47242156187987394, -.7259202466460886,
          .7592466784828146, .7389872606095471)
# The +0.5mm Y margin raises the palm/finger meshes above the supporting plane.
GRIP_POINT = (.09825324379877447, .08064380736054835, -.028503385021760003)
CONTACT_POINTS = {
    'right_hand_thumb_2_link': (-.0021880036219954447, .05162939429283141, .004594020545482632),
    'right_hand_middle_1_link': (.0470886118710041, .003626005025580522, -.0005778601625934258),
}
MESH_SHA256 = {
    'right_hand_thumb_2_link.STL': '3f1bfb37668e8f61801c8d25f171fa1949e08666be86c67acad7e0079937cc45',
    'right_hand_middle_1_link.STL': 'c9c34efce4563cacdcfd29fc838a982976d40f5442c71219811dcbbf3923a33d',
}


def cylinder_distance(point, radius=.0125, half_height=.003):
    """Signed distance to the public washer's finite Z-axis outer cylinder.

    The hole is deliberately excluded: this screens outer rim contact only.
    """
    radial = math.hypot(point[0], point[1])-radius
    axial = abs(point[2])-half_height
    return min(max(radial, axial), 0.)+math.hypot(max(radial, 0.), max(axial, 0.))


def hand_positions(backend, source, closure):
    if not math.isfinite(closure) or not 0. <= closure <= 1.:
        raise ValueError('Grip closure must be in [0,1]')
    result = list(source)
    for suffix, a, b in zip(FINGER_NAMES, OPEN, CLOSED):
        index = backend.names.index('right_hand_'+suffix+'_joint')
        result[index] = a+(b-a)*closure
    for suffix in ('index_0', 'index_1'):
        index = backend.names.index('right_hand_'+suffix+'_joint')
        result[index] = backend.neutral['joint_positions_rad'][index]
    return result


def compile_grip_plan(backend, layout, neutral):
    """Only the pickup for ADD_ONE/tray_A, then replace in the supply cup.

    The transfer is deliberately excluded until actual grip geometry is reviewed.
    """
    failures = []
    for yaw in (0., math.pi/2, -math.pi/2, math.pi, math.pi/4, -math.pi/4):
        plan = consequence(layout, neutral, 'ADD_ONE', 'tray_A')
        definition = next(row for row in layout['objects'] if row['id'] == plan['primary'])
        if definition['dimensions_m'] != [.025, .025, .006]:
            raise ValueError('Grip candidate is only defined for the 25x25x6mm washer')
        initial = list(backend.neutral['joint_positions_rad'])
        q, knots = initial, [(0., initial)]
        orientation = mul(axis_angle([0, 0, 1], yaw), axis_angle([1, 0, 0], -math.pi/2))
        original = pose(neutral[plan['primary']])
        destination = original
        plan['expected'] = deepcopy(neutral)
        plan['diagnostic_stage'] = 'pickup_and_replace_only'

        def move(u, point, closure, local_point=GRIP_POINT):
            nonlocal q
            q = hand_positions(backend, q, closure)
            q, actual = backend.solve(q, 'right', point, orientation,
                label=f'grip_probe/ADD_ONE/tray_A/{yaw}/{u}', local_point=local_point)
            knots.append((round(u*(SAMPLE_COUNT-1))/(SAMPLE_COUNT-1), list(q)))
            return actual

        try:
            move(.10, add(original[0], [0, 0, .09]), 0.)
            move(.20, original[0], 0.)
            palm = move(.24, original[0], 1.)
            grip = relative(palm, original)
            move(.34, add(original[0], [0, 0, .12]), 1., grip[0])
            move(.50, add(destination[0], [0, 0, .12]), 1., grip[0])
            move(.64, destination[0], 1., grip[0])
            move(.70, destination[0], 0., grip[0])
            move(.82, add(destination[0], [0, 0, .09]), 0., grip[0])
            knots.append((1., initial))
            tick = lambda u: round(u*(SAMPLE_COUNT-1))/(SAMPLE_COUNT-1)
            plan.update(kind='carry', knots=knots, joint_names=list(backend.names),
                attachments=[dict(start=tick(.24), end=tick(.64), side='right', relative_pose=grip)],
                release=tick(.64), palm_local_grip_point_m=list(GRIP_POINT), palm_yaw_rad=yaw,
                prior_planning_failures=failures, grasp_contact_validated=False, collision_reviewed=False)
            backend.write(initial)
            return plan
        except ValueError as error:
            failures.append(str(error))
    backend.write(initial)
    raise ValueError('Bounded mesh-derived grip search failed: '+'; '.join(failures))


def run_grip_check(manager, layout, output, *, capture_image=None):
    """Actual articulated one-item diagnostic with independent distal-link poses.

    Rendering occurs outside timing measurement. This is not the forty-file
    recording suite and creates no playable orientation/session index.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    adapter, neutral = manager.adapter, manager.neutral_state
    backend = IsaacMotionBackend(adapter, neutral['robot'])
    summary = dict(kind='actual_Dex3_one_item_grip_diagnostic', action='ADD_ONE', target='tray_A',
        diagnostic_stage='pickup_and_replace_only', complete_action_demonstrated=False,
        scene_sha256=adapter.scene_sha256, reset_snapshot_sha256=manager.reset_snapshot_sha256,
        mesh_sha256=MESH_SHA256, completed=False, reset_ok=False,
        grasp_contact_validated=False, collision_reviewed=False, rows=[])
    try:
        if not manager.reset()['reset_ok']:
            raise ValueError('Initial neutral reset failed')
        plan = compile_grip_plan(backend, layout, neutral['objects'])
        write_json(output/'plan.private.json', plan)
        write_json(output/'ik-results.json', backend.ik_results)
        if not manager.reset()['reset_ok']:
            raise ValueError('Post-planning neutral reset failed')
        library = DemoLibrary(backend, adapter.accessors, neutral['objects'], {('ADD_ONE', 'tray_A'): plan})
        steps = library('ADD_ONE', 'tray_A')
        inspect = {round(u*(SAMPLE_COUNT-1)) for u in (.10, .20, .24, .34, .50, .64, .70)}
        for index, _ in enumerate(steps):
            if index not in inspect:
                continue
            measured = adapter.read_state()
            prop = pose(measured['objects'][plan['primary']])
            contacts = []
            for link, local in CONTACT_POINTS.items():
                world = compose(pose(measured['frames'][link]), (local, [0., 0., 0., 1.]))[0]
                local_prop = relative(prop, (world, [0., 0., 0., 1.]))[0]
                contacts.append(dict(link=link, mesh_vertex_local_m=list(local), world_position_m=world,
                    washer_outer_surface_signed_distance_m=cylinder_distance(local_prop)))
            summary['rows'].append(dict(sample=index, washer=measured['objects'][plan['primary']],
                joint_positions_rad=measured['robot']['joint_positions_rad'],
                frames={k: v for k, v in measured['frames'].items() if k.startswith('right_hand_')},
                contacts=contacts))
            write_json(output/'summary.json', summary)
            if capture_image:
                capture_image(output/f'{index:03d}-observer.png')
                center = prop[0]
                capture_image(output/f'{index:03d}-close.png',
                    eye=add(center, [.23, -.25, .17]), look=center)
        summary['completed'] = library.last_result['execution_ok']
        summary['execution'] = deepcopy(library.last_result)
    except Exception:
        summary['error'] = traceback.format_exc()
    finally:
        summary['reset_ok'] = manager.reset()['reset_ok']
        write_json(output/'summary.json', summary)
    return summary
