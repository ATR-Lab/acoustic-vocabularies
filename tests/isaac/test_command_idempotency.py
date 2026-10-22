"""Whole-session idempotency: a bounded hot cache backed by the durable command log.

A request ID is never forgotten within a control session. Entries evicted from
the in-memory reply cache are answered from the fsynced DurableCommandLog, never
re-executed; the remembered-ID total is bounded and fails closed when reached.
"""
import gc
import json
import sys
import tracemalloc
import uuid
from types import SimpleNamespace

import pytest

from test_command_lock import setup, command, send
from isaac.commands import event_log as event_log_module
from isaac.commands.dispatcher import (CommandDispatcher, DEFAULT_CACHE_SIZE, DEFAULT_REQUEST_CAPACITY,
                                       MAX_REQUEST_CAPACITY, idempotency_limits)
from isaac.commands.event_log import CommandLogLocator, DurableCommandLog
from isaac.commands.queue import CommandQueue

DEMO = {"action": "SCAN", "target": "container_E"}


def journaled(tmp_path, *, cache_size, request_capacity=None):
    adapter, _, reset, dispatcher, _, motions = setup(cache_size=cache_size)
    if request_capacity is not None:
        dispatcher.request_capacity = idempotency_limits(cache_size, request_capacity)[1]
    log = DurableCommandLog(tmp_path/"commands.jsonl", session_id=uuid.uuid4().hex,
                            apparatus_version="fixture", protocol_version="unresolved-methodology")
    dispatcher.sink = log
    return adapter, reset, dispatcher, log, motions


def replay(dispatcher, raw):
    future = dispatcher.submit(raw, "127.0.0.1")
    assert future.done()  # A replay never admits new work.
    return future.result()


def test_evicted_ids_replay_from_durable_log_and_never_reexecute(tmp_path):
    adapter, _, dispatcher, log, motions = journaled(tmp_path, cache_size=4)
    send(dispatcher, "set_mode", {"mode": "teaching"})
    demo_raw = command(dispatcher, "demo", DEMO)
    future = dispatcher.submit(demo_raw, "127.0.0.1")
    dispatcher.advance(); dispatcher.advance()
    assert future.result()["reason"] == "DEMO_COMPLETE" and len(motions) == 1
    resets = [command(dispatcher, "reset") for _ in range(12)]
    originals = [dispatcher.submit(raw, "127.0.0.1").result() for raw in resets]
    assert all(reply["reason"] == "RESET_COMPLETE" for reply in originals)
    assert len(dispatcher.cache) == 4 and len(dispatcher.evicted) == 10 and dispatcher.remembered_count() == 14
    first = json.loads(resets[0])["request_id"]
    assert first not in dispatcher.cache and bytes.fromhex(first) in dispatcher.evicted
    writes, events = adapter.writes, log.sequence
    duplicate = replay(dispatcher, resets[0])
    assert duplicate["duplicate"] and duplicate["accepted"] and duplicate["reason"] == "RESET_COMPLETE"
    assert duplicate["reset_ok"] is True and duplicate["request_id"] == first
    assert adapter.writes == writes and log.sequence == events+1  # Logged, not executed.
    # The evicted demo replays its original completion without any new motion.
    again = replay(dispatcher, demo_raw)
    assert again["duplicate"] and again["reason"] == "DEMO_COMPLETE" and len(motions) == 1
    # A changed body under an evicted ID is a conflict, decided without disk reads.
    changed = json.loads(resets[1]); changed["command"] = "hold_neutral"
    assert replay(dispatcher, json.dumps(changed))["reason"] == "REQUEST_ID_CONFLICT"
    assert adapter.writes == writes and dispatcher.fault is None
    # Protected lock still precedes every cached outcome, including evicted ones.
    send(dispatcher, "set_mode", {"mode": "test"})
    assert replay(dispatcher, demo_raw)["reason"] == "PROTECTED_TARGET_COMMAND" and len(motions) == 1


def test_superseded_unlock_stays_refused_after_eviction(tmp_path):
    _, _, dispatcher, _, motions = journaled(tmp_path, cache_size=2)
    handoff = CommandQueue(dispatcher)
    unlock_raw = command(dispatcher, "set_mode", {"mode": "teaching"})
    unlock = handoff.submit(unlock_raw, "127.0.0.1")
    handoff.submit(command(dispatcher, "set_mode", {"mode": "test"}), "127.0.0.1")
    handoff.drain()
    assert unlock.result()["reason"] == "PROTECTED_BOUNDARY_SUPERSEDED"
    for _ in range(5):
        send(dispatcher, "health")
    request_id = json.loads(unlock_raw)["request_id"]
    assert bytes.fromhex(request_id) in dispatcher.evicted
    reply = replay(dispatcher, unlock_raw)
    assert reply["duplicate"] and reply["reason"] == "PROTECTED_BOUNDARY_SUPERSEDED"
    assert dispatcher.mode == "test" and not motions


def test_request_capacity_is_a_hard_refusal_that_forgets_nothing(tmp_path):
    adapter, _, dispatcher, _, _ = journaled(tmp_path, cache_size=2, request_capacity=6)
    raws = [command(dispatcher, "reset") for _ in range(6)]
    for raw in raws:
        assert dispatcher.submit(raw, "127.0.0.1").result()["accepted"]
    assert len(dispatcher.cache) == 2 and len(dispatcher.evicted) == 4
    refused = send(dispatcher, "health")
    assert refused["reason"] == "IDEMPOTENCY_CAPACITY" and not refused["accepted"]
    assert dispatcher.fault == "IDEMPOTENCY_CAPACITY" and dispatcher.paused
    writes = adapter.writes
    for raw in raws:  # Every remembered ID, hot or evicted, is still a duplicate.
        reply = replay(dispatcher, raw)
        assert reply["duplicate"] and reply["reason"] == "RESET_COMPLETE"
    assert adapter.writes == writes and dispatcher.remembered_count() == 6


def test_unreadable_sink_pins_entries_and_keeps_legacy_refusal():
    # A sink that returns no durable-log locator cannot back eviction: refuse.
    adapter, _, _, dispatcher, events, _ = setup(cache_size=3)
    raws = [command(dispatcher, "reset") for _ in range(3)]
    for raw in raws:
        dispatcher.submit(raw, "127.0.0.1").result()
    assert dispatcher.pinned == 3 and not dispatcher.evicted
    assert send(dispatcher, "health")["reason"] == "IDEMPOTENCY_CAPACITY"
    writes = adapter.writes
    assert all(replay(dispatcher, raw)["duplicate"] for raw in raws) and adapter.writes == writes


@pytest.mark.parametrize("damage", ["raise", "disk"])
def test_readback_failure_fails_closed_without_reexecution(tmp_path, damage):
    adapter, _, dispatcher, log, _ = journaled(tmp_path, cache_size=1)
    first, second = command(dispatcher, "reset"), command(dispatcher, "reset")
    dispatcher.submit(first, "127.0.0.1").result()
    dispatcher.submit(second, "127.0.0.1").result()
    if damage == "raise":
        def broken(_):
            raise OSError("fixture read failure")
        log.read = broken
    else:
        log.file.flush()
        path = tmp_path/"commands.jsonl"
        raw = path.read_bytes()
        line_end = raw.index(b"\n")
        assert raw[:line_end].count(b'"duplicate": false') == 1
        # Same length, still valid JSON, but no longer the original terminal reply.
        with path.open("r+b") as handle:
            handle.write(raw[:line_end].replace(b'"duplicate": false', b'"duplicate": true '))
    writes = adapter.writes
    reply = replay(dispatcher, first)
    assert not reply["accepted"] and reply["reason"] == "COMMAND_FAILED" and not reply["duplicate"]
    assert dispatcher.fault == "COMMAND_LOG_READBACK_FAILED" and dispatcher.paused
    assert adapter.writes == writes


def test_3000_command_run_with_default_limits_never_forgets(tmp_path, monkeypatch):
    # Durability is covered elsewhere; skip per-row fsync to keep the run quick.
    monkeypatch.setattr(event_log_module.os, "fsync", lambda _: None)
    adapter, _, dispatcher, log, motions = journaled(tmp_path, cache_size=DEFAULT_CACHE_SIZE)
    assert dispatcher.cache_size == 1024 and dispatcher.request_capacity == DEFAULT_REQUEST_CAPACITY
    # Soak-like cycle: unlock, reset, demos, lock, reset, protected probe, reset, health.
    cycle = [("set_mode", {"mode": "teaching"}), ("reset", {}), ("demo", DEMO), ("demo", DEMO),
             ("set_mode", {"mode": "test"}), ("reset", {}), ("demo", DEMO), ("reset", {}),
             ("health", {}), ("reset", {})]
    sent = []
    for index in range(3000):
        name, args = cycle[index % len(cycle)]
        raw = command(dispatcher, name, args)
        reply = send(dispatcher, name, args, request_id=json.loads(raw)["request_id"])
        sent.append((raw, reply))
        assert reply["reason"] not in ("IDEMPOTENCY_CAPACITY", "COMMAND_FAILED") and dispatcher.fault is None
        assert len(dispatcher.cache) <= 1024
    assert dispatcher.remembered_count() == 3000 and len(dispatcher.cache) == 1024
    assert len(dispatcher.evicted) == 1976 and len(motions) == 600
    assert sum(r["reason"] == "PROTECTED_TARGET_COMMAND" for _, r in sent) == 300
    # Replay every ID in teaching mode: nothing executes, every outcome is the original.
    send(dispatcher, "set_mode", {"mode": "teaching"})
    writes, mode = adapter.writes, dispatcher.mode
    for raw, original in sent:
        reply = replay(dispatcher, raw)
        assert reply["duplicate"] and reply["request_id"] == original["request_id"]
        assert (reply["accepted"], reply["reason"], reply["reset_ok"]) == (
            original["accepted"], original["reason"], original["reset_ok"])
    assert adapter.writes == writes and len(motions) == 600 and dispatcher.mode == mode
    assert dispatcher.fault is None


def test_memory_is_bounded_by_cache_size_and_compact_cold_index(tmp_path, monkeypatch):
    monkeypatch.setattr(event_log_module.os, "fsync", lambda _: None)
    _, _, dispatcher, _, _ = journaled(tmp_path, cache_size=64, request_capacity=4096)
    tracemalloc.start()  # Before warm-up, so replaced hot entries are traced on both sides.
    try:
        for _ in range(200):
            send(dispatcher, "health")
        gc.collect()
        before = tracemalloc.take_snapshot()
        for count in range(2800):
            send(dispatcher, "health")
            assert len(dispatcher.cache) <= 64
        gc.collect()
        grown = sum(stat.size_diff for stat in tracemalloc.take_snapshot().compare_to(before, "filename"))
    finally:
        tracemalloc.stop()
    assert len(dispatcher.cache) == 64 and len(dispatcher.evicted) == 2936
    # Steady-state growth is only the compact cold index, not full replies.
    assert grown / 2800 < 400, grown
    cold = (sys.getsizeof(dispatcher.evicted) + sum(sys.getsizeof(k)+sys.getsizeof(v)
                                                    for k, v in dispatcher.evicted.items())) / len(dispatcher.evicted)
    assert cold < 256
    def deep(value):
        if isinstance(value, dict):
            return sys.getsizeof(value) + sum(deep(k)+deep(v) for k, v in value.items())
        if isinstance(value, (tuple, list)):
            return sys.getsizeof(value) + sum(deep(v) for v in value)
        return sys.getsizeof(value)
    hot = deep(("0"*32,)+next(iter(dispatcher.cache.values()))[:2])
    assert hot > 5 * cold  # A full reply is far larger than a cold record.
    # Worst case remains bounded: capacity times the per-entry cold cost.
    assert MAX_REQUEST_CAPACITY * 256 <= 64 * 1024 * 1024


@pytest.mark.parametrize("cache_size,request_capacity", [(0, 10), (True, 10), (10, 9), (1, MAX_REQUEST_CAPACITY+1),
                                                         (MAX_REQUEST_CAPACITY+1, MAX_REQUEST_CAPACITY+1),
                                                         (10, 10.0), (10, None)])
def test_limits_are_validated(cache_size, request_capacity):
    with pytest.raises(ValueError):
        idempotency_limits(cache_size, request_capacity)


def test_dispatcher_defaults_and_validation():
    _, _, reset, dispatcher, _, _ = setup()
    assert (dispatcher.cache_size, dispatcher.request_capacity) == (1024, 16384)
    for kwargs in ({"cache_size": 0}, {"cache_size": 8, "request_capacity": 4},
                   {"request_capacity": MAX_REQUEST_CAPACITY+1}):
        with pytest.raises(ValueError):
            CommandDispatcher(reset, print, station_id="s", allowed_client="c", **kwargs)
    big = CommandDispatcher(reset, print, station_id="s", allowed_client="c", cache_size=20000)
    assert big.request_capacity == 20000


def test_soak_schedule_fits_default_capacity():
    # 36,000 s at the soak driver's ceiling of 20 commands/minute is 12,000 IDs.
    assert 36000 * 20 // 60 < DEFAULT_REQUEST_CAPACITY <= MAX_REQUEST_CAPACITY


def test_log_read_rejects_foreign_or_future_locators(tmp_path):
    def open_log(name):
        return DurableCommandLog(tmp_path/name, session_id=uuid.uuid4().hex,
                                 apparatus_version="fixture", protocol_version="p")
    a, b = open_log("a.jsonl"), open_log("b.jsonl")
    event = {"reply": {"host_mono_ms": 1., "sim_time": 0.}, "raw_command": "x"}
    locator = a(event)
    assert isinstance(locator, CommandLogLocator) and locator.event_seq == 0 and locator.offset == 0
    assert a.read(locator) == event
    second = a(event)
    assert second.offset == locator.length and a.read(second) == event
    for bad in (CommandLogLocator(b, 0, 0, locator.length), CommandLogLocator(a, 2, 0, locator.length),
                CommandLogLocator(a, 1, 0, locator.length), CommandLogLocator(a, 0, 1, locator.length)):
        with pytest.raises(ValueError):
            a.read(bad)
    a.close(); b.close()


def test_observation_wrapper_returns_the_durable_locator():
    from isaac.view_capture.observations import ObservationJournal
    fake = SimpleNamespace(fail=lambda reason: None, command_written=lambda event: None)
    marker = object()
    assert ObservationJournal.command_sink(fake, lambda event: marker)({}) is marker


def test_station_config_carries_validated_command_limits():
    from test_stations import config
    from isaac.stations.config import validate, command_limits
    value = config()
    assert command_limits(validate(value)) == {"cache_size": 1024, "request_capacity": 16384}
    tuned = validate({**value, "command_cache_size": 256, "command_request_capacity": 8192})
    assert command_limits(tuned) == {"cache_size": 256, "request_capacity": 8192}
    for bad in ({"command_cache_size": 0}, {"command_request_capacity": 512},
                {"command_cache_size": 64, "command_request_capacity": True},
                {"command_request_capacity": MAX_REQUEST_CAPACITY+1}, {"command_cache": 64}):
        with pytest.raises(ValueError):
            validate({**value, **bad})


def test_station_schema_accepts_limits(tmp_path):
    import jsonschema
    from pathlib import Path
    from test_stations import config
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root/"apparatus/stations/station.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate({**config(), "command_cache_size": 256, "command_request_capacity": 8192}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**config(), "command_request_capacity": 0}, schema)


@pytest.mark.parametrize("kwargs", [{"command_cache_size": 0}, {"command_request_capacity": 10},
                                    {"command_cache_size": 2048, "command_request_capacity": 1024}])
def test_joined_service_validates_limits_before_any_resource(tmp_path, kwargs):
    from isaac.e2e.service import run_joined_service
    with pytest.raises(ValueError, match="cache size|request capacity"):
        run_joined_service(None, None, tmp_path/"evidence", seconds=5, station_id="sim-01", host_uid=1000,
                           public_socket=tmp_path/"a.sock", private_socket=tmp_path/"b.sock", **kwargs)
    assert not (tmp_path/"evidence").exists()
