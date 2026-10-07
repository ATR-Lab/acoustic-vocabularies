"""Deadline pacing, pre-sampled publication and GC policy (host-only, no Isaac)."""
from copy import deepcopy
import csv
import gc
import json
import math
from pathlib import Path
import random

import pytest

from isaac.publisher.analyze import analyze
from isaac.publisher.benchmark import registry_from_snapshot
from isaac.publisher.pacing import (GcPolicy, claim_deadline, deadline_ns, run_paced_loop,
                                    sleep_until)
from isaac.publisher.protocol import StateEncoder, encode, validate_frame
from isaac.publisher.runtime import StatePublisher

ROOT = Path(__file__).resolve().parents[2]
PERIOD = 1_000_000_000 // 30


def fixture():
    layout = json.loads((ROOT/"apparatus/workcell_layout.json").read_text())
    snap = json.loads((ROOT/"isaac/snapshots/neutral_v1.json").read_text())
    registry = registry_from_snapshot(layout, snap, "b"*64, "synthetic-pacing", ROOT/"docs/spikes/isaac/joint_inventory.csv")
    return registry, snap["state"]


class Sink:
    def __init__(self):
        self.payloads = []
    def submit(self, payload):
        self.payloads.append(payload)
    def metrics(self):
        return {"connected_clients": 1, "queue_overwrites": 0, "failed": False}
    def close(self):
        pass


class FakeTime:
    """Clock and sleep under test control; sleep wakes ``wake`` ns late."""
    def __init__(self, start=10_000_000_000, wake=0):
        self.now, self.wake, self.sleeps = start, wake, []
    def clock(self):
        return self.now
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += round(seconds*1e9) + self.wake
    def burn(self, ns):
        self.now += ns


# ---- deadline math -------------------------------------------------------

def test_absolute_integer_deadlines_never_drift():
    epoch = 123_456_789
    ticks = 3600*30
    assert deadline_ns(epoch, ticks, 30) == epoch + 3600*1_000_000_000
    intervals = {deadline_ns(epoch, i+1, 30)-deadline_ns(epoch, i, 30) for i in range(300)}
    assert intervals == {33_333_333, 33_333_334}
    assert deadline_ns(epoch, 60, 60) - deadline_ns(epoch, 0, 60) == 1_000_000_000


def test_claim_waits_for_deadline_then_claims_exactly_one_tick():
    assert claim_deadline(0, 1, 30, deadline_ns(0, 1, 30)-1) is None
    assert claim_deadline(0, 1, 30, deadline_ns(0, 1, 30)) == (1, 0)
    assert claim_deadline(0, 1, 30, deadline_ns(0, 2, 30)-1) == (1, 0)


def test_late_claim_skips_missed_ticks_without_burst():
    now = deadline_ns(0, 4, 30) + 5_000_000  # 2.15 periods after tick 2's deadline
    assert claim_deadline(0, 2, 30, now) == (4, 2)
    # The next claim at the same instant is not due: no catch-up burst.
    assert claim_deadline(0, 5, 30, now) is None


def test_claim_includes_a_deadline_landing_exactly_now():
    # floor((d1-epoch)*rate/1e9) is 0 although tick 1 is due at exactly d1.
    assert claim_deadline(0, 0, 30, deadline_ns(0, 1, 30)) == (1, 1)


# ---- sleep ----------------------------------------------------------------

def test_sleep_until_absolute_deadline_and_lateness():
    fake = FakeTime(start=0, wake=70_000)
    assert sleep_until(5_000_000, clock_ns=fake.clock, sleep=fake.sleep) == 70_000
    assert fake.sleeps == [0.005]
    assert sleep_until(1, clock_ns=fake.clock, sleep=fake.sleep) == 0  # already past
    assert len(fake.sleeps) == 1


def test_bounded_final_spin_sleeps_short_of_deadline():
    calls = []
    fake = FakeTime(start=0)
    def clock():
        calls.append(fake.now)
        fake.now += 10_000  # each read advances: the spin terminates
        return fake.now
    late = sleep_until(2_000_000, clock_ns=clock, sleep=fake.sleep, spin_ns=500_000)
    assert fake.sleeps and fake.sleeps[0] == pytest.approx((2_000_000-10_000-500_000)/1e9)
    assert 0 <= late < 10_000 + 1
    with pytest.raises(ValueError):
        sleep_until(1, spin_ns=-1)


# ---- prepared publication ------------------------------------------------

def neutral_sample(state, offset=0.):
    def sample():
        objects = deepcopy(state["objects"])
        return [x + offset for x in state["robot"]["joint_positions_rad"]], objects, {"complete": True}
    return sample


def test_prepared_publication_bytes_equal_legacy_at_same_stamp(tmp_path):
    registry, state = fixture()
    payloads = []
    for mode in ("legacy", "prepared"):
        fake = FakeTime(start=0)
        sink = Sink()
        publisher = StatePublisher(registry, neutral_sample(state), sink, tmp_path/f"{mode}.csv",
                                   source_kind="synthetic", clock_ns=fake.clock)
        publisher.encoder.session_id = "d"*32
        try:
            for step in range(1, 6):
                fake.now = deadline_ns(0, step-1, 30) + 1234
                if mode == "legacy":
                    assert publisher.after_step(step/60, step) is not None
                else:
                    assert publisher.prepare(step/60, step) is not None
                    assert publisher.publish_prepared(step/60, step) is not None
        finally:
            publisher.close()
        payloads.append(sink.payloads)
    assert payloads[0] == payloads[1]
    assert [json.loads(p)["seq"] for p in payloads[1]] == list(range(5))


def test_commit_is_byte_identical_to_full_encode_for_random_states():
    registry, neutral = fixture()
    rng = random.Random(170)
    encoder = StateEncoder(registry, "synthetic")
    for index in range(100):
        objects = deepcopy(neutral["objects"])
        for record in objects.values():
            record["position_m"] = [rng.uniform(-2, 2) for _ in range(3)]
            yaw = rng.uniform(-math.pi, math.pi)
            record["rotation_xyzw"] = [0., 0., math.sin(yaw/2), math.cos(yaw/2)]
        prepared = encoder.prepare([rng.uniform(-1, 1) for _ in range(43)], objects, index/30+.01, index+1)
        stamp = rng.choice((0, 7, rng.randrange(10**18), 10**19 + rng.randrange(10**18)))
        frame, payload = encoder.commit(prepared, stamp)
        assert payload == encode(frame)
        assert frame["host_monotonic_ns"] == str(stamp) and frame["seq"] == index
        assert validate_frame(json.loads(payload), registry)
    assert encoder.sequence == 100


def test_commit_rejects_reuse_stale_sequence_and_invalid_stamps():
    registry, state = fixture()
    encoder = StateEncoder(registry, "synthetic")
    first = encoder.prepare(state["robot"]["joint_positions_rad"], state["objects"], .1, 1)
    second = encoder.prepare(state["robot"]["joint_positions_rad"], state["objects"], .2, 2)
    for bad in (-1, 1.5, True, "12", 10**20):
        with pytest.raises(ValueError):
            encoder.commit(first, bad)
    assert encoder.sequence == 0
    encoder.commit(first, 5)
    with pytest.raises(ValueError):
        encoder.commit(first, 6)  # already committed
    with pytest.raises(ValueError):
        encoder.commit(second, 6)  # prepared against a sequence that was since used
    assert encoder.sequence == 1


def test_prepare_does_not_consume_sequence_and_waits_for_deadline(tmp_path):
    registry, state = fixture()
    fake = FakeTime(start=0)
    sink = Sink()
    publisher = StatePublisher(registry, neutral_sample(state), sink, tmp_path/"wait.csv",
                               source_kind="synthetic", clock_ns=fake.clock)
    try:
        publisher.epoch_ns = 0
        assert publisher.after_step(1/60, 1) is not None
        assert publisher.prepare(2/60, 2) is not None
        assert publisher.prepare(2/60, 2) is not None  # re-prepare replaces, no sequence use
        fake.now = PERIOD - 1
        assert publisher.publish_prepared(2/60, 2) is None and publisher.fault is None
        fake.now = PERIOD + 50
        frame = publisher.publish_prepared(2/60, 2)
        assert frame["seq"] == 1 and frame["host_monotonic_ns"] == str(PERIOD + 50)
        assert publisher.prepared is None
    finally:
        publisher.close()
    rows = list(csv.DictReader((tmp_path/"wait.csv").open(newline="")))
    assert [float(r["deadline_lag_ms"]) for r in rows] == [0.0, 50/1e6]


@pytest.mark.parametrize("call", [
    lambda p: p.publish_prepared(3/60, 3),  # simulator advanced after sampling
    lambda p: p.publish_prepared(2/60 + 1e-9, 2),
])
def test_stale_prepared_state_latches_without_publishing(tmp_path, call):
    registry, state = fixture()
    fake = FakeTime(start=0)
    sink = Sink()
    publisher = StatePublisher(registry, neutral_sample(state), sink, tmp_path/"stale.csv",
                               source_kind="synthetic", clock_ns=fake.clock)
    try:
        publisher.epoch_ns = 0
        assert publisher.prepare(2/60, 2) is not None
        assert call(publisher) is None
        assert publisher.fault == "PUBLISHER_FAILURE" and sink.payloads == []
        assert publisher.prepare(4/60, 4) is None  # latched
    finally:
        publisher.close()


def test_publish_without_prepare_latches(tmp_path):
    registry, state = fixture()
    publisher = StatePublisher(registry, neutral_sample(state), Sink(), tmp_path/"none.csv", source_kind="synthetic")
    try:
        assert publisher.publish_prepared(1/60, 1) is None
        assert publisher.fault == "PUBLISHER_FAILURE"
    finally:
        publisher.close()


def test_failed_neutral_check_at_prepare_never_publishes(tmp_path):
    registry, state = fixture()
    sink = Sink()
    publisher = StatePublisher(registry, neutral_sample(state), sink, tmp_path/"neutral.csv",
                               neutral_check=lambda _: {"reset_ok": False}, source_kind="synthetic")
    try:
        publisher.require_neutral(True)
        assert publisher.prepare(1/60, 1) is None
        assert publisher.fault == "NEUTRAL_DIVERGED"
        assert publisher.publish_prepared(1/60, 1) is None and sink.payloads == []
    finally:
        publisher.close()


# ---- paced loop ------------------------------------------------------------

def paced(tmp_path, pacing, physics_ns, read_ns, ticks=6, wake=0):
    registry, state = fixture()
    fake = FakeTime(wake=wake)
    sim = {"step": 0}
    costs = iter(physics_ns)
    reads = iter(read_ns)
    base = neutral_sample(state)
    def sample():
        fake.burn(next(reads))
        return base()
    def advance():
        fake.burn(next(costs))
        sim["step"] += 2
        return sim["step"]/60, sim["step"]
    sink = Sink()
    publisher = StatePublisher(registry, sample, sink, tmp_path/f"{pacing}.csv", source_kind="synthetic",
                               clock_ns=fake.clock)
    stats = {}
    try:
        start, end, stats = run_paced_loop(publisher, advance, seconds=(ticks-.5)/30, pacing=pacing,
                                           clock_ns=fake.clock, sleep=fake.sleep, stats=stats)
    finally:
        publisher.close()
    stamps = [int(json.loads(p)["host_monotonic_ns"]) for p in sink.payloads]
    return start, stamps, publisher, stats


def test_presample_stamps_at_deadlines_and_removes_readback_variation(tmp_path):
    reads = [11_000_000, 15_000_000, 11_000_000, 17_000_000, 11_000_000, 12_000_000, 11_000_000]
    physics = [15_000_000]*7
    start, legacy, _, _ = paced(tmp_path, "at_deadline", physics, reads)
    start2, presampled, publisher, stats = paced(tmp_path, "presample", physics, reads)
    # Every frame, including tick 0, is stamped exactly at its absolute deadline.
    assert presampled == [deadline_ns(start2, i, 30) for i in range(len(presampled))]
    assert len(presampled) == stats["ticks"] == 7 and publisher.missed == 0 and stats["overrun_ticks"] == 0
    # Legacy stamps follow the readback: deadline + read duration.
    assert [b - deadline_ns(start, i, 30) for i, b in enumerate(legacy)] == reads
    legacy_errors = [abs(b-a-PERIOD) for a, b in zip(legacy, legacy[1:])]
    assert max(legacy_errors) >= 6_000_000


def test_overrun_publishes_late_once_and_skips_without_burst(tmp_path):
    # Tick 2's work ends 111 ms after tick 1's deadline: ticks 2 and 3 are
    # missed and never backfilled; one late frame is published for tick 4.
    physics = [15_000_000, 15_000_000, 100_000_000] + [15_000_000]*20
    reads = [11_000_000]*23
    start, stamps, publisher, stats = paced(tmp_path, "presample", physics, reads, ticks=9)
    assert publisher.missed == 2 and stats["overrun_ticks"] == 2
    rows = list(csv.DictReader((tmp_path/"presample.csv").open(newline="")))
    assert [int(r["missed_deadlines"]) for r in rows] == [0, 0, 2] + [0]*(len(rows)-3)
    assert [int(r["seq"]) for r in rows] == list(range(len(rows)))
    assert all(float(r["deadline_lag_ms"]) >= 0 for r in rows)
    # No burst: frames are never closer than half a period.
    assert min(b - a for a, b in zip(stamps, stamps[1:])) > PERIOD // 2
    # Every tick up to the last one is either published once or counted missed.
    last_tick = round((stamps[-1] - start - float(rows[-1]["deadline_lag_ms"])*1e6) * 30 / 1e9)
    assert len(stamps) == stats["ticks"] and publisher.published + publisher.missed == last_tick + 1


def test_loop_rejects_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        run_paced_loop(None, None, seconds=1, pacing="burst")
    with pytest.raises(ValueError):
        run_paced_loop(None, None, seconds=math.inf)


# ---- GC policy -------------------------------------------------------------

class FakeGc:
    def __init__(self):
        self.threshold, self.calls, self.frozen = (700, 10, 10), [], 0
    def get_threshold(self):
        return self.threshold
    def set_threshold(self, *values):
        self.calls.append(("set_threshold", values)); self.threshold = values
    def collect(self, generation=2):
        self.calls.append(("collect", generation)); return 0
    def freeze(self):
        self.calls.append(("freeze",)); self.frozen = 1000
    def unfreeze(self):
        self.calls.append(("unfreeze",)); self.frozen = 0
    def get_freeze_count(self):
        return self.frozen


def test_default_gc_policy_changes_nothing():
    fake = FakeGc()
    policy = GcPolicy("default", gc_module=fake, monitor=False)
    policy.start(); assert policy.safe_point() is None; policy.stop()
    assert fake.calls == [("set_threshold", (700, 10, 10))] and fake.threshold == (700, 10, 10)


def test_freeze_policy_freezes_after_warmup_and_restores():
    fake = FakeGc()
    policy = GcPolicy("freeze", gc_module=fake, monitor=False)
    policy.start()
    assert fake.calls[:2] == [("collect", 2), ("freeze",)] and fake.threshold == (700, 10, 10)
    assert policy.safe_point() is None
    policy.stop()
    assert fake.calls[-1] == ("unfreeze",) and fake.frozen == 0
    assert policy.summary()["frozen_objects"] == 1000


def test_manual_policy_disables_automatic_gen2_and_collects_at_safe_points():
    fake = FakeGc()
    policy = GcPolicy("freeze_manual", manual_interval=3, gc_module=fake, monitor=False)
    policy.start()
    assert fake.threshold[:2] == (700, 10) and fake.threshold[2] >= 2**30
    results = [policy.safe_point() for _ in range(7)]
    assert [r is not None for r in results] == [False, False, True, False, False, True, False]
    assert fake.calls.count(("collect", 2)) == 3  # warm-up plus two safe points
    policy.stop()
    assert fake.threshold == (700, 10, 10) and fake.frozen == 0
    assert policy.summary()["manual_collections"] == 2


def test_gc_policy_validation_and_real_interpreter_restore():
    for bad in (dict(mode="off"), dict(mode="freeze_manual", manual_interval=0)):
        with pytest.raises(ValueError):
            GcPolicy(**bad)
    before = (gc.get_threshold(), len(gc.callbacks))
    frozen_before = gc.get_freeze_count()
    policy = GcPolicy("freeze_manual", manual_interval=1)
    policy.start()
    try:
        assert gc.get_freeze_count() > frozen_before and gc.get_threshold()[2] >= 2**30
        garbage = []; garbage.append(garbage); del garbage
        assert policy.safe_point() is not None
        with pytest.raises(RuntimeError):
            policy.start()
    finally:
        policy.stop()
    assert (gc.get_threshold(), len(gc.callbacks)) == before
    # Only a freeze this policy owns is released.
    assert gc.get_freeze_count() == 0 if frozen_before == 0 else gc.get_freeze_count() >= frozen_before
    summary = policy.summary()
    assert summary["preexisting_frozen"] == frozen_before
    assert summary["monitor"]["by_generation"]["2"]["events"] >= 1


def test_freeze_policy_keeps_a_freeze_it_does_not_own():
    fake = FakeGc(); fake.frozen = 5
    policy = GcPolicy("freeze", gc_module=fake, monitor=False)
    policy.start(); policy.stop()
    assert ("unfreeze",) not in fake.calls and policy.summary()["preexisting_frozen"] == 5


def test_loop_restores_gc_policy_after_failure(tmp_path):
    fake = FakeGc()
    policy = GcPolicy("freeze", gc_module=fake, monitor=False)
    class Broken:
        epoch_ns = deadline_index = 0
        fault = "PUBLISHER_FAILURE"
        def next_deadline_ns(self): return 0
        def prepare(self, *_): return None
    with pytest.raises(RuntimeError):
        run_paced_loop(Broken(), lambda: (1/60, 1), seconds=1, gc_policy=policy)
    assert fake.calls[-1] == ("unfreeze",) and not policy.active


# ---- live-harness wiring with a fake simulator ----------------------------

@pytest.mark.parametrize("pacing,policy", [("presample", "freeze"), ("at_deadline", "default")])
def test_run_publisher_check_wiring_with_fake_adapter(tmp_path, monkeypatch, pacing, policy):
    import hashlib
    from types import SimpleNamespace
    from isaac.publisher import benchmark
    snapshot_path = ROOT/"isaac/snapshots/neutral_v1.json"
    snapshot_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    snapshot = json.loads(snapshot_path.read_text())
    layout = json.loads((ROOT/"apparatus/workcell_layout.json").read_text())
    delivered = []

    class Transport:
        def __init__(self, socket_path): self.health_provider = None
        def submit(self, payload): delivered.append(payload)
        def metrics(self): return dict(connected_clients=1, queue_overwrites=0, failed=False)
        def close(self): pass

    class Collector:
        def __init__(self, socket_path, registry):
            self.registry, self.error, self.sequence_gaps = registry, None, 0
        @property
        def count(self): return len(delivered)
        @property
        def first(self): return json.loads(delivered[0]) if delivered else None
        @property
        def last(self): return json.loads(delivered[-1]) if delivered else None
        def close(self):
            for raw in delivered: validate_frame(json.loads(raw), self.registry)

    monkeypatch.setattr(benchmark, "WebSocketTransport", Transport)
    monkeypatch.setattr(benchmark, "LocalCollector", Collector)
    state = snapshot["state"]
    sim = SimpleNamespace(time=0., get_physics_dt=lambda: 1/60)
    def step(render=False):
        sim.time += 1/60
    sim.step = step
    positions = SimpleNamespace(tolist=lambda: list(state["robot"]["joint_positions_rad"]))
    robot = SimpleNamespace(joint_names=list(state["robot"]["joint_names"]), write_data_to_sim=lambda: None,
                            update=lambda dt: None,
                            root_physx_view=SimpleNamespace(get_dof_positions=lambda: [positions]))
    class Adapter:
        scene_sha256 = snapshot["scene_sha256"]
        accessors = SimpleNamespace(read_public_state=lambda: deepcopy(state["objects"]))
        @property
        def sim_time(self): return sim.time
    adapter = Adapter(); adapter.sim, adapter.robot = sim, robot
    summary = benchmark.run_publisher_check(adapter, layout, snapshot_path, tmp_path/"run",
        expected_snapshot_sha256=snapshot_hash, seconds=1, pacing=pacing, gc_policy=policy)
    metadata = json.loads((tmp_path/"run/metadata.json").read_text())
    assert metadata["fault"] is None and metadata["completed"] is True
    assert metadata["pacing_mode"] == pacing and metadata["gc"]["policy"] == policy
    assert summary["frames"] == len(delivered) >= 25 and summary["contiguous"]
    assert summary["local_receive_complete"] and summary["rate_screen"] is False
    assert "deadline_lag_p99_ms" in summary
    with pytest.raises(ValueError):
        benchmark.run_publisher_check(adapter, layout, snapshot_path, tmp_path/"bad",
            expected_snapshot_sha256=snapshot_hash, pacing="burst")


# ---- analyzer attribution and synthetic harness ---------------------------

def test_analyzer_reports_lag_attribution_without_changing_screen(tmp_path):
    path = tmp_path/"lag.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["seq", "host_monotonic_ns", "sim_step", "queue_overwrites", "missed_deadlines", "deadline_lag_ms"])
        for i in range(60):
            writer.writerow([i, i*1_000_000_000//30 + 100, i*2, 0, 0, .0001])
    metadata = dict(rate_hz=30, start_host_ns="0", end_host_ns="2000000000", completed=True,
                    source_kind="live", schema_validated_frames=60, fault=None)
    result = analyze(path, metadata, required_seconds=2)
    assert result["timing_screen"] and not result["rate_screen"]
    assert result["deadline_lag_p99_ms"] == pytest.approx(.0001)


def test_virtual_host_harness_is_labelled_synthetic(tmp_path):
    from isaac.publisher import host_timing
    output = tmp_path/"host"
    assert host_timing.main(["--output", str(output), "--virtual-seconds", "3", "--seconds", "0",
                             "--ballast-objects", "20000"]) == 0
    report = json.loads((output/"host-timing-summary.json").read_text())
    assert report["synthetic_host_only"] is True and report["qualification"] is False
    probe = report["gc_pause_probe"]["results"]
    assert set(probe) == {"default", "freeze", "freeze_manual"}
    assert probe["freeze_manual"]["automatic_gen2_events"] == 0
    assert probe["freeze_manual"]["manual_collection_ms"] is not None
    runs = {run["config"]: run for run in report["virtual_runs"]}
    assert set(runs) == {"legacy", "presample"}
    for run in runs.values():
        assert run["analyzer"]["rate_screen"] is False and run["fault"] is None
    assert runs["presample"]["analyzer"]["deadline_lag_median_ms"] < runs["legacy"]["analyzer"]["deadline_lag_median_ms"]
    assert report["allocation_probe"]["presample"]["net_tracked_containers_per_frame"] < 1
