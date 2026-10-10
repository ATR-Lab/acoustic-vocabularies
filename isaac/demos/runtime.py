"""Interruptible fixed-sample playback of preflighted actual-articulation plans."""
from copy import deepcopy
import math

from .geometry import (pose, compose, relative, sub, norm, angle, interpolate_knots,
                       mul, inverse, axis_angle)


SAMPLE_HZ = 30
SAMPLE_COUNT = 300
NOMINAL_DURATION_SECONDS = 10.
# Fixed sim-step recording schedule (#56 maintainer decision, 2026-10). Every
# recorded sample is exactly two 60 Hz physics steps, so all 40 recordings span
# 600 steps by construction. Simulation time is never a playback clock.
PHYSICS_STEPS_PER_SAMPLE = 2
PHYSICS_DT_SECONDS = 1/60
TOTAL_PHYSICS_STEPS = SAMPLE_COUNT*PHYSICS_STEPS_PER_SAMPLE
if abs(TOTAL_PHYSICS_STEPS*PHYSICS_DT_SECONDS-NOMINAL_DURATION_SECONDS) > 1e-12 \
        or abs(SAMPLE_HZ*PHYSICS_STEPS_PER_SAMPLE*PHYSICS_DT_SECONDS-1.) > 1e-12:
    raise RuntimeError('Inconsistent fixed recording schedule constants')


def compare_objects(actual, expected, *, position_tolerance=.0015, angle_tolerance=.012):
    failures = []
    if set(actual) != set(expected):
        return ['object_registry']
    for key in expected:
        a, b = actual[key], expected[key]
        if norm(sub(a['position_m'], b['position_m'])) > position_tolerance:
            failures.append(key+'/position')
        if angle(a['rotation_xyzw'], b['rotation_xyzw']) > angle_tolerance:
            failures.append(key+'/rotation')
        for field in ('state', 'visible', 'enabled', 'collision_enabled'):
            if a[field] != b[field]:
                failures.append(key+'/'+field)
        for field in ('linear_velocity_m_s', 'angular_velocity_rad_s'):
            if norm(sub(a[field], b[field])) > 1e-5:
                failures.append(key+'/'+field)
    return failures


class DemoLibrary:
    def __init__(self, backend, accessors, neutral, plans):
        self.backend, self.accessors = backend, accessors
        self.neutral, self.plans = deepcopy(neutral), plans
        self.last_result = None
        self.active = False

    def __call__(self, action, target):
        if self.active:
            raise ValueError('Only one visualization may run at a time')
        plan = self.plans[(action, target)]
        if compare_objects(self.accessors.read_state(), self.neutral):
            raise ValueError('Verified neutral objects required before every demo')
        return self._play(plan)

    def _play(self, plan):
        if self.active:
            raise ValueError('Another visualization started before this iterator')
        self.active = True
        self.last_result = None
        primary = plan['primary']
        group = plan['carried_group']
        offsets = {key: relative(pose(self.neutral[primary]), pose(self.neutral[key])) for key in group}
        current = {key: deepcopy(self.neutral[key]) for key in group}
        owner = None
        released = False
        handoff_checked = False
        contact_reference = None
        trace = []
        try:
            for index in range(SAMPLE_COUNT):
                u = index/(SAMPLE_COUNT-1)
                wanted = interpolate_knots(plan['knots'], u)
                self.backend.write(wanted)
                measured = self.backend.positions()
                joint_error = max(abs(a-b) for a, b in zip(wanted, measured))
                if joint_error > .001:
                    raise ValueError('Actual articulation differs from scripted joint keyframe')
                changed = {}
                if plan['kind'] == 'carry' and u >= plan['attachments'][0]['start'] and not released:
                    attachment = next((item for item in reversed(plan['attachments']) if u >= item['start']), None)
                    if owner is None:
                        calculated = compose(self.backend.palm(attachment['side']), attachment['relative_pose'])
                        if norm(sub(calculated[0], current[primary]['position_m'])) > .003:
                            raise ValueError('Pickup virtual grip missed the actual prop')
                    if plan.get('handoff') and not handoff_checked and u >= plan['handoff']['at']:
                        handoff = plan['handoff']
                        source = compose(self.backend.palm(handoff['source']), handoff['source_relative'])
                        destination = compose(self.backend.palm(handoff['destination']), handoff['destination_relative'])
                        separation = norm(sub(source[0], destination[0]))
                        rotation = angle(source[1], destination[1])
                        if separation > .003 or rotation > .02:
                            raise ValueError('Two-hand virtual grips do not meet; ownership unchanged')
                        handoff_checked = True
                        trace.append(dict(event='handoff', sample=index, separation_m=separation, orientation_error_rad=rotation))
                    owner = attachment['side']
                    carried = compose(self.backend.palm(owner), attachment['relative_pose'])
                    for key in group:
                        value = deepcopy(current[key])
                        p, q = compose(carried, offsets[key])
                        value['linear_velocity_m_s'] = [(b-a)*SAMPLE_HZ for a, b in zip(value['position_m'], p)]
                        delta = mul(q, inverse(value['rotation_xyzw']))
                        if delta[3] < 0: delta = [-v for v in delta]
                        size = norm(delta[:3])
                        scale = 0. if size < 1e-10 else 2*math.atan2(size, delta[3])*SAMPLE_HZ/size
                        value['angular_velocity_rad_s'] = [v*scale for v in delta[:3]]
                        value['position_m'], value['rotation_xyzw'] = p, q
                        if 'location' in value['state']:
                            value['state']['location'] = 'robot/'+owner+'_hand'
                        current[key] = value
                    if u >= plan['release']:
                        for key, value in current.items():
                            value['state'] = deepcopy(plan['expected'][key]['state'])
                            if plan['action'] == 'FLIP_CARD':
                                # Transfer the half-turn into the child's face state
                                # without changing the displayed world orientation.
                                value['rotation_xyzw'] = mul(value['rotation_xyzw'], axis_angle([1, 0, 0], -math.pi))
                            value['linear_velocity_m_s'] = [0.]*3
                            value['angular_velocity_rad_s'] = [0.]*3
                        failures = compare_objects(current, {key: plan['expected'][key] for key in group})
                        if failures:
                            raise ValueError('Placement outside expected pose: '+','.join(failures))
                        released = True
                        trace.append(dict(event='release', sample=index, owner=owner))
                    changed.update(current)
                elif plan['kind'] == 'contact' and plan['contact_start'] <= u <= plan['contact_end']:
                    value = deepcopy(self.neutral[primary])
                    palm = self.backend.palm('right' if plan['target'].startswith('tray_') else 'left')
                    if plan['action'] == 'ALIGN_ARROW':
                        contact = list(self.neutral[primary]['position_m'])
                        contact[2] += .06
                        if norm(sub(palm[0], contact)) > .006:
                            raise ValueError('Measured wrist left arrow contact region')
                        if contact_reference is None:
                            # The planner has an exact .25 knot; use its evaluated
                            # palm to avoid absorbing the first sample's turn.
                            self.backend.write(interpolate_knots(plan['knots'], plan['contact_start']))
                            contact_reference = self.backend.palm('right')[1]
                            self.backend.write(wanted)
                        delta = mul(palm[1], inverse(contact_reference))
                        yaw = math.atan2(2*(delta[3]*delta[2]+delta[0]*delta[1]), 1-2*(delta[1]**2+delta[2]**2))
                        value['state']['arrow_angle_rad'] = self.neutral[primary]['state']['arrow_angle_rad']+yaw
                    elif plan['action'] == 'CLOSE':
                        p = self.neutral[primary]['position_m']
                        radius = math.hypot(palm[0][0]-p[0], palm[0][2]-p[2]-.06)
                        # Keyframes subdivide the actual hinge arc. Do not drive
                        # the lid angle from a remote, unrelated hand position.
                        if abs(radius-plan['contact_radius_m']) > .006 or abs(palm[0][1]-p[1]) > .006:
                            raise ValueError('Measured palm left lid contact arc')
                        theta = math.atan2(-(palm[0][2]-p[2]-.06), palm[0][0]-p[0])
                        if theta > math.pi/2: theta -= math.tau
                        value['state']['lid_open_fraction'] = min(1., max(0., theta/plan['contact_open_angle_rad']))
                    if u >= plan['contact_end']:
                        if plan['action'] == 'ALIGN_ARROW' and abs(value['state']['arrow_angle_rad']) > .015:
                            raise ValueError('Measured wrist did not reach aligned slot')
                        if plan['action'] == 'CLOSE' and value['state']['lid_open_fraction'] > .015:
                            raise ValueError('Measured palm did not close lid')
                        value['state'] = deepcopy(plan['expected'][primary]['state'])
                    changed[primary] = value
                if changed:
                    self.accessors.apply_subset(changed)
                yield dict(sample=index, joint_error_rad=joint_error)
            actual = self.accessors.read_state()
            failures = compare_objects(actual, plan['expected'])
            neutral_error = max(abs(a-b) for a, b in zip(self.backend.positions(), self.backend.neutral['joint_positions_rad']))
            result = dict(execution_ok=not failures and neutral_error <= .001,
                          semantic_error=False, failures=failures, robot_neutral_error_rad=neutral_error,
                          sample_count=SAMPLE_COUNT, nominal_duration_seconds=NOMINAL_DURATION_SECONDS,
                          private_result=deepcopy(plan['private_result']), events=trace,
                          collision_reviewed=False, grasp_contact_validated=False)
            self.last_result = result
            return result
        finally:
            # Cancellation leaves the observable state where it is; the command
            # dispatcher owns the explicit reset and audit. No hidden restoration.
            self.active = False
