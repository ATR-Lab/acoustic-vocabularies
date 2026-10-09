"""Publisher readback cost work: guarded handle-cache opt-in, stage timing, replay."""
from copy import deepcopy
import csv
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from isaac.publisher import benchmark
from isaac.publisher.benchmark import StageTimer
from isaac.publisher.protocol import validate_frame
from isaac.publisher.replay import counterfactuals, infer_physics, load, replay
from isaac.workcell.run_scene import validate_publisher_cache_profile

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT/"isaac/snapshots/neutral_v1.json"
PERIOD = 1000/30


class Accessors:
    """Stand-in for StateAccessors' cache surface (no pxr on this host)."""
    def __init__(self, state, *, fail_enable=False, invalidate_after=None, enabled=False):
        self.state, self.fail_enable, self.invalidate_after = state, fail_enable, invalidate_after
        self.handle_cache_enabled, self.enable_calls, self.reads = enabled, 0, 0
        self.reads_before_enable = 0

    def enable_handle_cache(self):
        self.enable_calls += 1
        if self.fail_enable:
            raise RuntimeError("Handle cache unavailable; missing USD bindings: synthetic")
        self.reads_before_enable = self.reads
        self.handle_cache_enabled = True

    def read_public_state(self):
        self.reads += 1
        if self.invalidate_after is not None and self.reads > self.invalidate_after:
            # The guard fails closed: a structural change permanently faults reads.
            raise ValueError("Workcell prim, property or ancestry was resynced")
        return deepcopy(self.state["objects"])


def harness(tmp_path, monkeypatch, accessors):
    snapshot = json.loads(SNAPSHOT.read_text())
    delivered = []

    class Transport:
        def __init__(self, socket_path): self.health_provider = None
        def submit(self, payload): delivered.append(payload)
        def metrics(self): return dict(connected_clients=1, queue_overwrites=0, failed=False)
        def close(self): pass

    class Collector:
        def __init__(self, socket_path, registry): self.registry, self.error, self.sequence_gaps = registry, None, 0
        count = property(lambda self: len(delivered))
        first = property(lambda self: json.loads(delivered[0]) if delivered else None)
        last = property(lambda self: json.loads(delivered[-1]) if delivered else None)
        def close(self):
            for raw in delivered: validate_frame(json.loads(raw), self.registry)

    monkeypatch.setattr(benchmark, "WebSocketTransport", Transport)
    monkeypatch.setattr(benchmark, "LocalCollector", Collector)
    state = snapshot["state"]
    sim = SimpleNamespace(time=0., get_physics_dt=lambda: 1/60)
    def step(render=False): sim.time += 1/60
    sim.step = step
    positions = SimpleNamespace(tolist=lambda: list(state["robot"]["joint_positions_rad"]))
    robot = SimpleNamespace(joint_names=list(state["robot"]["joint_names"]), write_data_to_sim=lambda: None,
                            update=lambda dt: None, root_physx_view=SimpleNamespace(get_dof_positions=lambda: [positions]))
    accessors.state = state
    adapter = SimpleNamespace(scene_sha256=snapshot["scene_sha256"], accessors=accessors, sim=sim, robot=robot)
    type(adapter)  # SimpleNamespace attribute for sim_time below
    adapter_cls = type("Adapter", (), {"sim_time": property(lambda self: sim.time)})
    live = adapter_cls(); live.__dict__.update(vars(adapter))
    layout = json.loads((ROOT/"apparatus/workcell_layout.json").read_text())
    def run(name, **options):
        return benchmark.run_publisher_check(live, layout, SNAPSHOT, tmp_path/name,
            expected_snapshot_sha256=hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest(), seconds=1, **options)
    return run, delivered


def test_cache_opt_in_enables_once_before_any_sample_and_is_recorded(tmp_path, monkeypatch):
    accessors = Accessors(None)
    run, delivered = harness(tmp_path, monkeypatch, accessors)
    summary = run("cached", handle_cache=True)
    metadata = json.loads((tmp_path/"cached/metadata.json").read_text())
    assert accessors.enable_calls == 1 and accessors.reads_before_enable == 0
    assert metadata["handle_cache_requested"] and metadata["handle_cache_enabled"]
    assert set(metadata["handle_cache_source_sha256"]) == {"isaac/workcell/state.py", "isaac/workcell/cache_guard.py"}
    assert summary["frames"] == len(delivered) and metadata["fault"] is None


def test_cache_off_by_default_never_touches_the_accessor_cache(tmp_path, monkeypatch):
    accessors = Accessors(None)
    run, _ = harness(tmp_path, monkeypatch, accessors)
    run("plain")
    metadata = json.loads((tmp_path/"plain/metadata.json").read_text())
    assert accessors.enable_calls == 0 and metadata["handle_cache_enabled"] is False
    assert metadata["handle_cache_source_sha256"] is None


def test_cache_initialization_failure_aborts_without_fallback_or_output(tmp_path, monkeypatch):
    accessors = Accessors(None, fail_enable=True)
    run, delivered = harness(tmp_path, monkeypatch, accessors)
    with pytest.raises(RuntimeError, match="missing USD bindings"):
        run("broken", handle_cache=True)
    assert not (tmp_path/"broken").exists() and delivered == [] and accessors.reads == 0


def test_cache_already_enabled_elsewhere_is_refused(tmp_path, monkeypatch):
    run, _ = harness(tmp_path, monkeypatch, Accessors(None, enabled=True))
    with pytest.raises(RuntimeError, match="already enabled"):
        run("twice", handle_cache=True)


def test_guard_fault_mid_run_latches_and_fails_the_check(tmp_path, monkeypatch):
    accessors = Accessors(None, invalidate_after=5)
    run, delivered = harness(tmp_path, monkeypatch, accessors)
    summary = run("invalidated", handle_cache=True)
    metadata = json.loads((tmp_path/"invalidated/metadata.json").read_text())
    assert metadata["fault"] == "PUBLISHER_FAILURE" and metadata["completed"] is False
    assert len(delivered) == 5 and summary["timing_screen"] is False
    assert accessors.reads == 6  # no retry or uncached re-read after the fault


def test_stage_timing_rows_match_published_frames(tmp_path, monkeypatch):
    run, delivered = harness(tmp_path, monkeypatch, Accessors(None))
    run("stages")
    rows = list(csv.DictReader((tmp_path/"stages/stages.csv").open(newline="")))
    frames = [json.loads(raw) for raw in delivered]
    assert [int(r["sim_step"]) for r in rows] == [f["sim_step"] for f in frames]
    assert all(float(r[k]) >= 0 for r in rows for k in StageTimer.STAGES)
    stage_ms = json.loads((tmp_path/"stages/metadata.json").read_text())["stage_ms"]
    assert stage_ms["objects_ms"]["count"] == len(frames)
    assert "stages.csv" in json.loads((tmp_path/"stages/summary.json").read_text())["hashes"]
    run("nostages", stage_timing=False)
    assert not (tmp_path/"nostages/stages.csv").exists()
    with pytest.raises(ValueError):
        run("bad", handle_cache="yes")


def test_stage_timer_ignores_unpaired_events_and_uses_no_tracked_storage():
    import itertools
    ticks = itertools.count(0, 1_000_000)
    timer = StageTimer(clock_ns=lambda: next(ticks))
    timer.record("encode_end", a=1)  # no begin: ignored
    timer.note(joints_ms=1.5, objects_ms=9.)
    timer.record("sample_begin", a=2)
    timer.record("encode_begin", a=2); timer.record("encode_end", a=2)
    timer.record("encode_begin", a=3); timer.record("encode_end", a=3)  # no notes: NaN sample stages
    assert list(timer.steps) == [2, 3]
    assert timer.values["encode_ms"].tolist() == [1.0, 1.0]
    assert math.isnan(timer.values["joints_ms"][1])
    assert timer.summary()["joints_ms"]["count"] == 1
    import gc
    gc.collect()
    before = len(gc.get_objects())
    for step in range(5000):
        timer.note(joints_ms=1., objects_ms=2.)
        timer.record("encode_begin", a=step); timer.record("encode_end", a=step)
    gc.collect()
    assert len(gc.get_objects()) - before < 50  # rows are packed doubles, not tracked objects


# ---- scene-runner profile ---------------------------------------------------

def profile(**changes):
    values = dict(publisher_handle_cache=True, reset_check=True, publisher_seconds=3600., e2e_handle_cache=False,
                  published_command_check=False, disconnect_check=False, demo_check=False, demo_preflight=False,
                  grip_check=False, protected_stream_seconds=0., e2e_seconds=0., same_iteration_check=False,
                  command_check=True)
    return SimpleNamespace(**(values | changes))


def test_publisher_cache_profile_accepts_bounded_rate_check():
    validate_publisher_cache_profile(profile())
    validate_publisher_cache_profile(SimpleNamespace(publisher_handle_cache=False))


@pytest.mark.parametrize("changes", [
    {"reset_check": False}, {"e2e_handle_cache": True},
    *({"publisher_seconds": value} for value in (0., -1., 3601., float("nan"), float("inf"))),
    *({key: True} for key in ("published_command_check", "disconnect_check", "demo_check",
                              "demo_preflight", "grip_check", "same_iteration_check")),
    {"protected_stream_seconds": 30.}, {"e2e_seconds": 40.},
])
def test_publisher_cache_profile_refuses_ambiguous_runs(changes):
    with pytest.raises(ValueError, match="Publisher handle cache"):
        validate_publisher_cache_profile(profile(**changes))


# ---- replay -------------------------------------------------------------------

def write_log(path, physics, serialize, wake=.1):
    """Simulate the presample loop exactly, then write its publish log."""
    lags, missed, rows = [wake], [0], []
    for k in range(1, len(serialize)):
        late = lags[-1] + .05 + physics[k] + serialize[k] - PERIOD
        skipped = 0
        while late >= PERIOD:
            late -= PERIOD; skipped += 1
        lags.append(max(wake, late)); missed.append(skipped)
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["seq", "serialize_ms", "handoff_ms", "missed_deadlines", "deadline_lag_ms"])
        for k, s in enumerate(serialize):
            writer.writerow([k, s, .05, missed[k], lags[k]])
    return sum(missed)


def test_replay_identity_reproduces_misses_and_cheaper_reads_remove_them(tmp_path):
    serialize = [13.]*400
    for k in range(100, 160):
        serialize[k] = 17.  # a sustained readback slowdown, as in the native hour
    physics = [18.]*400
    missed = write_log(tmp_path/"log.csv", physics, serialize)
    rows = load(tmp_path/"log.csv")
    known, _, median = infer_physics(rows)
    assert missed >= 1 and abs(median - 18.) < 1e-6 and len(known) >= 50
    result = counterfactuals(rows, saving_ms=3., object_share=.75, object_factor=.67)
    assert result["identity"]["missed_deadlines"] == missed
    assert result["additive"]["missed_deadlines"] == 0
    assert result["additive"]["overrun_ticks"] < result["identity"]["overrun_ticks"]
    assert replay(rows, [s + 3 for s in serialize])["missed_deadlines"] > missed


def test_replay_requires_presampled_lag_column(tmp_path):
    path = tmp_path/"legacy.csv"
    path.write_text("seq,serialize_ms,handoff_ms,missed_deadlines\n0,1,0,0\n")
    with pytest.raises(ValueError):
        load(path)
