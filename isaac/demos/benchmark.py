"""Bounded actual G1 preflight, executions, replay and explicit following resets."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
import traceback
import uuid

from isaac.commands import CommandDispatcher
from isaac.commands.protocol import LEGAL_PAIRS
from isaac.commands.event_log import DurableCommandLog
from isaac.commands.hold import make_robot_hold
from isaac.publisher.protocol import PublicRegistry, StateEncoder
from .backend import IsaacMotionBackend
from .planner import compile_plan
from .recording import TrajectoryWriter, read_trajectory, write_json
from .runtime import DemoLibrary, SAMPLE_HZ, SAMPLE_COUNT, NOMINAL_DURATION_SECONDS, compare_objects
from .semantics import ORIENTATION_PAIRS


def run_demo_check(manager, layout, output, *, preflight_only=False, capture_image=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    adapter, neutral = manager.adapter, manager.neutral_state
    backend = IsaacMotionBackend(adapter, neutral['robot'])
    registry = PublicRegistry('engineering-demo', adapter.scene_sha256, manager.reset_snapshot_sha256,
        tuple(backend.names), tuple((key, tuple(sorted(value['state']))) for key, value in sorted(neutral['objects'].items())),
        tuple(layout['anchor_ids']))
    plans, preflight = {}, []
    for action, target in sorted(LEGAL_PAIRS):
        if not manager.reset()['reset_ok']:
            raise RuntimeError('Preflight reset failed')
        row = dict(action=action, target=target, feasible=False)
        start = len(backend.ik_results)
        try:
            plans[(action, target)] = compile_plan(backend, layout, neutral['objects'], action, target)
            row['feasible'] = True
        except Exception as error:
            row['error'] = str(error)
        row['ik_results'] = backend.ik_results[start:]
        preflight.append(row)
        write_json(output/'preflight.json', preflight)
    reset_after_planning = manager.reset()
    summary = dict(kind='actual_G1_kinematic_visualization', scene_sha256=adapter.scene_sha256,
        reset_snapshot_sha256=manager.reset_snapshot_sha256, planning_pairs=32,
        feasible_pairs=len(plans), planning_reset_ok=reset_after_planning['reset_ok'],
        collision_reviewed=False, grasp_contact_validated=False, methodology_review_complete=False,
        recording_complete=False, rows=[])
    write_json(output/'summary.json', summary)
    if preflight_only or len(plans) != 32 or not reset_after_planning['reset_ok']:
        return summary
    write_json(output/'plans.private.json', {action+'/'+target: plan for (action, target), plan in plans.items()})
    library = DemoLibrary(backend, adapter.accessors, neutral['objects'], plans)
    log = DurableCommandLog(output/'command-events.private.jsonl', session_id=uuid.uuid4().hex,
                            apparatus_version='workcell-development-v1', protocol_version='unresolved-methodology')
    dispatcher = CommandDispatcher(manager, log, station_id='engineering-demo', allowed_client='local-main-thread',
                                   demo_factory=library, hold_robot=make_robot_hold(adapter))

    def submit(name, args=None):
        return dispatcher.submit(json.dumps(dict(version=1, kind='private_command',
            control_session_id=dispatcher.control_session_id, request_id=uuid.uuid4().hex,
            command=name, args=args or {})), 'local-main-thread')

    def physics():
        # Exactly two 60 Hz physics steps per 30 Hz command sample. Motion then
        # overwrites articulation state kinematically before independent readback.
        for _ in range(2):
            adapter.robot.write_data_to_sim()
            adapter.sim.step(render=False)
            adapter.robot.update(adapter.sim.get_physics_dt())

    try:
        before = hashlib.sha256(json.dumps(adapter.read_state(), sort_keys=True).encode()).hexdigest()
        rejects = [submit('demo', dict(action=a, target=t)).result() for a, t in sorted(LEGAL_PAIRS)]
        after = hashlib.sha256(json.dumps(adapter.read_state(), sort_keys=True).encode()).hexdigest()
        summary['protected_real_factory'] = dict(rejected=sum(not r['accepted'] for r in rejects),
            before_sha256=before, after_sha256=after, unchanged=before == after,
            factory_ran=library.last_result is not None)
        if before != after or any(row['reason'] != 'PROTECTED_TARGET_COMMAND' for row in rejects):
            raise RuntimeError('Protected real-factory gate failed')
        submit('set_mode', dict(mode='teaching')).result()
        items = [('orientation', a, t) for a, t in ORIENTATION_PAIRS]+[('execution', a, t) for a, t in sorted(LEGAL_PAIRS)]
        for number, (group, action, target) in enumerate(items):
            initial_reset = submit('reset').result()
            if not initial_reset['reset_ok']:
                raise RuntimeError('Demo initial reset failed')
            name = f'{number:03d}.ndjson'
            writer, encoder = TrajectoryWriter(output/name, registry), StateEncoder(registry)
            future = submit('demo', dict(action=action, target=target))
            started = time.monotonic()
            completed = False
            error = None
            try:
                for index in range(SAMPLE_COUNT):
                    deadline = started+index/SAMPLE_HZ
                    remaining = deadline-time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    physics()
                    dispatcher.advance()
                    if future.done():
                        raise RuntimeError('Demo ended before its fixed sample count: '+future.result()['reason'])
                    writer.append(encoder.build(backend.positions(), adapter.accessors.read_public_state(),
                                                float(adapter.sim.current_time), index+1))
                dispatcher.advance()  # Terminal check after the last actual sample.
                completed = future.result()['accepted'] and library.last_result['execution_ok']
            except Exception:
                error = traceback.format_exc()
                submit('hold_neutral').result()
            record = writer.close(completed=completed)
            result = deepcopy(library.last_result)
            row = dict(group=group, action=action, target=target, capture=record,
                       execution=result, error=error, reset_ok=False, replay_end_state_ok=False)
            # Replay validates file hash and every v2 frame, then applies the
            # recorded measured joint/prop states to the actual articulation/USD.
            if completed:
                submit('reset').result()
                for frame in read_trajectory(output/name, record['sha256'], registry):
                    backend.write(frame['joint_positions'])
                    states = deepcopy(neutral['objects'])
                    for item in frame['objects']:
                        for key in ('position_m', 'rotation_xyzw', 'visible', 'enabled', 'state'):
                            states[item['id']][key] = deepcopy(item[key])
                    adapter.accessors.apply_state(states)
                row['replay_end_state_ok'] = not compare_objects(adapter.accessors.read_state(), plans[(action, target)]['expected'])
            row['reset_ok'] = submit('reset').result()['reset_ok']
            summary['rows'].append(row)
            write_json(output/'summary.json', summary)
            # Visual inspection frames are generated separately and never
            # included in timing evidence or used to conceal recording stalls.
            if capture_image and completed and group == 'execution':
                directory = output/f'visual-{number:03d}'
                directory.mkdir()
                iterator = library(action, target)
                for index in range(SAMPLE_COUNT):
                    next(iterator)
                    if index in (0, 60, 100, 150, 200, 250, 299):
                        capture_image(directory/f'{index:03d}.png')
                try: next(iterator)
                except StopIteration: pass
                submit('reset').result()
        summary['recording_complete'] = len(summary['rows']) == 40 and all(
            row['capture']['capture_complete'] and row['capture']['timing_ok'] and row['reset_ok'] and row['replay_end_state_ok']
            for row in summary['rows'])
        summary['joint_names_sha256'] = hashlib.sha256(('\n'.join(backend.names)+'\n').encode()).hexdigest()
        summary['station_id'] = registry.station_id
        summary['nominal_duration_seconds'] = NOMINAL_DURATION_SECONDS
        write_json(output/'index.private.json', summary)
        write_json(output/'summary.json', summary)
        return summary
    finally:
        log.close()
