"""Preflight all kinematic keyframes before exposing a demonstration service.

The virtual grip is an engineering palm offset, with a side grip for cards.
It is rigidly coupled to actual palm readback, but does not assert fingertip contact.

ADD_ONE uses a supply-cup corridor instead of the earlier thumb/middle pinch,
which put distal links inside the 4 mm cup walls. One straight middle finger
descends vertically to the top washer's ring with every other hand link above
the rim; posture and orientation stay fixed until the finger is back above the
rim. The declared envelope in ``grip_geometry`` must keep the declared margin
from every wall at every corridor keyframe and straight segment, or the plan
is refused. The attachment remains kinematic: no grasp force is claimed.
"""
from copy import deepcopy
import math

from .geometry import add, compose, relative, pose, mul, inverse, axis_angle, rotate
from .grip_geometry import pad_posture, require_planned_clearance, supply_cup_corridor
from .semantics import consequence


WASHER_YAWS = (0., math.pi/2, -math.pi/2, math.pi, math.pi/4, -math.pi/4, 3*math.pi/4, -3*math.pi/4)
CUP_PALM_YAWS = (0., math.pi/2, -math.pi/2, math.pi)
# Corridor timing (fraction of the fixed 300-sample demo).
CUP_ENTER, CUP_CONTACT, CUP_ATTACHED_DWELL, CUP_EXIT = .06, .20, .24, .38


def compile_plan(backend, layout, neutral, action, target):
    errors = []
    if action == 'FLIP_CARD':
        variants = [dict(card_offset=offset, card_roll=roll) for offset in (-.075, .075)
                    for roll in (-math.pi/2, 0., math.pi/2, -math.pi/4, math.pi/4)]
    elif action == 'ADD_ONE':
        variants = [dict(washer_yaw=yaw, palm_yaw=palm) for palm in CUP_PALM_YAWS for yaw in WASHER_YAWS]
    elif action == 'REMOVE_ONE':
        variants = [dict(washer_yaw=yaw) for yaw in WASHER_YAWS]
    else:
        variants = [dict()]
    for variant in variants:
        try:
            plan = _compile_one(backend, layout, neutral, action, target, **variant)
            plan["prior_planning_failures"] = errors
            return plan
        except ValueError as error:
            errors.append(str(error))
    raise ValueError("All bounded grip/keyframe variants failed: "+"; ".join(errors))


def _compile_one(backend, layout, neutral, action, target, card_offset=None, card_roll=None,
                 washer_yaw=0., palm_yaw=0.):
    plan = consequence(layout, neutral, action, target)
    initial = list(backend.neutral['joint_positions_rad'])
    primary, expected = plan['primary'], plan['expected']
    if action in ('ADD_ONE', 'REMOVE_ONE'):
        expected[primary]['rotation_xyzw'] = mul(axis_angle([0, 0, 1], washer_yaw), expected[primary]['rotation_xyzw'])
        plan['washer_endpoint_yaw_rad'] = washer_yaw
    side = 'right' if target.startswith('tray_') else 'left'
    knots, attachments = [(0., initial)], []
    q = initial

    def move(u, hand, position, orientation=None, closed=0., local_point=None, posture=None):
        nonlocal q
        q = backend.fingers(q, hand, closed) if posture is None else posture(q)
        q, actual = backend.solve(q, hand, position, orientation, label=f'{action}/{target}/{u}', local_point=local_point)
        knots.append((u, list(q)))
        return actual

    def carry(u, hand, object_pose, offset, closed=1., posture=None):
        # palm * grip = object, hence palm = object * inverse(grip).
        iq = inverse(offset[1])
        inverted = (rotate(iq, [-x for x in offset[0]]), iq)
        wanted = compose(object_pose, inverted)
        if u not in (.64, .70):
            return move(u, hand, object_pose[0], closed=closed, local_point=offset[0], posture=posture)
        return move(u, hand, wanted[0], wanted[1], closed, posture=posture)

    original = pose(neutral[primary])
    destination = pose(expected[primary])
    plan['kind'] = 'carry' if plan['carried_group'] or action == 'FLIP_CARD' else 'contact'
    if action == 'ADD_ONE':
        # Supply-cup corridor: fixed fingers-down orientation and pad posture
        # from entry above the rim, down to the washer ring and back out.
        corridor = supply_cup_corridor(layout, neutral, primary, palm_yaw)
        orientation, tip = corridor['orientation_xyzw'], corridor['tip_local_m']
        pick, heights = corridor['pick_m'], corridor['tip_heights_m']
        pad = lambda value: pad_posture(backend.names, value, side)
        corridor_knots = []
        for k, z in enumerate(heights):
            u = CUP_ENTER+(CUP_CONTACT-CUP_ENTER)*k/(len(heights)-1)
            palm = move(u, side, [pick[0], pick[1], z], orientation, local_point=tip, posture=pad)
            corridor_knots.append((u, palm))
        grip = relative(palm, original)
        lift = list(reversed(heights))
        for k, z in enumerate(lift):
            u = CUP_ATTACHED_DWELL+(CUP_EXIT-CUP_ATTACHED_DWELL)*k/(len(lift)-1)
            palm = move(u, side, [pick[0], pick[1], z], orientation, local_point=tip, posture=pad)
            corridor_knots.append((u, palm))
        # The pad posture is held at every knot from entry through release (.70).
        corridor.update(enter=CUP_ENTER, attach=CUP_CONTACT, exit=CUP_EXIT, pad_until=.70)
        corridor['planned_clearance'] = require_planned_clearance(corridor, corridor_knots, grip)
        attachments.append(dict(start=CUP_CONTACT, end=.70, side=side, relative_pose=grip))
        carry(.50, side, (add(destination[0], [0, 0, .12]), destination[1]), grip, posture=pad)
        carry(.64, side, destination, grip, posture=pad)
        palm = backend.palm(side)
        move(.70, side, palm[0], palm[1], posture=pad)  # No finger motion while attached.
        plan['release'] = .70
        plan.update(cup_corridor=corridor, pickup_kind='supply_cup_vertical_fingertip',
                    pickup_orientation_source='fixed_fingers_down_supply_cup_corridor')
        palm = backend.palm(side)
        move(.82, side, add(palm[0], [0, 0, .06]))
    elif plan['kind'] == 'carry':
        if action == 'FLIP_CARD':
            plan['carried_group'] = [primary]
            destination = (original[0], mul(original[1], axis_angle([1, 0, 0], math.pi)))
        pickup = add(original[0], [card_offset, 0., 0.] if action == 'FLIP_CARD' else [0., 0., .06])
        pickup_q = None if action != 'FLIP_CARD' else mul(axis_angle([1, 0, 0], card_roll), axis_angle([0, 1, 0], math.pi if card_offset > 0 else 0.))
        plan['pickup_orientation_source'] = 'pickup_solution_or_card_side_grip'
        move(.10, side, add(pickup, [0., 0., .06]))
        palm = move(.20, side, pickup, pickup_q)
        grip = relative(palm, original)
        move(.24, side, pickup, palm[1], 1.)
        attachments.append(dict(start=.20, end=.70, side=side, relative_pose=grip))
        carry(.32, side, (add(original[0], [0, 0, .12]), original[1]), grip)
        if action == 'REMOVE_ONE':
            handoff_pose = ([.16, 0., 1.04], original[1])
            carry(.42, side, handoff_pose, grip)
            handoff_pose = compose(backend.palm(side), grip)
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
        move(.82, side, add(palm[0], [0, 0, .06]))
    else:
        p = list(original[0])
        plan['contact_start'], plan['contact_end'] = .25, .70
        if action == 'CLOSE':
            definition = next(item for item in layout['objects'] if item['id'] == primary)
            radius = definition['dimensions_m'][0]
            start_angle = definition['open_angle_rad']
            plan['contact_radius_m'] = radius
            plan['contact_open_angle_rad'] = start_angle
            plan['contact_path'] = []
            arc = [(.10, 1.)]+[(.25+.45*step/10, 1-step/10) for step in range(11)]
            for u, fraction in arc:
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
            for u, fraction in [(.25+.45*step/6, step/6) for step in range(1, 7)]:
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
    if 'cup_corridor' in plan:
        for key in ('enter', 'attach', 'exit', 'pad_until'):
            plan['cup_corridor'][key] = tick(plan['cup_corridor'][key])
        if [u for u, _ in knots] != sorted(set(u for u, _ in knots)):
            raise ValueError('Corridor keyframes collapsed onto the same recorded sample')
    plan.update(knots=knots, attachments=attachments, joint_names=list(backend.names),
                virtual_grip_offset_m=(abs(card_offset) if action == 'FLIP_CARD' else
                                       None if action == 'ADD_ONE' else .06),
                grasp_contact_validated=False, collision_reviewed=False)
    backend.write(initial)
    return plan
