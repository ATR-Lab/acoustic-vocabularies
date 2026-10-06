"""The orchestrator as panel session host (#20 -> #21): rating rules, plays, stations.

A probe on the synthetic panel sends the station calls a real panel server would relay
(bad ratings, plays, reconnects) at the start of each slot; the run itself is the real
orchestrator over two atoms.
"""

import collections
import hashlib

import pytest

from av_generation import _batch_sim as sim
from av_generation.clock import ManualClock
from av_generation.outcomes import SlotOutcome
from av_generation.panel_session import PanelRefused, PlayReport, RatingSubmission
from av_generation.records import PlayEvent, RatingRecord, SlotRecord, TimingEvent, read_records

RUN = "DEMO-host-01"


def _sub(rater, station, slot, *, a=4, d=4, comfort="acceptable", at=None):
    received = slot.start_ms + slot.unlock_offset_ms + 10 if at is None else at
    return RatingSubmission(rater, station, slot.rating_slot_id, a, d, comfort, 10, received)


@pytest.fixture(scope="module")
def probed(tmp_path_factory):
    config = sim.demo_batch_config()
    first_book = config.panel.order[0]
    method = config.method_of(first_book)
    a1, a2 = config.atom_order[:2]
    seen = collections.defaultdict(list)
    previous = {}

    def propose(request, slot, rng):
        if request.atom_id == a1 and request.round == 1 and slot == 3:
            return sim.SimProposal("invalid_json")
        return sim.SimProposal("recipe", sim.uniform_recipe(rng))

    def probe(host, slot):
        def ack(sub):
            return host.submit_rating(sub).code

        if slot.placeholder:
            seen["placeholder"].append(ack(_sub("R01", "S1", slot)))
        elif slot.first_atom and "first" not in seen:
            seen["first"] = [
                ack(_sub("R01", "S1", slot, d=5)),
                ack(_sub("R01", "S1", slot, d=None, at=slot.start_ms + 100)),
                ack(_sub("R09", "S1", slot, d=None)),
                ack(_sub("R01", "S1", slot, a=9, d=None)),
                ack(
                    RatingSubmission(
                        "R01",
                        "S1",
                        f"{config.batch_id}.{config.atom_order[9]}.r4p9",
                        4,
                        None,
                        "acceptable",
                        1,
                        slot.start_ms + 5_000,
                    )
                ),
                ack(_sub("R01", "S1", slot, d=None, at=slot.start_ms + 20_000)),
            ]
            for call in (
                lambda: host.station_joined("R09", "S1", "bot"),
                lambda: host.station_joined("R01", "S1", "human"),
                lambda: host.report_withdrawal("R09", "S9", "x"),
                lambda: host.report_play(
                    PlayReport("R01", "S1", slot.rating_slot_id, "candidate", "0" * 64, 0, 0, 0)
                ),
                lambda: host.report_play(
                    PlayReport("R01", "S1", "DEMO-A-P01.K-a1.r4p9", "candidate", "0" * 64, 0, 0, 0)
                ),
            ):
                try:
                    call()
                    seen["refused"].append(None)
                except PanelRefused as err:
                    seen["refused"].append(err.code)
            snap = host.snapshot()
            seen["snapshot"] = [snap.state, snap.slot == slot, len(snap.preload) > 0]
            data = host.asset_bytes(slot.candidate.asset_id)
            seen["asset"] = [hashlib.sha256(data).hexdigest() == slot.candidate.asset_id]
            try:
                host.asset_bytes("0" * 64)
            except KeyError:
                seen["asset"].append("KeyError")
            host.asset_ready("R01", "S1", slot.candidate.asset_id, False)
            # R03 drops and rejoins during this slot: its record is marked reconnected
            host.station_left("R03", "S3")
            host.station_joined("R03", "S3", "bot")
            seen["reconnected_slot"] = slot.rating_slot_id
        elif not slot.first_atom and "later" not in seen:
            seen["later"] = [ack(_sub("R01", "S1", slot, d=None))]
            seen["later_slot"] = slot.rating_slot_id
        if "prev" in previous and "closed" not in seen:
            seen["closed"] = [ack(_sub("R02", "S2", previous["prev"]))]
        previous["prev"] = slot

    def policy(seat, slot):
        if slot.rating_slot_id == seen.get("later_slot") and seat.rater_id == "R02":
            return None  # a missing rating
        return base(seat, slot)

    base = sim.seeded_policy(RUN)
    batch = sim.make_sim_batch(
        tmp_path_factory.mktemp("host"),
        RUN,
        clock=ManualClock(),
        policy=policy,
        propose={method: propose},
    )
    batch.panel.probe = probe
    with batch.panel:
        batch.orchestrator.run_atom(a1)
        seen["between"] = [batch.orchestrator.panel_host().snapshot().state]
        batch.orchestrator.run_atom(a2)
    return batch, seen


def test_rating_refusal_codes(probed):
    _, seen = probed
    assert seen["first"] == [
        "E_FIRST_ATOM",
        "E_LOCKED",
        "E_UNKNOWN_RATER",
        "E_PROTOCOL",
        "E_UNKNOWN_SLOT",
        "E_SLOT_CLOSED",
    ]
    assert set(seen["placeholder"]) == {"E_PLACEHOLDER"}
    assert seen["later"] == ["E_PROTOCOL"]
    assert seen["closed"] == ["E_SLOT_CLOSED"]
    assert seen["refused"] == [
        "E_UNKNOWN_RATER",
        "E_UNKNOWN_RATER",
        "E_UNKNOWN_RATER",
        "E_PROTOCOL",
        "E_UNKNOWN_SLOT",
    ]
    batch, _ = probed
    dupes = [a for a in batch.panel.acks if a[2] is not None]
    assert dupes == []  # the probe's refused calls never reached the bots' own submissions


def test_snapshot_assets_and_states(probed):
    _, seen = probed
    assert seen["snapshot"] == ["slot", True, True]
    assert seen["asset"] == [True, "KeyError"]
    assert seen["between"] == ["between_atoms"]


def test_rating_records_per_seat(probed):
    batch, seen = probed
    ratings = read_records(batch.layout.log("rating"), RatingRecord)
    assert len(ratings) == 2 * 36 * 3
    slots = read_records(batch.layout.log("slot"), SlotRecord)
    invalid = {s.slot_id for s in slots if s.outcome is not SlotOutcome.VALID}
    placeholders = [r for r in ratings if r.placeholder]
    assert {r.slot_id for r in placeholders} == invalid
    assert len(placeholders) == 3 * len(invalid)
    scripted = (
        f"{batch.orchestrator.config.panel.order[0]}.{batch.orchestrator.config.atom_order[0]}.r1s3"
    )
    assert scripted in invalid
    assert all(
        r.association is None and r.comfort is None and not r.missing and r.rt_ms is None
        for r in placeholders
    )
    missing = [r for r in ratings if r.missing]
    assert [(r.rater_id, r.rating_slot_id) for r in missing] == [("R02", seen["later_slot"])]
    assert missing[0].rt_ms is None and missing[0].unlock_ms is not None
    reconnected = [r for r in ratings if r.reconnected]
    assert [(r.rater_id, r.rating_slot_id) for r in reconnected] == [
        ("R03", seen["reconnected_slot"])
    ]
    rated = [r for r in ratings if not r.placeholder and not r.missing]
    assert all(r.candidate_onset_ms == 0 and r.rt_ms is not None for r in rated)
    kinds = {r.rater_kind for r in ratings}
    assert kinds == {"bot"}


def test_panel_timing_and_plays(probed):
    batch, _ = probed
    timing = read_records(batch.layout.log("timing"), TimingEvent)
    events = collections.Counter(e.event for e in timing)
    assert events["station_connect"] == 3
    assert events["station_disconnect"] == 1 and events["station_reconnect"] == 1
    assert events["clock_sync"] == 3 and events["asset_ready"] == 1
    failed = [e for e in timing if e.event == "asset_ready"]
    assert failed[0].detail.endswith(" failed") and failed[0].component == "panel"
    plays = read_records(batch.layout.log("play"), PlayEvent)
    assert {p.context for p in plays} == {"rating_candidate", "rating_reference"}
    assert all(p.audio_kind == "atom" and p.result == "played" for p in plays)
    candidates = [p for p in plays if p.context == "rating_candidate"]
    assert all(p.slot_id is not None for p in candidates)
