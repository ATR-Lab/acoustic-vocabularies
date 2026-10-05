"""Public engineering fixtures: command modes, retries and protected isolation."""
import copy
import json
import queue
import threading
import uuid
from pathlib import Path

import pytest

from test_reset import manager
from isaac.commands import CommandDispatcher, CommandQueue
from isaac.commands.protocol import LEGAL_PAIRS, COMMANDS, MODES


def setup(*, demo=True, cache_size=1024):
    adapter, snapshot, reset_events, reset = manager()
    reset.reset()
    events, motions = [], []
    def factory(action, target):
        motions.append((action, target))
        yield "safe_point"
        return {"execution_ok": True}
    def hold(value):
        adapter.state["robot"] = copy.deepcopy(value)
    dispatcher = CommandDispatcher(reset, events.append, station_id="engineering-fixture", allowed_client="127.0.0.1",
                                   demo_factory=factory if demo else None, hold_robot=hold, cache_size=cache_size)
    return adapter, snapshot, reset, dispatcher, events, motions


def command(dispatcher, name, args=None, request_id=None):
    return json.dumps({"version": 1, "kind": "private_command", "control_session_id": dispatcher.control_session_id,
                       "request_id": request_id or uuid.uuid4().hex, "command": name, "args": args or {}})


def send(dispatcher, name, args=None, **kwargs):
    future = dispatcher.submit(command(dispatcher, name, args, **kwargs), "127.0.0.1")
    for _ in range(3):
        if future.done():
            break
        dispatcher.advance()
    return future.result(timeout=.2)


def test_all_32_test_demos_rejected_with_identical_full_state():
    adapter, snapshot, _, dispatcher, events, motions = setup()
    for action, target in sorted(LEGAL_PAIRS):
        reply = send(dispatcher, "demo", dict(action=action, target=target))
        assert not reply["accepted"] and reply["reason"] == "PROTECTED_TARGET_COMMAND"
        assert adapter.state == snapshot["state"]
    assert len(events) == 32 and not motions
    assert all(event["mode"] == "test" and event["arguments"] for event in events)


@pytest.mark.parametrize("mode", ["teaching", "post_endpoint"])
def test_all_32_legal_hooks_complete_in_visible_modes(mode):
    _, _, _, dispatcher, events, motions = setup()
    assert send(dispatcher, "set_mode", {"mode": mode})["accepted"]
    for action, target in sorted(LEGAL_PAIRS):
        assert send(dispatcher, "demo", dict(action=action, target=target))["reason"] == "DEMO_COMPLETE"
    assert len(motions) == 32 and len(events) == 33


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", COMMANDS)
def test_every_command_in_every_mode(mode, name):
    _, _, _, dispatcher, events, _ = setup()
    send(dispatcher, "set_mode", {"mode": mode})
    args = {"action": "FLIP_CARD", "target": "tray_A"} if name == "demo" else {"mode": mode} if name == "set_mode" else {}
    reply = send(dispatcher, name, args)
    assert reply["accepted"] is not (name == "demo" and mode == "test")
    assert len(events) == 2


@pytest.mark.parametrize("raw", ["{", "[]", '{"version":NaN}', '{"command":"reset","command":"demo"}',
    '{"command":"health","args":{"nested":{"target":"tray_A"}}}',
    '{"command":"unknown","args":{}}'])
def test_malformed_unknown_nested_target_commands_have_no_effect(raw):
    adapter, snapshot, _, dispatcher, events, motions = setup()
    reply = dispatcher.submit(raw, "127.0.0.1").result()
    assert not reply["accepted"] and adapter.state == snapshot["state"]
    assert len(events) == 1 and not motions and events[0]["raw_command"] == raw


def test_unknown_client_is_logged_without_effect():
    adapter, snapshot, _, dispatcher, events, _ = setup()
    reply = dispatcher.submit(command(dispatcher, "set_mode", {"mode": "teaching"}), "127.0.0.2").result()
    assert reply["reason"] == "UNKNOWN_CLIENT" and dispatcher.mode == "test"
    assert adapter.state == snapshot["state"] and len(events) == 1


def test_replay_does_not_execute_and_protected_lock_precedes_cached_success():
    _, _, _, dispatcher, events, motions = setup()
    send(dispatcher, "set_mode", {"mode": "teaching"})
    identifier = uuid.uuid4().hex
    assert send(dispatcher, "demo", {"action": "SCAN", "target": "container_E"}, request_id=identifier)["accepted"]
    replay = send(dispatcher, "demo", {"action": "SCAN", "target": "container_E"}, request_id=identifier)
    assert replay["duplicate"] and len(motions) == 1
    conflict = send(dispatcher, "demo", {"action": "TAG", "target": "container_E"}, request_id=identifier)
    assert conflict["reason"] == "REQUEST_ID_CONFLICT"
    send(dispatcher, "set_mode", {"mode": "test"})
    protected = send(dispatcher, "demo", {"action": "SCAN", "target": "container_E"}, request_id=identifier)
    assert protected["reason"] == "PROTECTED_TARGET_COMMAND" and len(motions) == 1
    assert len(events) == 6


def test_session_restart_refuses_old_ids_and_cache_never_evicts():
    _, _, _, dispatcher, _, _ = setup(cache_size=1)
    old = command(dispatcher, "reset")
    assert dispatcher.submit(old, "127.0.0.1").result()["accepted"]
    assert send(dispatcher, "health")["reason"] == "IDEMPOTENCY_CAPACITY"
    assert dispatcher.submit(old, "127.0.0.1").result()["duplicate"]
    _, _, _, restarted, _, _ = setup()
    assert restarted.submit(old, "127.0.0.1").result()["reason"] == "CONTROL_SESSION_MISMATCH"


def test_pause_resume_and_stop_interrupt_only_at_explicit_safe_points():
    _, _, _, dispatcher, events, motions = setup()
    send(dispatcher, "set_mode", {"mode": "teaching"})
    future = dispatcher.submit(command(dispatcher, "demo", {"action": "SCAN", "target": "container_E"}), "127.0.0.1")
    dispatcher.advance()
    assert len(motions) == 1 and not future.done()
    send(dispatcher, "pause")
    for _ in range(5): dispatcher.advance()
    assert not future.done()
    send(dispatcher, "resume")
    dispatcher.advance()
    assert future.result()["accepted"]
    second = dispatcher.submit(command(dispatcher, "demo", {"action": "SCAN", "target": "container_E"}), "127.0.0.1")
    send(dispatcher, "stop")
    assert second.result()["reason"] == "STOP_INTERRUPTED"
    assert send(dispatcher, "resume")["reason"] == "STOPPED_RESTART_REQUIRED"


def test_test_transition_cancels_active_and_prioritizes_over_queued_demo():
    _, _, reset, dispatcher, events, motions = setup()
    send(dispatcher, "set_mode", {"mode": "teaching"})
    active = dispatcher.submit(command(dispatcher, "demo", {"action": "SCAN", "target": "container_E"}), "127.0.0.1")
    dispatcher.advance()
    handoff = CommandQueue(dispatcher)
    queued = handoff.submit(command(dispatcher, "demo", {"action": "TAG", "target": "container_E"}), "127.0.0.1")
    locked = handoff.submit(command(dispatcher, "set_mode", {"mode": "test"}), "127.0.0.1")
    handoff.drain()
    assert active.result()["reason"] == "PROTECTED_MODE_INTERRUPTED"
    assert queued.result()["reason"] == "PROTECTED_BOUNDARY_SUPERSEDED"
    assert locked.result()["reset_ok"] and dispatcher.mode == "test" and reset.exposure_ready
    assert len(motions) == 1


def test_failed_neutral_transition_still_locks_and_does_not_ack_success():
    adapter, _, _, dispatcher, _, _ = setup()
    send(dispatcher, "set_mode", {"mode": "teaching"})
    adapter.locked = True
    result = send(dispatcher, "set_mode", {"mode": "test"})
    assert not result["accepted"] and result["reset_ok"] is False and dispatcher.mode == "test"


def test_hold_restores_robot_only_and_latches_object_fault():
    adapter, _, _, dispatcher, _, _ = setup()
    adapter.state["robot"]["joint_positions_rad"][0] = .1
    adapter.state["robot"]["joint_velocities_rad_s"][0] = .1
    assert dispatcher.after_physics_step()
    adapter.state["objects"]["engineering_object_0"]["state"]["card_face"] = 1
    assert not dispatcher.after_physics_step() and dispatcher.fault == "NEUTRAL_DIVERGED"
    assert adapter.state["objects"]["engineering_object_0"]["state"]["card_face"] == 1
    send(dispatcher, "reset")
    assert dispatcher.fault == "NEUTRAL_DIVERGED"  # Explicit service restart required.


def test_missing_demo_is_not_fabricated_execution():
    _, _, _, dispatcher, _, _ = setup(demo=False)
    send(dispatcher, "set_mode", {"mode": "teaching"})
    assert send(dispatcher, "demo", {"action": "SCAN", "target": "container_E"})["reason"] == "DEMO_NOT_IMPLEMENTED"


def test_queue_is_bounded_and_network_threads_never_touch_scene():
    adapter, _, _, dispatcher, _, _ = setup()
    handoff = CommandQueue(dispatcher, capacity=1)
    result = []
    thread = threading.Thread(target=lambda: result.append(handoff.submit(command(dispatcher, "reset"), "127.0.0.1")))
    before = adapter.writes
    thread.start(); thread.join()
    assert adapter.writes == before and not result[0].done()
    assert handoff.submit(command(dispatcher, "health"), "127.0.0.1").result()["reason"] == "COMMAND_QUEUE_FULL"
    handoff.drain()
    assert result[0].result()["accepted"] and adapter.writes == before+1


def test_wire_schemas_and_durable_full_command_log(tmp_path):
    import jsonschema
    from isaac.commands.event_log import DurableCommandLog
    _, _, _, dispatcher, _, _ = setup()
    path = tmp_path/"commands.jsonl"
    logger = DurableCommandLog(path, session_id="0"*32, apparatus_version="fixture", protocol_version="unresolved-methodology")
    dispatcher.sink = logger
    root = Path(__file__).resolve().parents[2]/"isaac/commands"
    request_schema = json.loads((root/"command.schema.json").read_text())
    reply_schema = json.loads((root/"reply.schema.json").read_text())
    event_schema = json.loads((root/"command-event.schema.json").read_text())
    raw = command(dispatcher, "reset")
    jsonschema.validate(json.loads(raw), request_schema)
    for received in (raw, raw, '{"args":{"value":1e999}}'):
        reply = dispatcher.submit(received, "127.0.0.1").result()
        jsonschema.validate(reply, reply_schema)
    logger.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 3 and rows[1]["payload"]["reply"]["duplicate"]
    for row in rows:
        jsonschema.validate(row, event_schema)
    assert [row["event_seq"] for row in rows] == [0, 1, 2]
    assert rows[0]["payload"]["raw_command"] == raw


def test_logging_failure_never_acknowledges_or_repeats_reset():
    adapter, _, _, dispatcher, _, _ = setup()
    def broken(_):
        raise OSError("fixture disk failure")
    dispatcher.sink = broken
    raw = command(dispatcher, "reset")
    with pytest.raises(OSError): dispatcher.submit(raw, "127.0.0.1").result()
    written = adapter.writes
    with pytest.raises(OSError): dispatcher.submit(raw, "127.0.0.1").result()
    assert adapter.writes == written and dispatcher.fault == "COMMAND_LOG_FAILED"


def test_disconnect_and_shutdown_do_not_leave_unlogged_jobs():
    _, _, _, dispatcher, events, _ = setup()
    handoff = CommandQueue(dispatcher)
    disconnected = handoff.submit(command(dispatcher, "reset"), "127.0.0.1")
    disconnected.cancel()
    handoff.drain()
    assert len(events) == 1  # Execution/log complete despite no receiver.
    pending = handoff.submit(command(dispatcher, "reset"), "127.0.0.1")
    handoff.close()
    assert pending.result()["reason"] == "SERVICE_STOPPING" and len(events) == 2
    assert handoff.submit(command(dispatcher, "health"), "127.0.0.1").result()["reason"] == "SERVICE_STOPPING"


def test_priority_lock_supersedes_earlier_queued_unlock_and_demo():
    _, _, _, dispatcher, _, motions = setup()
    handoff = CommandQueue(dispatcher)
    unlock_raw = command(dispatcher, "set_mode", {"mode": "teaching"})
    unlock = handoff.submit(unlock_raw, "127.0.0.1")
    demo = handoff.submit(command(dispatcher, "demo", {"action": "SCAN", "target": "container_E"}), "127.0.0.1")
    lock = handoff.submit(command(dispatcher, "set_mode", {"mode": "test"}), "127.0.0.1")
    handoff.drain()
    assert lock.result()["accepted"] and dispatcher.mode == "test" and not motions
    assert unlock.result()["reason"] == demo.result()["reason"] == "PROTECTED_BOUNDARY_SUPERSEDED"
    assert dispatcher.submit(unlock_raw, "127.0.0.1").result()["duplicate"]
    assert dispatcher.mode == "test"
    new_unlock = handoff.submit(command(dispatcher, "set_mode", {"mode": "teaching"}), "127.0.0.1")
    handoff.drain()
    assert new_unlock.result()["accepted"] and dispatcher.mode == "teaching"


def test_exposure_health_requires_current_neutral_and_fresh_healthy_publisher():
    adapter, _, _, dispatcher, _, _ = setup()
    class Publisher:
        closed = False
        status = {"fault": None, "stale": False, "age_ms": 0.}
        def health(self): return self.status
    assert not dispatcher.health()["exposure_ready"]
    dispatcher.publisher = Publisher()
    assert dispatcher.health()["exposure_ready"]
    dispatcher.publisher.status["fault"] = "NEUTRAL_DIVERGED"
    assert not dispatcher.health()["exposure_ready"]
    dispatcher.publisher.status.update(fault=None, stale=True)
    assert not dispatcher.health()["exposure_ready"]
    dispatcher.publisher.status["stale"] = False
    adapter.state["objects"]["engineering_object_0"]["state"]["card_face"] = 1
    dispatcher.reset_manager.verify_current()  # Protected loop owns full sampling.
    assert not dispatcher.health()["exposure_ready"]


def test_health_cache_expires_without_scene_reads_or_new_ticks():
    _, _, reset, dispatcher, _, _ = setup()
    class Publisher:
        closed = False
        def health(self): return {"fault": None, "stale": False, "age_ms": 0.}
    dispatcher.publisher = Publisher()
    handoff = CommandQueue(dispatcher)
    assert handoff.health()["exposure_ready"]
    # A network health read must not invoke any simulator accessor.
    reset.adapter.read_state = lambda: (_ for _ in ()).throw(AssertionError("unexpected scene traversal"))
    assert handoff.health()["exposure_ready"]
    handoff.cached_health["health_sample_host_mono_ms"] -= 251.
    assert not handoff.health()["exposure_ready"]


def test_rejected_commands_preserve_public_v2_render_fields():
    from isaac.publisher.protocol import PublicRegistry, StateEncoder, encode
    adapter, _, reset, dispatcher, _, _ = setup()
    state = adapter.read_state()
    registry = PublicRegistry("engineering-fixture", adapter.scene_sha256, reset.reset_snapshot_sha256,
                              tuple(state["robot"]["joint_names"]),
                              tuple((key, tuple(sorted(value["state"]))) for key, value in sorted(state["objects"].items())),
                              ("fixture_anchor",))
    encoder = StateEncoder(registry, source_kind="synthetic")
    neutral = encoder.build(state["robot"]["joint_positions_rad"], state["objects"], 0., 0)
    for index, (action, target) in enumerate(sorted(LEGAL_PAIRS), 1):
        assert not send(dispatcher, "demo", {"action": action, "target": target})["accepted"]
        current = adapter.read_state()
        public = encoder.build(current["robot"]["joint_positions_rad"], current["objects"], index/30, index)
        assert public["objects"] == neutral["objects"] and public["joint_positions"] == neutral["joint_positions"]
        assert '"command"' not in encode(public) and '"target"' not in encode(public) and '"action"' not in encode(public)
