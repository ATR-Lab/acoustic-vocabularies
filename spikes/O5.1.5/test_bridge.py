import importlib.util
import json
import math
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).parent
def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

protocol = load("bridge_protocol")
analysis = load("analyze_bridge")


class BridgeTests(unittest.TestCase):
    def frame(self):
        return protocol.FrameBuilder(["public_joint"], "synthetic").build(0, 0, [0.])

    def row(self, event, received, seq="", session="x", **extra):
        return dict(event=event, recv_client_s=str(received), session_id=session, seq=str(seq),
                    publish_host_ns=str(round((received + 9.99) * 1e9)), source_kind="live",
                    apply_ms=".1", queue_drops="0", **extra)

    def test_state_has_no_private_fields(self):
        frame = self.frame()
        protocol.validate_frame(frame)
        frame["target"] = "forbidden"
        with self.assertRaises(ValueError):
            protocol.validate_frame(frame)

    def test_canonical_order_and_nonfinite_values_rejected(self):
        frame = self.frame()
        with self.assertRaises(ValueError):
            protocol.validate_frame(frame, ["other_joint"])
        frame["joint_positions"] = [math.nan]
        with self.assertRaises(ValueError):
            protocol.validate_frame(frame)

    def test_nested_private_field_rejected(self):
        frame = self.frame()
        frame["objects"] = [dict(id="placeholder_0", position_m=[0, 0, 0], rotation_xyzw=[0, 0, 0, 1], trial="forbidden")]
        with self.assertRaises(ValueError):
            protocol.validate_frame(frame)

    def test_echo_four_timestamps_offset_and_server_work(self):
        value = analysis.echo_statistics(1, "11010000000", "11012000000", 1.022)
        self.assertAlmostEqual(value["network_rtt_ms"], 20)
        self.assertAlmostEqual(value["offset_server_minus_client_s"], 10)
        self.assertAlmostEqual(value["symmetry_uncertainty_ms"], 10)
        with self.assertRaises(ValueError):
            analysis.echo_statistics(2, "11010000000", "11012000000", 1)

    def test_missing_reordered_and_restart_sequences_separate(self):
        rows = [self.row("state", 1, 0), self.row("state", 1.1, 2), self.row("state", 1.2, 1), self.row("state", 1.3, 0, "new")]
        result = analysis.summarize(rows, 30)
        self.assertEqual(result["missing_sequences_within_sessions"], 1)
        self.assertEqual(result["duplicate_or_reordered_frames"], 1)
        self.assertEqual(result["session_count"], 2)
        self.assertIsNone(result["one_way_estimate_median_ms"])

    def test_terminal_outage_counted_without_recovery(self):
        rows = [self.row("run_start", 0), self.row("state", .01, 0), self.row("disconnected", .1), self.row("run_end", 1800)]
        result = analysis.summarize(rows, 30)
        self.assertEqual(result["gaps_over_250ms"], 1)
        self.assertTrue(result["unrecovered_disconnect"])
        self.assertFalse(result["steady_state_screen_met"])

    def test_synthetic_never_passes_live_screen(self):
        rows = [self.row("run_start", 0), self.row("state", .01, 0), self.row("run_end", .02)]
        rows[1]["source_kind"] = "synthetic"
        self.assertFalse(analysis.summarize(rows, 30, .01)["steady_state_screen_met"])

    def qualified_rows(self):
        rows = [self.row("run_start", 0), self.row("echo", .002, c0_s="0", s1_ns="10001000000", s2_ns="10001000000", c3_s=".002")]
        for seq, timestamp in enumerate((.02, .05, .08)):
            row = self.row("state", timestamp, seq)
            row.update(source_fresh="true", applied="true")
            rows.append(row)
        rows.append(self.row("run_end", .1))
        return rows

    def test_unknown_clock_echo_gap_and_stale_queue_cannot_pass(self):
        rows = self.qualified_rows()
        self.assertTrue(analysis.summarize(rows, 30, .09)["steady_state_screen_met"])
        self.assertFalse(analysis.summarize([r for r in rows if r["event"] != "echo"], 30, .09)["steady_state_screen_met"])
        for reason in ("unknown_source_clock", "stale_source", "stale_queued_frame", "invalid_echo"):
            self.assertFalse(analysis.summarize(rows + [self.row(reason, .09)], 30, .09)["steady_state_screen_met"])

    def test_source_progression_and_actual_application_required(self):
        rows = self.qualified_rows()
        rows[2]["applied"] = "false"
        self.assertFalse(analysis.summarize(rows, 30, .09)["steady_state_screen_met"])
        rows = self.qualified_rows()
        rows[3]["publish_host_ns"] = rows[2]["publish_host_ns"]
        result = analysis.summarize(rows, 30, .09)
        self.assertEqual(result["progression_violations"], 1)
        self.assertFalse(result["steady_state_screen_met"])

    def test_diagnostic_never_passes_even_if_other_fields_claim_qualified(self):
        rows = self.qualified_rows()
        rows[2]["diagnostic"] = "true"
        result = analysis.summarize(rows, 30, .09)
        self.assertTrue(result["diagnostic_capture"])
        self.assertFalse(result["steady_state_screen_met"])

    def test_missing_or_duplicate_boundaries_reported(self):
        rows = self.qualified_rows()[:-1]
        result = analysis.summarize(rows, 30, .09)
        self.assertFalse(result["start_end_boundaries_valid"])
        self.assertFalse(result["steady_state_screen_met"])
        rows = self.qualified_rows()
        self.assertFalse(analysis.summarize(rows + [rows[-1]], 30, .09)["start_end_boundaries_valid"])

    def test_schema_recursive_unknown_keys_rejected(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((HERE.parents[1] / "apparatus/schemas/bridge-state.schema.json").read_text())
        validator = Draft202012Validator(schema)
        validator.validate(self.frame())
        invalid = self.frame(); invalid["trial_id"] = "forbidden"
        self.assertTrue(list(validator.iter_errors(invalid)))


if __name__ == "__main__":
    unittest.main()
