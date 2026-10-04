import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("audio_onsets", Path(__file__).with_name("audio_onsets.py"))
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)


class OnsetAnalysisTests(unittest.TestCase):
    def sync(self):
        rows = [{"host_s": x, "capture_s": 4 + 1.0001 * x, "role": "fit" if x % 2 == 0 else "check"}
                for x in range(8)]
        return audio.fit_sync(rows, .5)

    def test_detect_known_onsets_and_noise(self):
        samples = [0.] * 1000
        samples[20] = .9  # single-sample spike is rejected
        samples[100:104] = [.3] * 4
        samples[500:504] = [.3] * 4
        self.assertEqual(audio.detect(samples, 1000, .2), [.1, .5])

    def test_dropped_pulse_does_not_shift_ids_and_extras_are_ambiguous(self):
        sync = self.sync()
        events = [{"trial_id": str(i), "request_host_s": i} for i in (1, 3, 5)]
        onsets = [4 + 1.0001 + .05, 4 + 5.0005 + .05, 4 + 5.0005 + .2, 20]
        pairs, extra = audio.pair_events(events, onsets, sync)
        self.assertEqual([p["status"] for p in pairs], ["matched", "missing", "ambiguous"])
        self.assertEqual(extra, 1)

    def test_affine_drift_and_no_extrapolation(self):
        sync = self.sync()
        self.assertAlmostEqual(sync["slope"], 1.0001)
        self.assertLess(sync["heldout_max_ms"], 1e-8)
        with self.assertRaises(ValueError):
            audio.pair_events([{"trial_id": "x", "request_host_s": 10}], [], sync)

    def test_overlapping_windows_rejected(self):
        with self.assertRaises(ValueError):
            audio.pair_events([{"trial_id": str(i), "request_host_s": i} for i in (1, 1.2)], [], self.sync())

    def test_no_pass_for_incomplete_or_uncertain_sync(self):
        rows = [{"status": "matched", "offset_ms": 40.} for _ in range(200)]
        sync = self.sync()
        self.assertTrue(audio.summarize(rows, sync, {}, 0)["screening_target_met"])
        self.assertFalse(audio.summarize(rows[:-1], sync, {}, 0)["screening_target_met"])
        sync["screening_bound_ms"] = 21
        self.assertFalse(audio.summarize(rows, sync, {}, 0)["screening_target_met"])

    def test_generated_public_click_pcm(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "test.wav"
            audio.generate_click(target)
            rate, channels = audio.read_pcm(target)
            self.assertEqual((rate, len(channels), len(channels[0])), (48000, 1, 480))


if __name__ == "__main__":
    unittest.main()
