"""Panel session contract (#20 host <-> #21 panel server): types, masking, protocol shape."""

import dataclasses
import threading
from pathlib import Path

from av_generation.config import BatchConfig, RaterSeat
from av_generation.masking import masking_findings
from av_generation.panel_session import (
    AssetRef,
    PanelEvent,
    PanelRefused,
    PanelSessionHost,
    PanelSlot,
    PanelSnapshot,
    PlayReport,
    RatingAck,
    RatingSubmission,
)

ROOT = Path(__file__).resolve().parents[2]
H = "a" * 64


def _slot(first_atom: bool = False, placeholder: bool = False) -> PanelSlot:
    asset = AssetRef(H, "b" * 64, 9_600, 19_244)
    return PanelSlot(
        rating_slot_id="DEMO-A-P01.K-a1.r2p5",
        position=5,
        start_ms=10_000,
        placeholder=placeholder,
        first_atom=first_atom,
        meaning="DEMO placeholder meaning for ADD_ONE",
        candidate=None if placeholder else asset,
        reference=None if first_atom else asset,
        reference_meaning=None if first_atom else "DEMO placeholder meaning for B",
        unlock_offset_ms=2_000,
    )


class FakeHost:
    """The smallest host: one scripted event list, every callback recorded."""

    run_id = "DEMO-run-01"

    def __init__(self, seats):
        self._seats = tuple(seats)
        self._events = [PanelEvent(1, "slot", 10_000, slot=_slot())]
        self.calls = []
        self._cond = threading.Condition()

    def seats(self):
        return self._seats

    def wait_events(self, after_seq, timeout_s):
        with self._cond:
            return tuple(e for e in self._events if e.seq > after_seq)

    def snapshot(self):
        return PanelSnapshot(1, "slot", _slot(), ())

    def asset_bytes(self, asset_id):
        raise KeyError(asset_id)

    def station_joined(self, rater_id, station, kind):
        if RaterSeat(rater_id, station, kind) not in self._seats:
            raise PanelRefused("E_UNKNOWN_RATER", rater_id)
        self.calls.append(("joined", rater_id))

    def station_left(self, rater_id, station):
        self.calls.append(("left", rater_id))

    def asset_ready(self, rater_id, station, asset_id, ok):
        self.calls.append(("ready", ok))

    def clock_synced(self, rater_id, station, offset_ms, rtt_ms):
        self.calls.append(("sync", offset_ms))

    def report_play(self, report):
        self.calls.append(("play", report.role))

    def submit_rating(self, submission):
        return RatingAck(True)

    def report_withdrawal(self, rater_id, station, reason):
        self.calls.append(("withdraw", reason))


def test_fake_host_satisfies_the_protocol():
    config = BatchConfig.read(ROOT / "generation/examples/demo-batch-config.json")
    host = FakeHost(config.panel.raters)
    assert isinstance(host, PanelSessionHost)
    assert host.wait_events(0, 0.1)[0].slot.position == 5
    assert host.wait_events(1, 0.0) == ()
    host.station_joined("R01", "S1", "bot")
    try:
        host.station_joined("R01", "S1", "human")
    except PanelRefused as err:
        assert err.code == "E_UNKNOWN_RATER"
    report = PlayReport("R01", "S1", "DEMO-A-P01.K-a1.r2p5", "candidate", H, 10_000, 10_004, 10_010)
    host.report_play(report)
    ack = host.submit_rating(
        RatingSubmission("R01", "S1", "DEMO-A-P01.K-a1.r2p5", 5, 3, "acceptable", 900, 15_000)
    )
    assert ack.accepted and ack.code is None


def test_slot_rules_and_masking_by_construction():
    assert _slot().ask_distinguishability
    assert not _slot(first_atom=True).ask_distinguishability
    assert not _slot(placeholder=True).ask_distinguishability
    assert (_slot().candidate_offset_ms, _slot().reference_offset_ms) == (0, 2_000)
    for cls in (PanelSlot, PanelEvent, PanelSnapshot, PlayReport, RatingSubmission, AssetRef):
        names = {f.name for f in dataclasses.fields(cls)}
        assert not names & {"book_id", "slot_id", "method", "seed", "seed_key", "alias"}
    assert masking_findings(repr(_slot())) == ()
