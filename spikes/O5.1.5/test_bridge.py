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

    def test_schema_recursive_unknown_keys_rejected(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((HERE.parents[1] / "apparatus/schemas/bridge-state.schema.json").read_text())
        validator = Draft202012Validator(schema)
        validator.validate(self.frame())
        invalid = self.frame(); invalid["trial_id"] = "forbidden"
        self.assertTrue(list(validator.iter_errors(invalid)))


if __name__ == "__main__":
    unittest.main()
