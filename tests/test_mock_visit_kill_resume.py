"""Synthetic #67 kill/resume verification cases; not native process evidence."""
import copy
import unittest

from tools.mock_visit.kill_resume import validate_receipt, verify_records
from tools.mock_visit.records import EvidenceError

E1, E2 = "1" * 32, "2" * 32


def receipt(target="MOCK-02", items=4):
    return {"version": 1, "scope": "SIMULATION_TEST", "kind": "process_kill_resume", "items": items, "kill_target_opportunity": target,
            "killed": {"process_id": 11, "session_nonce": "a" * 32, "data_clock_epoch": E1, "exit_code": -1,
                       "kill_utc": "2026-10-07T00:00:30Z", "last_observed_record_sha256": None, "last_observed_record_count": None},
            "resumed": {"process_id": 12, "session_nonce": "b" * 32, "data_clock_epoch": E2, "exit_code": 0, "result_status": "MOCK_BLOCK_COMPLETE"}}


class Block:
    def __init__(self):
        self.rows = []

    def add(self, epoch, kind, opportunity=None, **payload):
        n = len(self.rows)
        self.rows.append({"sequence": n, "sha256": f"{n:064x}", "clock_epoch": epoch, "event_type": kind,
                          "opportunity_id": opportunity, "payload": payload})

    def item(self, epoch, opportunity, done=True, events=("Loaded", "Ready", "CueRequested", "ResponseOpen", "Closed", "Reset", "Done")):
        consumed = False
        for state in events:
            if state == "CueRequested":
                consumed = True
            if state == "Done" and not done:
                break
            for edge in ("state_before", "state_after"):
                audible = "Uncertain" if consumed else "NotRequested"
                self.add(epoch, "session", opportunity, event=edge, state=state, exposure_consumed=consumed, audible_status=audible)
                if state == "CueRequested" and edge == "state_after":
                    self.add(epoch, "audio_request", opportunity, code="AUDIO_REQUESTED")

    def resume(self, epoch):
        self.add(epoch, "session", None, event="operator_resume", state=None, exposure_consumed=False, audible_status="NoCue")


def native(kill_at_response=True):
    b = Block(); b.resume(E1); b.item(E1, "MOCK-01")
    b.item(E1, "MOCK-02", done=False, events=("Loaded", "Ready", "CueRequested", "ResponseOpen"))
    observed = b.rows[-1]
    b.resume(E2); b.item(E2, "MOCK-03"); b.item(E2, "MOCK-04")
    b.add(E2, "session", None, event="visit_complete", state=None, exposure_consumed=False, audible_status="NoCue")
    r = receipt(); r["killed"]["last_observed_record_sha256"] = observed["sha256"]; r["killed"]["last_observed_record_count"] = observed["sequence"] + 1
    trials = [{"opportunity_id": o, "exposure_consumed": "true", "interrupted": "true" if o == "MOCK-02" else "false"} for o in ("MOCK-01", "MOCK-02", "MOCK-03", "MOCK-04")]
    exposures = [{"opportunity_id": o} for o in ("MOCK-01", "MOCK-02", "MOCK-03", "MOCK-04")]
    return r, b, trials, exposures


class KillResume(unittest.TestCase):
    def run_case(self, r, b, trials, exposures):
        return verify_records(r, b.rows, trials, exposures)

    def test_killed_item_consumed_and_resume_starts_at_next_unplayed(self):
        report = self.run_case(*native())
        self.assertTrue(report["kill_resume_verified"], report)
        self.assertEqual(report["values"]["second_process_cued"], ["MOCK-03", "MOCK-04"])
        self.assertEqual(report["values"]["duplicate_first_exposures"], [])
        self.assertTrue(report["values"]["block_completed_after_resume"])

    def test_replayed_killed_item_is_a_duplicate_first_exposure(self):
        r, b, t, e = native()
        b.rows = [x for x in b.rows if x["clock_epoch"] == E1] + []
        b.resume(E2); b.item(E2, "MOCK-02"); b.item(E2, "MOCK-03")
        e = e + [{"opportunity_id": "MOCK-02"}]
        report = self.run_case(r, b, t, e)
        self.assertFalse(report["checks"]["resumed_at_next_unplayed_item"])
        self.assertFalse(report["checks"]["killed_or_earlier_item_never_renewed"])
        self.assertFalse(report["checks"]["no_duplicate_first_exposure"])
        self.assertIn("MOCK-02", report["values"]["duplicate_first_exposures"])
        self.assertFalse(report["kill_resume_verified"])

    def test_skipping_an_unplayed_item_is_not_a_resume(self):
        r, b, t, e = native()
        b.rows = [x for x in b.rows if x["clock_epoch"] == E1]
        b.resume(E2); b.item(E2, "MOCK-04")
        self.assertFalse(self.run_case(r, b, t, e)["checks"]["resumed_at_next_unplayed_item"])

    def test_killed_item_must_stay_consumed_and_interrupted(self):
        r, b, t, e = native()
        for x in b.rows:
            if x["opportunity_id"] == "MOCK-02" and x["event_type"] == "session":
                x["payload"]["exposure_consumed"] = False
        t[1]["interrupted"] = "false"
        report = self.run_case(r, b, t, e)
        self.assertFalse(report["checks"]["killed_item_consumed_uncertain"])
        self.assertFalse(report["checks"]["killed_trial_row_consumed_and_interrupted"])

    def test_automatic_resume_without_operator_rejected(self):
        r, b, t, e = native()
        b.rows = [x for x in b.rows if not (x["clock_epoch"] == E2 and x["payload"].get("event") == "operator_resume")]
        self.assertFalse(self.run_case(r, b, t, e)["checks"]["resume_waited_for_operator"])

    def test_kill_between_items_or_single_epoch_is_not_evidence(self):
        r, b, t, e = native()
        b.rows = [x for x in b.rows if x["clock_epoch"] == E1]
        report = self.run_case(r, b, t, e)
        self.assertFalse(report["checks"]["two_process_epochs_in_order"])
        r2 = copy.deepcopy(r); r2["kill_target_opportunity"] = "MOCK-01"
        self.assertFalse(self.run_case(r2, *native()[1:])["checks"]["killed_item_was_the_single_open_cue"])

    def test_kill_must_follow_the_observed_record(self):
        r, b, t, e = native(); r["killed"]["last_observed_record_sha256"] = "f" * 64
        self.assertFalse(self.run_case(r, b, t, e)["checks"]["kill_after_observed_record"])

    def test_receipt_contract(self):
        r = native()[0]; validate_receipt(r)
        for change in ({"scope": "PARTICIPANT"}, {"kill_target_opportunity": "MOCK-04"}, {"extra": 1}, {"items": 1}):
            bad = dict(copy.deepcopy(r), **change)
            with self.subTest(change=change), self.assertRaises(EvidenceError):
                validate_receipt(bad)
        bad = copy.deepcopy(r); bad["resumed"]["process_id"] = bad["killed"]["process_id"]
        with self.assertRaises(EvidenceError):
            validate_receipt(bad)


if __name__ == "__main__":
    unittest.main()
