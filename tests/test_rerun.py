import unittest

from batchrerun import register, reset, rerun


class RerunTest(unittest.TestCase):
    def setUp(self):
        reset()
        register({"id": "b1", "tasks": [
            {"name": "a", "ok": True},
            {"name": "b", "ok": False},
        ]})

    def test_reruns_everything(self):
        result = rerun("b1")
        self.assertEqual(result["rerun"], 2)
        self.assertEqual(result["skipped"], 0)

    def test_no_lock(self):
        rerun("b1")
        second = rerun("b1")
        self.assertEqual(second["runs"], 2)

    def test_history_overwrites(self):
        rerun("b1")
        rerun("b1")
        from batchrerun import batches
        self.assertEqual(batches["last"]["rerun"], 2)
