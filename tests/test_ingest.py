import importlib
import unittest

from quarantine import ingest, replay

module = importlib.import_module("quarantine.ingest")


class IngestTest(unittest.TestCase):
    def setUp(self):
        module._good.clear()

    def test_good_batch(self):
        self.assertEqual(ingest([{"id": 1}])["ingested"], 1)

    def test_one_bad_row_aborts_all(self):
        with self.assertRaises(ValueError):
            ingest([{"id": 1}, {"id": 2, "bad": True}])
        self.assertEqual(len(module._good), 0)

    def test_replay_not_supported(self):
        with self.assertRaises(NotImplementedError):
            replay("b1")
