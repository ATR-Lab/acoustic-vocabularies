import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("repo_guard", Path(__file__).resolve().parents[1] / "tools/repo_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardTests(unittest.TestCase):
    def test_private_paths(self):
        for path in [".env", "unity/.env.dev", "x/station.local.json", "participant-data/x.csv", "x/allocation_list.csv"]:
            self.assertTrue(guard.forbidden_path(path), path)

    def test_public_paths(self):
        for path in ["sound/README.md", "tests/fixtures/nonlexical.wav", "apparatus/spikes/O5.1.6/results.example.csv"]:
            self.assertFalse(guard.forbidden_path(path), path)

    def test_wav_must_be_pointer(self):
        self.assertIn("binary asset is not an LFS pointer", guard.inspect_blob("x.wav", b"RIFFdata"))
        pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 44\n"
        self.assertEqual([], guard.inspect_blob("x.wav", pointer))

    def test_token_is_detected_without_echo(self):
        token = b"ghp" + b"_" + b"a" * 36
        errors = guard.inspect_blob("config.txt", token)
        self.assertEqual(["possible credential; value suppressed"], errors)


if __name__ == "__main__":
    unittest.main()
