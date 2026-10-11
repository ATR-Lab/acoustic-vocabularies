"""Study A message provenance in delivered-content reconciliation; synthetic only.

Native attempts simulation-test-A-D0-023-isaac2-019/-020 delivered all 108
message-lesson plays as runtime composites (waveform_sha256 null, both atom
PCM hashes, PCM == package composite) while the 36 trained-block plays used
the package WAV. The reconciler required the WAV file hash for every Study A
trained-message request and aborted with MOCK_TRAINED_FILE_HASH.
"""
from collections import defaultdict
import struct
import tempfile
import unittest
from pathlib import Path

from tools.mock_visit import content
from tools.mock_visit.records import EvidenceError, sha
from tools.mock_visit.reconcile import read, relative


def wav(samples: bytes) -> bytes:
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36 + len(samples), b'WAVE', b'fmt ', 16, 1, 1, 48000, 96000, 2, 16,
                       b'data', len(samples)) + samples


def build_package(root: Path):
    """A complete, self-consistent DEMO Study A package: 16 atoms, 32 messages."""
    files, atoms, messages, pcm = {}, [], [], {}
    actions, referents = [f"A-a{i}" for i in range(1, 9)], [f"A-r{i}" for i in range(1, 9)]
    for n, atom in enumerate(actions + referents):
        samples = bytes([n + 1, 0]) * (100 + n)
        raw = wav(samples); name = f"atoms/{atom}.wav"; files[name] = raw; pcm[atom] = samples
        atoms.append(dict(atom_id=atom, path=name, pcm_sha256=sha(samples), file_sha256=sha(raw), n_samples=len(samples) // 2))
    for n in range(32):
        action, referent = actions[n % 8], referents[(n // 8 + n) % 8]
        composite = pcm[action] + bytes(9600 * 2) + pcm[referent]
        trained = n < 28; mid = f"A-m{n + 1:02d}"; path = file_hash = None
        if trained:
            raw = wav(composite); path = f"messages/{mid}.wav"; files[path] = raw; file_hash = sha(raw)
        messages.append(dict(message_id=mid, action_atom=action, referent_atom=referent, status="trained" if trained else "heldout",
                             composite_sha256=sha(composite), n_samples=len(composite) // 2, path=path, file_sha256=file_hash))
    files["audio.json"] = content.canonical(dict(study="A", atoms=atoms, messages=messages))
    for name, raw in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True); (root / name).write_bytes(raw)
    manifest = dict(demo=True, study="A", files={k: dict(bytes=len(v), sha256=sha(v)) for k, v in files.items()})
    manifest["package_sha256"] = sha(content.canonical(manifest))
    raw = content.canonical(manifest); (root / "manifest.json").write_bytes(raw)
    return manifest["package_sha256"], dict(path="manifest.json", sha256=sha(raw)), {m["message_id"]: m for m in messages}, {a["atom_id"]: a for a in atoms}


class StudyAMessageProvenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.hash, self.pin, self.messages, self.atoms = build_package(self.root)
        self.artifacts = defaultdict(list); self.artifacts["package_manifest"] = [(self.pin, b"")]

    def tearDown(self):
        self.tmp.cleanup()

    def file_play(self, mid):
        m = self.messages[mid]
        return dict(pcm_sha256=m["composite_sha256"], waveform_sha256=m["file_sha256"], action_pcm_sha256=None, referent_pcm_sha256=None)

    def composite_play(self, mid):
        m = self.messages[mid]
        return dict(pcm_sha256=m["composite_sha256"], waveform_sha256=None,
                    action_pcm_sha256=self.atoms[m["action_atom"]]["pcm_sha256"], referent_pcm_sha256=self.atoms[m["referent_atom"]]["pcm_sha256"])

    def verify(self, block, mid, payload):
        items = {"opp": dict(block=block, trial_type="message_lesson" if block == "message_lessons" else "full_message", message_id=mid)}
        requests = {"r" * 32: dict(opportunity_id="opp", audio_request_id="r" * 32, payload=payload)}
        return content.verify(self.root, self.artifacts, items, requests, self.hash, read, relative)

    def test_message_lesson_composite_and_trained_block_file_are_both_accepted(self):
        self.assertEqual(self.verify("message_lessons", "A-m01", self.composite_play("A-m01"))[1], 1)
        self.assertEqual(self.verify("trained", "A-m01", self.file_play("A-m01"))[1], 1)
        self.assertEqual(self.verify("novel", "A-m30", self.composite_play("A-m30"))[1], 1)

    def test_trained_block_still_requires_the_package_file(self):
        with self.assertRaisesRegex(EvidenceError, "MOCK_TRAINED_FILE_HASH"):
            self.verify("trained", "A-m01", self.composite_play("A-m01"))
        play = self.file_play("A-m01"); play["waveform_sha256"] = self.messages["A-m02"]["file_sha256"]
        with self.assertRaisesRegex(EvidenceError, "MOCK_TRAINED_FILE_HASH"):self.verify("trained", "A-m01", play)

    def test_message_lesson_composite_tamper_is_refused(self):
        other = self.atoms["A-a2"]["pcm_sha256"]
        for key, value, code in (("action_pcm_sha256", other, "MOCK_COMPOSITE_PROVENANCE"),
                                 ("referent_pcm_sha256", other, "MOCK_COMPOSITE_PROVENANCE"),
                                 ("action_pcm_sha256", None, "MOCK_COMPOSITE_PROVENANCE"),
                                 ("waveform_sha256", "b" * 64, "MOCK_COMPOSITE_PROVENANCE"),
                                 ("pcm_sha256", "c" * 64, "MOCK_DELIVERED_MESSAGE_HASH")):
            play = self.composite_play("A-m01"); play[key] = value
            with self.subTest(key=key, value=value), self.assertRaisesRegex(EvidenceError, code):
                self.verify("message_lessons", "A-m01", play)
        # The lesson path is the composer's, never the stored-file path.
        with self.assertRaisesRegex(EvidenceError, "MOCK_COMPOSITE_PROVENANCE"):
            self.verify("message_lessons", "A-m01", self.file_play("A-m01"))

    def test_heldout_message_never_reaches_a_lesson(self):
        with self.assertRaisesRegex(EvidenceError, "MOCK_HELDOUT_OUTSIDE_NOVEL"):
            self.verify("message_lessons", "A-m30", self.composite_play("A-m30"))


if __name__ == "__main__":
    unittest.main()
