import os
import shutil
import tempfile
import threading
import unittest
import unittest.mock

import ckptgen.checkpoint as checkpoint
from ckptgen import fallbacks, generations, load, save


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_save_load(self):
        save("t1", {"cursor": 3}, root=self.dir)
        self.assertEqual(load("t1", root=self.dir)["cursor"], 3)

    def test_overwrite_loses_old(self):
        save("t1", {"cursor": 1}, root=self.dir)
        save("t1", {"cursor": 2}, root=self.dir)
        self.assertEqual(load("t1", 1, root=self.dir)["cursor"], 2)

    def test_no_generation_field(self):
        save("t1", {"cursor": 1}, root=self.dir)
        self.assertNotIn("generation", load("t1", root=self.dir))

    def test_atomic_write_keeps_previous(self):
        save("t2", {"cursor": 1}, root=self.dir)
        with unittest.mock.patch.object(
                checkpoint, "_atomic_replace",
                side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                save("t2", {"cursor": 2}, root=self.dir)
        self.assertEqual(load("t2", root=self.dir)["cursor"], 1)
        self.assertEqual(generations("t2", root=self.dir), [1])
        leftovers = [name for name in os.listdir(os.path.join(self.dir, "t2"))
                     if name.startswith(".tmp-")]
        self.assertEqual(leftovers, [])

    def test_prune_keeps_recent(self):
        for cursor in range(1, 6):
            info = save("t3", {"cursor": cursor}, root=self.dir, keep=2)
        self.assertEqual(info["generation"], 5)
        self.assertEqual(info["kept"], [5, 4])
        self.assertEqual(generations("t3", root=self.dir), [5, 4])
        self.assertEqual(load("t3", 2, root=self.dir)["cursor"], 4)

    def test_prune_skips_generation_being_read(self):
        save("t4", {"cursor": 1}, root=self.dir, keep=1)
        entered = threading.Event()
        release = threading.Event()
        original = checkpoint._read_payload

        def slow_read(path):
            if path.endswith("gen-000001.json"):
                entered.set()
                release.wait(5)
            return original(path)

        outcome = {}

        def reader():
            try:
                outcome["state"] = load("t4", 1, root=self.dir)
            except Exception as error:
                outcome["error"] = error

        thread = threading.Thread(target=reader)
        with unittest.mock.patch.object(
                checkpoint, "_read_payload", side_effect=slow_read):
            thread.start()
            self.assertTrue(entered.wait(5))
            save("t4", {"cursor": 2}, root=self.dir, keep=1)
            save("t4", {"cursor": 3}, root=self.dir, keep=1)
            self.assertTrue(os.path.exists(checkpoint._path("t4", 1, self.dir)))
            release.set()
            thread.join(5)
        self.assertEqual(outcome["state"]["cursor"], 1)

    def test_corrupt_falls_back(self):
        save("t5", {"cursor": 1}, root=self.dir)
        save("t5", {"cursor": 2}, root=self.dir)
        with open(checkpoint._path("t5", 2, self.dir), "w",
                  encoding="utf-8") as handle:
            handle.write("{broken")
        self.assertEqual(load("t5", 1, root=self.dir)["cursor"], 1)
        records = fallbacks("t5")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["generation"], 2)
        self.assertEqual(records[0]["fell_back_to"], 1)

    def test_concurrent_writes_serialized(self):
        def worker():
            for _ in range(5):
                save("t6", {"cursor": 1}, root=self.dir, keep=100)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(generations("t6", root=self.dir),
                         list(range(40, 0, -1)))
        for generation in range(1, 41):
            payload = checkpoint._read_payload(
                checkpoint._path("t6", generation, self.dir))
            self.assertEqual(payload["generation"], generation)
