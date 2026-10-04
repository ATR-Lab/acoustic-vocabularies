"""Synthetic unit fixtures only; these are not apparatus measurements."""
import unittest
from summarize_frames import summarize


def frame(tick, interval, hz="90", active="1"):
    return dict(host_ticks=str(tick), interval_ms=str(interval), refresh_hz=hz,
                measurement_active=active, xr_running="1")


class SummaryTests(unittest.TestCase):
    def test_percentiles_budget_gaps_and_unknown_rate(self):
        result = summarize([frame(1, 5, active="0"), frame(2, 10), frame(3, 12), frame(4, 251, hz="0")])
        self.assertEqual(result["samples"], 3)
        self.assertEqual(result["p95_ms"], 251)
        self.assertEqual(result["frames_over_observed_budget"], 1)
        self.assertEqual(result["frames_with_unknown_budget"], 1)
        self.assertEqual(result["application_gaps_above_250ms"], 1)
        self.assertFalse(result["at_least_15_minutes"])
        self.assertFalse(result["single_observed_refresh_rate"])
        self.assertIsNone(result["presentation_freezes_above_250ms"])

    def test_rejects_nonmonotonic_and_nonfinite_data(self):
        for rows in ([frame(2, 10), frame(1, 10)], [frame(1, "nan")]):
            with self.assertRaises(ValueError):
                summarize(rows)

    def test_no_ready_is_not_a_zero_frame_success(self):
        with self.assertRaises(ValueError):
            summarize([frame(1, 10, active="0")])


if __name__ == "__main__":
    unittest.main()
