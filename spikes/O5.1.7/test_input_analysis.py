import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("input_analysis", Path(__file__).with_name("input_analysis.py"))
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


class InputAnalysisTests(unittest.TestCase):
    def events(self, loss=False):
        row = dict(tester_code="T01", method="controller-ray", trial_id="1", attempt="1",
                   prompt_target="A", prompt_action="tray-1", target="A", action="tray-1")
        data = [dict(row, event=event, host_s=str(time)) for time, event in
                ((1, "prompt"), (2, "target"), (3, "action"), (4, "commit"))]
        data[1]["target"] = "B"
        if loss:
            data.insert(3, dict(row, event="input_lost", host_s="3.5"))
        return data

    def test_schedule_balanced_and_all_32_legal(self):
        rows = analysis.schedule()["trials"]
        self.assertEqual(len(rows), 192)
        for tester in ("T01", "T02", "T03"):
            for method in analysis.METHODS:
                commands = [(r["target"], r["action"]) for r in rows if r["tester_code"] == tester and r["method"] == method]
                self.assertEqual(len(set(commands)), 32)
                self.assertTrue(all(analysis.legal(*pair) for pair in commands))
        self.assertEqual(rows[0]["method"], "controller-ray")
        self.assertEqual(rows[64]["method"], "hand-poke")
        self.assertFalse(analysis.legal("A", "container-1"))

    def test_errors_are_not_accidental_commits_without_annotation(self):
        trials, result = analysis.analyze(self.events(), [])
        summary = result["controller-ray"]
        self.assertEqual(summary["median_s"], 3)
        self.assertEqual(summary["wrong_selection_rate"], .5)
        self.assertIsNone(summary["accidental_commit_count"])
        self.assertFalse(summary["speed_screen_met"])

    def test_loss_attempt_excluded_from_speed_but_retained(self):
        trials, result = analysis.analyze(self.events(True), [])
        self.assertTrue(trials[0]["input_loss"])
        self.assertEqual(result["controller-ray"]["attempts"], 1)
        self.assertIsNone(result["controller-ray"]["median_s"])
        self.assertEqual(result["controller-ray"]["tracking_loss_events"], 1)

    def test_duplicate_or_illegal_commit_fails(self):
        data = self.events()
        with self.assertRaises(ValueError):
            analysis.analyze(data + [data[-1]], [])
        data[-1]["action"] = "container-1"
        with self.assertRaises(ValueError):
            analysis.analyze(data, [])

    def test_ladder_requires_every_tester_and_margin(self):
        rows = [dict(tester_code=t, angle_deg=.4, labels_tested=8, errors=0) for t in ("T01", "T02", "T03")]
        self.assertAlmostEqual(analysis.legibility(rows, ["T01", "T02", "T03"])["candidate_panel_text_deg"], .6)
        self.assertIsNone(analysis.legibility(rows[:-1], ["T01", "T02", "T03"])["minimum_all_read_deg"])
        rows[1]["errors"] = 1
        self.assertIsNone(analysis.legibility(rows, ["T01", "T02", "T03"])["minimum_all_read_deg"])


if __name__ == "__main__":
    unittest.main()
