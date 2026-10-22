"""Preflight all kinematic keyframes before exposing a demonstration service.

The virtual grip is an engineering palm offset, with a side grip for cards.
It is rigidly coupled to actual palm readback, but does not assert fingertip contact.

ADD_ONE uses a supply-cup corridor instead of the earlier thumb/middle pinch,
which put distal links inside the 4 mm cup walls. One straight middle finger
descends vertically to the top washer's ring with every other hand link above
the rim. The finger posture is fixed from corridor entry to release. The palm
orientation (fingers down, optionally leaning by a bounded tilt) is fixed
inside the cup; after the corridor exit, when the whole declared hand and the
carried washer are clear of the cup, it turns about the vertical in small
keyframes to the placement yaw, which keeps the washer level. The declared
envelope in ``grip_geometry`` must keep the declared margin from every wall at
every corridor keyframe and straight segment and along the joint-interpolated
path actually commanded (evaluated through the backend at <= 1 mm spacing), or
the plan is refused. The attachment remains kinematic: no grasp force is claimed.
"""
from copy import deepcopy
import math

from .backend import IK_ORIENTATION_LIMIT_RAD, IK_POSITION_LIMIT_M
from .geometry import (add, rotation_angle as quaternion_angle, compose, relative, pose, mul, inverse, axis_angle,
                       rotate, lerp)
from .grip_geometry import (HAND_REACH_M, PICKUP_TILTS, hand_orientation, pad_posture, path_clearance,
                            refusal_message, require_planned_clearance, supply_cup_corridor)
from .semantics import consequence


WASHER_YAWS = (0., math.pi/2, -math.pi/2, math.pi, math.pi/4, -math.pi/4, 3*math.pi/4, -3*math.pi/4)
# Pickup and placement palm yaws (45-degree grid, so every pair differs by a
# washer yaw that preserves the 32-segment ring geometry).
CUP_PALM_YAWS = WASHER_YAWS
# Corridor timing (fraction of the fixed 300-sample demo).
CUP_ENTER, CUP_CONTACT, CUP_ATTACHED_DWELL, CUP_EXIT = .06, .20, .24, .38
TRANSPORT_END, CUP_PLACE, CUP_RELEASE = .50, .64, .70
TRANSPORT_STEP_M = .03                 # Carried-washer spacing of transport keyframes.
TRANSPORT_YAW_STEP_RAD = math.pi/16    # Yaw change per transport keyframe.
SWEEP_STEP_M = .001                    # Swept-path check spacing (any declared hand point).
# Screening keeps iterating to this fraction of the acceptance limits so the
# residual measures headroom; acceptance is unchanged.
HEADROOM_TOLERANCE = (.2*IK_POSITION_LIMIT_M, .2*IK_ORIENTATION_LIMIT_RAD)
SCREENED_PICKUPS_PER_TILT = 2
FULL_COMPILES_PER_TILT = 4


def ik_headroom(target, quaternion, actual, local_point=None):
    """1 - (largest fraction of an acceptance limit used); <= 0 means refused."""
    point = actual[0] if local_point is None else add(actual[0], rotate(actual[1], local_point))
    position = math.dist(point, target)
    orientation = 0. if quaternion is None else quaternion_angle(quaternion, actual[1])
    return dict(position_m=position, orientation_rad=orientation,
                headroom=1-max(position/IK_POSITION_LIMIT_M, orientation/IK_ORIENTATION_LIMIT_RAD))


def _wrap(value):
    return math.atan2(math.sin(value), math.cos(value))


def _tier(headroom):
    # Coarse bins: residuals below 20 % of the limits are not ranked further.
    return 0 if headroom >= .8-1e-9 else 1 if headroom >= .5 else 2


def compile_plan(backend, layout, neutral, action, target):
    if action == 'ADD_ONE':
        return _compile_add_one(backend, layout, neutral, target)
    errors = []
    if action == 'FLIP_CARD':
        variants = [dict(card_offset=offset, card_roll=roll) for offset in (-.075, .075)
                    for roll in (-math.pi/2, 0., math.pi/2, -math.pi/4, math.pi/4)]
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


def _variant_name(palm_yaw, tilt, tilt_direction):
    return 'yaw%+.4f/tilt%.2f@%+.4f' % (palm_yaw, tilt, tilt_direction)


def _screen_add_one(backend, layout, neutral, target, tilt, tilt_direction):
    """Bounded screen for one pickup lean.

    Solves the corridor-entry keyframe for every pickup palm yaw (iterating to
    ``HEADROOM_TOLERANCE`` so the residual measures headroom), then the
    full placement pose for every placement yaw from the best pickups, seeded
    from their entry solution. Returns candidate (pickup yaw, washer yaw) pairs
    ranked by headroom tier, then by the smallest transport yaw change.
    """
    side = 'right'
    plan = consequence(layout, neutral, 'ADD_ONE', target)
    primary = plan['primary']
    original = pose(neutral[primary])
    destination = list(plan['expected'][primary]['position_m'])
    seed = pad_posture(backend.names, list(backend.neutral['joint_positions_rad']), side)
    pickups = []
    # The entry solve is target independent (same washer, seed and seed label),
    # and the IK is deterministic, so its outcome is reused across trays.
    cache = backend.__dict__.setdefault('_add_one_entry_screen', {})
    for index, yaw in enumerate(CUP_PALM_YAWS):
        corridor = supply_cup_corridor(layout, neutral, primary, yaw, tilt=tilt, tilt_direction=tilt_direction)
        orientation, tip = corridor['orientation_xyzw'], corridor['tip_local_m']
        entry = [corridor['pick_m'][0], corridor['pick_m'][1], corridor['tip_heights_m'][0]]
        name = _variant_name(yaw, tilt, tilt_direction)
        key = (primary, name, tuple(seed), tuple(entry))
        if key not in cache:
            item = dict(palm_yaw=yaw)
            try:
                q, actual = backend.solve(seed, side, entry, orientation, label=f'ADD_ONE/{target}/screen-entry/{name}',
                                          local_point=tip, tolerance=HEADROOM_TOLERANCE,
                                          seed_label=f'ADD_ONE/supply_cup/entry/{name}')
                item.update(ik_headroom(entry, orientation, actual, tip), q=q)
            except ValueError as error:
                item['error'] = str(error)
            cache[key] = item
        pickups.append(dict(cache[key], index=index))
    feasible = sorted((p for p in pickups if 'q' in p), key=lambda p: (_tier(p['headroom']), p['index']))
    pairs, placements = [], []
    for pickup in feasible[:SCREENED_PICKUPS_PER_TILT]:
        corridor = supply_cup_corridor(layout, neutral, primary, pickup['palm_yaw'], tilt=tilt,
                                       tilt_direction=tilt_direction)
        orientation, tip = corridor['orientation_xyzw'], corridor['tip_local_m']
        contact = [corridor['pick_m'][0], corridor['pick_m'][1], corridor['tip_heights_m'][-1]]
        grip = relative((add(contact, [-v for v in rotate(orientation, tip)]), orientation), original)
        for index, placement_yaw in enumerate(CUP_PALM_YAWS):
            washer_yaw = _wrap(placement_yaw-pickup['palm_yaw'])
            washer = (destination, mul(axis_angle([0, 0, 1], washer_yaw), original[1]))
            palm_orientation = mul(washer[1], inverse(grip[1]))
            item = dict(palm_yaw=pickup['palm_yaw'], placement_yaw=placement_yaw, washer_yaw=washer_yaw)
            name = _variant_name(pickup['palm_yaw'], tilt, tilt_direction)
            try:
                q, actual = backend.solve(pickup['q'], side, washer[0], palm_orientation,
                                          label=f'ADD_ONE/{target}/screen-place/{name}/washer%+.4f' % washer_yaw,
                                          local_point=grip[0], tolerance=HEADROOM_TOLERANCE)
                item.update(ik_headroom(washer[0], palm_orientation, actual, grip[0]))
                pairs.append(dict(item, entry_headroom=pickup['headroom'], placement_q=q,
                                  rank=(_tier(min(pickup['headroom'], item['headroom'])), abs(washer_yaw),
                                        pickup['index'], index)))
            except ValueError as error:
                item['error'] = str(error)
            placements.append(item)
    pairs.sort(key=lambda p: p['rank'])
    summary = dict(tilt_rad=tilt, tilt_direction_rad=tilt_direction,
                   pickups=[{k: v for k, v in p.items() if k != 'q'} for p in pickups],
                   placements=placements,
                   pairs=[{k: v for k, v in p.items() if k not in ('rank', 'placement_q')} for p in pairs])
    return pairs, summary


def _compile_add_one(backend, layout, neutral, target):
    """Bounded search: pickup lean (fingers-down first), then screened pickup
    and placement yaws; full compile of the best-ranked pairs."""
    errors, screens = [], []
    for tilt, direction in PICKUP_TILTS:
        pairs, summary = _screen_add_one(backend, layout, neutral, target, tilt, direction)
        screens.append(summary)
        if not pairs:
            errors.append('lean %.2f@%+.4f: no screened pickup/placement pair (entry feasible %d/%d, placement '
                          'feasible 0/%d)' % (tilt, direction, sum('headroom' in p for p in summary['pickups']),
                                              len(summary['pickups']), len(summary['placements'])))
            continue
        for pair in pairs[:FULL_COMPILES_PER_TILT]:
            try:
                plan = _compile_one(backend, layout, neutral, 'ADD_ONE', target, washer_yaw=pair['washer_yaw'],
                                    palm_yaw=pair['palm_yaw'], tilt=tilt, tilt_direction=direction,
                                    placement_seed=pair['placement_q'])
            except ValueError as error:
                errors.append('%s/washer%+.4f: %s' % (_variant_name(pair['palm_yaw'], tilt, direction),
                                                      pair['washer_yaw'], error))
                continue
            plan['prior_planning_failures'] = errors
            chosen = {k: v for k, v in pair.items() if k != 'placement_q'}
            plan['variant_selection'] = dict(chosen=dict(chosen, tilt_rad=tilt, tilt_direction_rad=direction),
                                             screens=screens, rule='lean order, then headroom tier '
                                             '(>= 0.8, >= 0.5, else), then smallest transport yaw change')
            return plan
    raise ValueError("All bounded grip/keyframe variants failed: "+"; ".join(errors))


def _swept_rows(backend, knots, side, corridor, grip, start, end, attach, release):
    """Evaluate the joint-interpolated path the runtime will command between
    ``start`` and ``end`` through the backend, subdividing until no declared
    hand point (palm displacement + reach x rotation) moves more than
    ``SWEEP_STEP_M`` between consecutive rows."""
    reach = corridor['hand_reach_m']
    rows = []

    def evaluate(qa, qb, s, ua, ub):
        backend.write([a+(b-a)*s for a, b in zip(qa, qb)])
        palm = backend.palm(side)
        t = .5-math.sin(math.asin(max(-1., min(1., 1-2*s)))/3)   # Inverse of the runtime smoothstep.
        return (s, ua+(ub-ua)*t, (list(palm[0]), list(palm[1])))

    def far(a, b):
        return math.dist(a[2][0], b[2][0])+reach*quaternion_angle(a[2][1], b[2][1]) > SWEEP_STEP_M

    for (ua, qa), (ub, qb) in zip(knots, knots[1:]):
        if ua < start-1e-12 or ub > end+1e-12:
            continue
        segment = [evaluate(qa, qb, 0., ua, ub)]
        pending = [evaluate(qa, qb, 1., ua, ub)]
        while pending:
            left, right = segment[-1], pending[-1]
            if far(left, right) and right[0]-left[0] > 1e-6:
                pending.append(evaluate(qa, qb, (left[0]+right[0])/2, ua, ub))
            else:
                segment.append(pending.pop())
        for _, u, palm in segment:
            rows.append(dict(u=u, palm=palm, carried=compose(palm, grip) if attach <= u <= release else None))
    return rows


def _compile_one(backend, layout, neutral, action, target, card_offset=None, card_roll=None,
                 washer_yaw=0., palm_yaw=0., tilt=0., tilt_direction=0., placement_seed=None):
    plan = consequence(layout, neutral, action, target)
    initial = list(backend.neutral['joint_positions_rad'])
    primary, expected = plan['primary'], plan['expected']
    if action in ('ADD_ONE', 'REMOVE_ONE'):
        expected[primary]['rotation_xyzw'] = mul(axis_angle([0, 0, 1], washer_yaw), expected[primary]['rotation_xyzw'])
        plan['washer_endpoint_yaw_rad'] = washer_yaw
    side = 'right' if target.startswith('tray_') else 'left'
    knots, attachments = [(0., initial)], []
    q = initial

    def move(u, hand, position, orientation=None, closed=0., local_point=None, posture=None, **options):
        nonlocal q
        q = backend.fingers(q, hand, closed) if posture is None else posture(q)
        q, actual = backend.solve(q, hand, position, orientation, label=f'{action}/{target}/{u}',
                                  local_point=local_point, **options)
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
        # Supply-cup corridor: fixed (optionally leaning) fingers-down palm
        # orientation and pad posture from entry above the rim, down to the
        # washer ring and back out.
        corridor = supply_cup_corridor(layout, neutral, primary, palm_yaw, tilt=tilt, tilt_direction=tilt_direction)
        orientation, tip = corridor['orientation_xyzw'], corridor['tip_local_m']
        pick, heights = corridor['pick_m'], corridor['tip_heights_m']
        pad = lambda value: pad_posture(backend.names, value, side)
        name = _variant_name(palm_yaw, tilt, tilt_direction)
        corridor_knots = []
        for k, z in enumerate(heights):
            u = CUP_ENTER+(CUP_CONTACT-CUP_ENTER)*k/(len(heights)-1)
            # The entry repeats the screen exactly (same seed and tolerance).
            options = dict(tolerance=HEADROOM_TOLERANCE, seed_label=f'ADD_ONE/supply_cup/entry/{name}') if k == 0 else {}
            palm = move(u, side, [pick[0], pick[1], z], orientation, local_point=tip, posture=pad, **options)
            if k == 0:
                corridor['entry_ik'] = ik_headroom([pick[0], pick[1], z], orientation, palm, tip)
            corridor_knots.append((u, palm))
        grip = relative(palm, original)
        lift = list(reversed(heights))
        for k, z in enumerate(lift):
            u = CUP_ATTACHED_DWELL+(CUP_EXIT-CUP_ATTACHED_DWELL)*k/(len(lift)-1)
            palm = move(u, side, [pick[0], pick[1], z], orientation, local_point=tip, posture=pad)
            corridor_knots.append((u, palm))
        # The pad posture is held at every knot from entry through release.
        corridor.update(enter=CUP_ENTER, attach=CUP_CONTACT, exit=CUP_EXIT, pad_until=CUP_RELEASE,
                        transport_from=CUP_EXIT, grip_relative_pose=grip,
                        contact_local_m=relative(original, ([pick[0], pick[1], heights[-1]], [0., 0., 0., 1.]))[0])
        corridor['planned_clearance'] = require_planned_clearance(corridor, corridor_knots, grip)
        attachments.append(dict(start=CUP_CONTACT, end=CUP_RELEASE, side=side, relative_pose=grip))
        # Transport: the carried washer moves to 120 mm above its destination
        # while the palm turns about the vertical by the washer yaw in steps of
        # at most TRANSPORT_YAW_STEP_RAD (the washer stays level), then descends
        # vertically. Each keyframe constrains the full pose at the washer centre.
        exit_washer = compose(palm, grip)
        exit_q = list(q)
        above = add(destination[0], [0, 0, .12])

        def place(u, washer, fraction):
            # After the previous keyframe, try the joint-space interpolation
            # from the corridor exit to the screened placement solution.
            seeds = () if placement_seed is None else (
                ('exit_to_screened_placement', lerp(exit_q, placement_seed, fraction)),)
            return move(u, side, washer[0], mul(washer[1], inverse(grip[1])), local_point=grip[0], posture=pad,
                        seed_label=f'ADD_ONE/{target}/{name}/washer%+.4f/{u}' % washer_yaw, extra_seeds=seeds)
        count = max(2, math.ceil(abs(washer_yaw)/TRANSPORT_YAW_STEP_RAD-1e-9),
                    math.ceil(math.dist(exit_washer[0], above)/TRANSPORT_STEP_M-1e-9))
        for k in range(1, count+1):
            place(CUP_EXIT+(TRANSPORT_END-CUP_EXIT)*k/count,
                  (lerp(exit_washer[0], above, k/count), mul(axis_angle([0, 0, 1], washer_yaw*k/count), original[1])),
                  k/count)
        drop = math.ceil(.12/TRANSPORT_STEP_M-1e-9)
        for k in range(1, drop+1):
            place(TRANSPORT_END+(CUP_PLACE-TRANSPORT_END)*k/drop, (lerp(above, destination[0], k/drop), destination[1]),
                  1.)
        palm = backend.palm(side)
        placed = compose(palm, grip)
        if math.dist(placed[0], destination[0]) > .0015 or quaternion_angle(placed[1], destination[1]) > .012:
            raise ValueError('Planned placement outside the expected washer pose')
        corridor['placement_ik'] = ik_headroom(destination[0], mul(destination[1], inverse(grip[1])), palm, grip[0])
        move(CUP_RELEASE, side, palm[0], palm[1], posture=pad)  # No finger motion while attached.
        plan['release'] = CUP_RELEASE
        plan.update(cup_corridor=corridor, pickup_kind='supply_cup_vertical_fingertip',
                    pickup_orientation_source='bounded_lean_supply_cup_corridor_then_vertical_axis_transport_turn')
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
        corridor = plan['cup_corridor']
        for key in ('enter', 'attach', 'exit', 'pad_until', 'transport_from'):
            corridor[key] = tick(corridor[key])
        if [u for u, _ in knots] != sorted(set(u for u, _ in knots)):
            raise ValueError('Corridor keyframes collapsed onto the same recorded sample')
        # Every declared check on the joint-interpolated path the runtime will
        # actually command, from corridor entry through release.
        rows = _swept_rows(backend, knots, side, corridor, corridor['grip_relative_pose'], corridor['enter'],
                           plan['release'], corridor['attach'], plan['release'])
        report = path_clearance(corridor, rows, stop_on_violation=True)
        if report['violations']:
            raise ValueError(refusal_message(report['violations'][0])+' (swept joint path)')
        corridor['swept_path_clearance'] = report
        transport = [value for u, value in knots if corridor['exit'] <= u <= plan['release']]
        corridor['transport_max_joint_step_rad'] = max(max(abs(a-b) for a, b in zip(p, n))
                                                       for p, n in zip(transport, transport[1:]))
    plan.update(knots=knots, attachments=attachments, joint_names=list(backend.names),
                virtual_grip_offset_m=(abs(card_offset) if action == 'FLIP_CARD' else
                                       None if action == 'ADD_ONE' else .06),
                grasp_contact_validated=False, collision_reviewed=False)
    backend.write(initial)
    return plan
