"""Actual-state lock/hold diagnostics with explicitly synthetic demo hooks."""
import hashlib
import json
from pathlib import Path
import uuid

from .dispatcher import CommandDispatcher
from .event_log import DurableCommandLog
from .hold import make_robot_hold
from .protocol import LEGAL_PAIRS
from ..reset.benchmark import durable
from ..reset.snapshot import sha256


def run_command_check(reset_manager, output, physics_steps=240):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    events = DurableCommandLog(output/"command-events.jsonl", session_id=uuid.uuid4().hex,
                              apparatus_version="workcell-development-v1", protocol_version="unresolved-methodology")
    motions = []
    def synthetic_demo(action, target):
        motions.append((action, target))
        yield None
        return {"execution_ok": True}
    dispatcher = CommandDispatcher(reset_manager, events, station_id="engineering-fixture", allowed_client="127.0.0.1",
                                   demo_factory=synthetic_demo, hold_robot=make_robot_hold(reset_manager.adapter))
    def send(command, args=None):
        raw = json.dumps(dict(version=1, kind="private_command", control_session_id=dispatcher.control_session_id,
                              request_id=uuid.uuid4().hex, command=command, args=args or {}))
        pending = dispatcher.submit(raw, "127.0.0.1")
        for _ in range(3):
            if pending.done(): break
            dispatcher.advance()
        return pending.result(timeout=1)
    try:
        initial = send("reset")
        before = sha256(reset_manager.adapter.read_state())
        rejected = [send("demo", dict(action=action, target=target)) for action, target in sorted(LEGAL_PAIRS)]
        after = sha256(reset_manager.adapter.read_state())
        adapter = reset_manager.adapter
        held = 0
        for _ in range(physics_steps):
            adapter.robot.write_data_to_sim()
            adapter.sim.step(render=False)
            adapter.robot.update(adapter.sim.get_physics_dt())
            if not dispatcher.after_physics_step(): break
            held += 1
        send("set_mode", {"mode": "teaching"})
        accepted = [send("demo", dict(action=action, target=target)) for action, target in sorted(LEGAL_PAIRS)]
        send("set_mode", {"mode": "test"})
        changed = adapter.accessors.read_state()
        card = next(value for value in changed.values() if "card_face" in value["state"])
        card["state"]["card_face"] = 1-card["state"]["card_face"]
        adapter.accessors.apply_state(changed)
        drift_rejected = not dispatcher.after_physics_step()
        drift_not_hidden = adapter.accessors.read_state() == changed
        recovery = send("reset")
        report = dict(source="loaded_isaac_articulation_and_workcell", demo_hook="synthetic_no_motion",
                      initial_reset_ok=initial["reset_ok"], protected_rejections=sum(not result["accepted"] for result in rejected),
                      protected_state_sha256_before=before, protected_state_sha256_after=after,
                      protected_state_exactly_unchanged=before == after,
                      physics_steps_requested=physics_steps, physics_steps_neutral_verified=held,
                      synthetic_teaching_hooks_completed=sum(result["accepted"] for result in accepted),
                      actual_motion_content_validated=False, object_drift_rejected=drift_rejected,
                      object_drift_not_silently_restored=drift_not_hidden, explicit_recovery_reset_ok=recovery["reset_ok"],
                      fault_remains_latched=dispatcher.fault == "NEUTRAL_DIVERGED")
    finally:
        events.close()
    report["command_events_sha256"] = hashlib.sha256((output/"command-events.jsonl").read_bytes()).hexdigest()
    durable(output/"summary.json", (json.dumps(report, indent=2)+"\n").encode())
    return report
