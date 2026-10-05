"""Preflight all kinematic keyframes before exposing a demonstration service.

The virtual grip is an engineering 60 mm palm offset. It is visibly inspectable
and rigidly coupled to actual palm readback, but does not assert fingertip contact.
"""
from copy import deepcopy
import math

from .geometry import add, compose, relative, pose, mul, inverse, axis_angle, rotate
from .semantics import consequence


def compile_plan(backend, layout, neutral, action, target):
    plan = consequence(layout, neutral, action, target)
    initial = list(backend.neutral['joint_positions_rad'])
    primary, expected = plan['primary'], plan['expected']
    side = 'right' if target.startswith('tray_') else 'left'
    knots, attachments = [(0., initial)], []
    q = initial

    def move(u, hand, position, orientation=None, closed=0.):
        nonlocal q
        q = backend.fingers(q, hand, closed)
        q, actual = backend.solve(q, hand, position, orientation, label=f'{action}/{target}/{u}')
        knots.append((u, list(q)))
        return actual

    original = pose(neutral[primary])
    destination = pose(expected[primary])
    plan['kind'] = 'carry' if plan['carried_group'] or action == 'FLIP_CARD' else 'contact'
    if plan['kind'] == 'carry':
        if action == 'FLIP_CARD':
            plan['carried_group'] = [primary]
            destination = (original[0], mul(original[1], axis_angle([1, 0, 0], math.pi)))
        pickup = add(original[0], [0., 0., .06])
        move(.10, side, add(pickup, [0., 0., .06]))
        palm = move(.20, side, pickup)
        grip = relative(palm, original)
        move(.24, side, pickup, palm[1], 1.)
        attachments.append(dict(start=.20, end=.70, side=side, relative_pose=grip))

        def carry(u, hand, object_pose, offset, closed=1.):
            # palm * grip = object, hence palm = object * inverse(grip).
            iq = inverse(offset[1])
            inverted = (rotate(iq, [-x for x in offset[0]]), iq)
            wanted = compose(object_pose, inverted)
            return move(u, hand, wanted[0], wanted[1], closed)

        carry(.32, side, (add(original[0], [0, 0, .12]), original[1]), grip)
        if action == 'REMOVE_ONE':
            handoff_pose = ([.16, 0., 1.04], original[1])
            carry(.42, side, handoff_pose, grip)
            # Both virtual grip frames meet, while the palms remain separated.
            left_palm = move(.48, 'left', add(handoff_pose[0], [0., .075, .02]))
            left_grip = relative(left_palm, handoff_pose)
            move(.50, 'left', left_palm[0], left_palm[1], 1.)
            attachments[0]['end'] = .50
            attachments.append(dict(start=.50, end=.74, side='left', relative_pose=left_grip))
            plan['handoff'] = dict(at=.50, source='right', destination='left',
                                   source_relative=grip, destination_relative=left_grip)
            side, grip = 'left', left_grip
            carry(.62, side, (add(destination[0], [0, 0, .12]), destination[1]), grip)
            carry(.70, side, destination, grip)
            move(.74, side, backend.palm(side)[0], backend.palm(side)[1], 0.)
            plan['release'] = .74
        else:
            carry(.50, side, (add(destination[0], [0, 0, .12]), destination[1]), grip)
            carry(.64, side, destination, grip)
            palm = backend.palm(side)
            move(.70, side, palm[0], palm[1], 0.)
            plan['release'] = .70
        palm = backend.palm(side)
        move(.82, side, add(palm[0], [0, 0, .10]), palm[1])
    else:
        p = list(original[0])
        plan['contact_start'], plan['contact_end'] = .25, .70
        if action == 'CLOSE':
            definition = next(item for item in layout['objects'] if item['id'] == primary)
            radius = definition['dimensions_m'][0]
            start_angle = definition['open_angle_rad']
            plan['contact_path'] = []
            for u, fraction in ((.10, 1.), (.25, 1.), (.34, .8), (.43, .6), (.52, .4), (.61, .2), (.70, 0.)):
                angle = start_angle*fraction
                edge = add(p, [radius*math.cos(angle), 0, -radius*math.sin(angle)+.06+(.06 if u == .10 else 0)])
                actual = move(u, side, edge)
                if u >= .25:
                    plan['contact_path'].append((u, fraction, actual[0]))
        elif action == 'ALIGN_ARROW':
            # Approach the turning control from above, then turn the wrist with it.
            actual = move(.10, side, add(p, [0, 0, .12]))
            actual = move(.25, side, add(p, [0, 0, .06]))
            start_q = actual[1]
            angle0 = neutral[primary]['state']['arrow_angle_rad']
            for u, fraction in ((.40, 1/3), (.55, 2/3), (.70, 1.)):
                move(u, side, actual[0], mul(axis_angle([0, 0, 1], -angle0*fraction), start_q), 1.)
        else:  # SCAN is a visible dwell at the printed code; result stays private.
            move(.10, side, add(p, [0., 0., .12]))
            actual = move(.25, side, add(p, [0., 0., .06]))
            move(.70, side, actual[0], actual[1])
        palm = backend.palm(side)
        move(.82, side, add(palm[0], [0, 0, .10]))
    knots.append((1., initial))
    # All semantic boundaries coincide with a recorded sample, so attachment,
    # handoff and release never depend on stepping past an unsampled keyframe.
    from .runtime import SAMPLE_COUNT
    tick = lambda value: round(value*(SAMPLE_COUNT-1))/(SAMPLE_COUNT-1)
    knots = [(tick(u), value) for u, value in knots]
    for item in attachments:
        item['start'], item['end'] = tick(item['start']), tick(item['end'])
    for key in ('release', 'contact_start', 'contact_end'):
        if key in plan:
            plan[key] = tick(plan[key])
    if 'handoff' in plan:
        plan['handoff']['at'] = tick(plan['handoff']['at'])
    plan.update(knots=knots, attachments=attachments, joint_names=list(backend.names),
                virtual_grip_offset_m=.06, grasp_contact_validated=False, collision_reviewed=False)
    backend.write(initial)
    return plan
