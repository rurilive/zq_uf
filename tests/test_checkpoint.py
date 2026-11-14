import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest

import ckptgen.checkpoint as cp
from ckptgen import (
    CheckpointError,
    current_generation,
    fallback_records,
    load,
    retained_generations,
    save,
)


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


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_crash_before_rename_keeps_previous_generation(self):
        save("t1", {"cursor": 1}, root=self.dir)
        original_commit = cp._commit

        def crash_commit(tmp_path, final_path):
            raise RuntimeError("模拟写到一半崩溃")

        cp._commit = crash_commit
        try:
            with self.assertRaises(RuntimeError):
                save("t1", {"cursor": 2}, root=self.dir)
        finally:
            cp._commit = original_commit

        self.assertEqual(load("t1", root=self.dir), {"cursor": 1})
        self.assertEqual(current_generation("t1", root=self.dir), 0)
        task_dir = os.path.join(self.dir, "t1")
        self.assertEqual(
            [n for n in os.listdir(task_dir) if n.endswith(".tmp")], []
        )

    def test_writes_via_tmp_then_atomic_replace(self):
        calls = []
        original_commit = cp._commit

        def spy_commit(tmp_path, final_path):
            self.assertTrue(tmp_path.endswith(".tmp"))
            self.assertTrue(os.path.exists(tmp_path))
            self.assertFalse(os.path.exists(final_path))
            calls.append((tmp_path, final_path))
            return original_commit(tmp_path, final_path)

        cp._commit = spy_commit
        try:
            save("t1", {"cursor": 1}, root=self.dir)
        finally:
            cp._commit = original_commit

        self.assertEqual(len(calls), 1)
        self.assertFalse(os.path.exists(calls[0][0]))

    def test_envelope_carries_generation_and_checksum(self):
        save("t1", {"cursor": 1}, root=self.dir)
        save("t1", {"cursor": 2}, root=self.dir)
        path = cp._gen_path("t1", 1, self.dir)
        with open(path, encoding="utf-8") as handle:
            envelope = json.load(handle)
        self.assertEqual(envelope["generation"], 1)
        payload = json.dumps({"cursor": 2}, ensure_ascii=False).encode("utf-8")
        self.assertEqual(
            envelope["checksum"], hashlib.sha256(payload).hexdigest()
        )
        self.assertEqual(envelope["state"], {"cursor": 2})


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_keeps_newest_and_prunes_oldest_first(self):
        results = []
        for cursor in range(5):
            results.append(save("t1", {"cursor": cursor}, root=self.dir, keep=3))

        self.assertEqual(retained_generations("t1", root=self.dir), [2, 3, 4])
        self.assertEqual(results[-1]["generation"], 4)
        self.assertEqual(results[-1]["retained"], [2, 3, 4])
        self.assertEqual(results[-1]["removed"], [1])
        self.assertFalse(os.path.exists(cp._gen_path("t1", 1, self.dir)))
        self.assertTrue(os.path.exists(cp._gen_path("t1", 2, self.dir)))

    def test_keep_one_keeps_only_current_generation(self):
        for cursor in range(4):
            save("t1", {"cursor": cursor}, root=self.dir, keep=1)
        self.assertEqual(retained_generations("t1", root=self.dir), [3])
        self.assertEqual(load("t1", root=self.dir), {"cursor": 3})

    def test_invalid_keep_rejected(self):
        with self.assertRaises(ValueError):
            save("t1", {"cursor": 1}, root=self.dir, keep=0)

    def test_reader_blocks_cleanup_of_generation_being_read(self):
        for cursor in range(3):
            save("t1", {"cursor": cursor}, root=self.dir, keep=3)
        entered = threading.Event()
        release = threading.Event()

        def reader():
            lock = cp._get_lock(os.path.join(self.dir, "t1"))
            lock.acquire_read()
            entered.set()
            release.wait(timeout=5)
            lock.release_read()

        thread = threading.Thread(target=reader)
        thread.start()
        self.assertTrue(entered.wait(timeout=5))
        writer_done = threading.Event()

        def writer():
            save("t1", {"cursor": 9}, root=self.dir, keep=1)
            writer_done.set()

        write_thread = threading.Thread(target=writer)
        write_thread.start()
        self.assertFalse(writer_done.wait(timeout=1))
        release.set()
        self.assertTrue(writer_done.wait(timeout=5))
        write_thread.join()
        thread.join()
        self.assertEqual(retained_generations("t1", root=self.dir), [3])


class CorruptionFallbackTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_corrupt_latest_falls_back_to_previous(self):
        save("t1", {"cursor": 1}, root=self.dir)
        save("t1", {"cursor": 2}, root=self.dir)
        with open(cp._gen_path("t1", 1, self.dir), "w", encoding="utf-8") as f:
            f.write("{broken")

        self.assertEqual(load("t1", root=self.dir), {"cursor": 1})
        self.assertEqual(load("t1", 1, root=self.dir), {"cursor": 1})
        records = fallback_records("t1")
        self.assertTrue(records)
        self.assertEqual(records[-1]["used"], 0)

    def test_checksum_mismatch_is_corruption(self):
        save("t1", {"cursor": 1}, root=self.dir)
        save("t1", {"cursor": 2}, root=self.dir)
        path = cp._gen_path("t1", 1, self.dir)
        with open(path, encoding="utf-8") as handle:
            envelope = json.load(handle)
        envelope["state"] = {"cursor": 42}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle)

        self.assertEqual(load("t1", 1, root=self.dir), {"cursor": 1})

    def test_missing_generation_falls_back(self):
        for cursor in range(3):
            save("t1", {"cursor": cursor}, root=self.dir)
        os.remove(cp._gen_path("t1", 2, self.dir))
        self.assertEqual(load("t1", 2, root=self.dir), {"cursor": 1})

    def test_all_generations_unreadable_raises(self):
        save("t1", {"cursor": 1}, root=self.dir)
        with open(cp._gen_path("t1", 0, self.dir), "w", encoding="utf-8") as f:
            f.write("nonsense")
        with self.assertRaises(CheckpointError):
            load("t1", root=self.dir)

    def test_load_without_any_generation_raises(self):
        with self.assertRaises(CheckpointError):
            load("ghost", root=self.dir)


class ConcurrentWriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_parallel_saves_are_serialized_and_complete(self):
        errors = []

        def worker(index):
            try:
                save("t1", {"cursor": index}, root=self.dir, keep=30)
            except Exception as error:  # pragma: no cover - 失败即暴露问题
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(
            retained_generations("t1", root=self.dir), list(range(20))
        )
        self.assertEqual(len(load("t1", root=self.dir)), 1)
        task_dir = os.path.join(self.dir, "t1")
        self.assertEqual(
            [n for n in os.listdir(task_dir) if n.endswith(".tmp")], []
        )

    def test_concurrent_saves_respect_retention(self):
        threads = [
            threading.Thread(
                target=save, args=("t1", {"cursor": i}),
                kwargs={"root": self.dir, "keep": 3},
            )
            for i in range(10)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(retained_generations("t1", root=self.dir)), 3)
        self.assertEqual(current_generation("t1", root=self.dir), 9)

    def test_concurrent_reads_while_writing(self):
        seen = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    seen.append(load("t1", root=self.dir)["cursor"])
                except CheckpointError:
                    pass

        readers = [threading.Thread(target=reader) for _ in range(3)]
        for thread in readers:
            thread.start()
        for cursor in range(15):
            save("t1", {"cursor": cursor}, root=self.dir)
        stop.set()
        for thread in readers:
            thread.join()

        self.assertTrue(seen)
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(load("t1", root=self.dir)["cursor"], 14)
