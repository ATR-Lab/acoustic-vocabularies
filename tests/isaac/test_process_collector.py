"""Synthetic Unix transport checks for the separate strict diagnostic receiver."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from isaac.publisher.process_collector import ProcessCollector
from isaac.publisher.protocol import PublicRegistry
from isaac.publisher.transport import WebSocketTransport


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("websockets"), "Approved Linux websockets runtime required")
class ProcessCollectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)/"state.sock"
        self.frame = json.loads((Path(__file__).parents[1]/"fixtures/publisher-state.json").read_text())
        f = self.frame
        self.registry = PublicRegistry(f['station_id'], f['scene_sha256'], f['reset_snapshot_sha256'],
            tuple(f['joint_names']), tuple((o['id'], tuple(sorted(o['state']))) for o in f['objects']),
            tuple(sorted({o['state']['location'] for o in f['objects'] if 'location' in o['state']})))
        self.transport = WebSocketTransport(socket_path=self.path)
        self.collector = ProcessCollector(self.path, self.registry)

    def tearDown(self):
        self.collector.close()
        self.transport.close()
        self.directory.cleanup()

    def until(self, predicate):
        deadline = time.monotonic()+5
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(predicate())

    def send(self, sequence, expected_count):
        frame = deepcopy(self.frame)
        frame['seq'] = sequence
        self.transport.submit(json.dumps(frame))
        self.until(lambda: self.collector.count == expected_count or self.collector.error is not None)
        self.assertIsNone(self.collector.error)

    def test_validated_frames_samples_and_cleanup(self):
        for sequence in range(10):
            self.send(sequence, sequence+1)
        directory = self.collector.directory
        self.collector.close()
        self.assertEqual(self.collector.count, 10)
        self.assertEqual(self.collector.sequence_gaps, 0)
        self.assertEqual(self.collector.first['seq'], 0)
        self.assertEqual(self.collector.last['seq'], 9)
        self.assertFalse(directory.exists())
        self.assertIsNotNone(self.collector.process.returncode)

    def test_sequence_gap_accounting_unchanged(self):
        self.send(0, 1)
        self.send(2, 2)
        self.collector.close()
        self.assertEqual(self.collector.sequence_gaps, 1)

    def test_strict_schema_failure_propagates(self):
        frame = deepcopy(self.frame)
        frame['private_command'] = 'synthetic-invalid-field'
        self.transport.submit(json.dumps(frame))
        self.until(lambda: self.collector.error is not None)
        self.collector.close()
        self.assertEqual(self.collector.count, 0)
        self.assertIsNotNone(self.collector.error)

    def test_unexpected_child_exit_is_a_fault(self):
        self.collector.process.terminate()
        self.until(lambda: self.collector.error is not None)
        self.assertEqual(self.collector.count, 0)


if __name__ == '__main__':
    unittest.main()
