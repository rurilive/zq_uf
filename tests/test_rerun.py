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


class PartialRerunTest(unittest.TestCase):
    def setUp(self):
        reset()
        register({"id": "b1", "tasks": [
            {"name": "a", "ok": True},
            {"name": "b", "ok": False},
        ]})

    def test_second_rerun_only_failed(self):
        rerun("b1")
        result = rerun("b1")
        self.assertEqual(result["rerun"], 1)
        self.assertEqual(result["skipped"], 1)

    def test_partitions_recorded(self):
        rerun("b1")
        from batchrerun import batches
        self.assertEqual(batches["b1"], {"succeeded": ["a"], "failed": ["b"]})

    def test_history_records_attempts(self):
        rerun("b1")
        rerun("b1")
        from batchrerun import history
        self.assertEqual(len(history), 2)
        self.assertEqual([h["rerun"] for h in history], [1, 2])
        self.assertEqual(history[1]["tasks"], 1)
        self.assertEqual(history[1]["skipped"], 1)
        self.assertEqual(history[1]["succeeded"], ["a"])
        self.assertEqual(history[1]["failed"], ["b"])

    def test_state_is_json_serializable(self):
        import json
        rerun("b1")
        rerun("b1")
        from batchrerun import batches, history
        json.dumps({"batches": batches, "history": history})


class LockTest(unittest.TestCase):
    def setUp(self):
        reset()
        register({"id": "b1", "tasks": [
            {"name": "a", "ok": True},
            {"name": "b", "ok": False},
        ]})

    def test_concurrent_rerun_returns_running(self):
        import threading
        import time
        from unittest import mock

        started = threading.Event()

        def slow_execute(task):
            started.set()
            time.sleep(0.2)
            return bool(task.get("ok"))

        with mock.patch("batchrerun.rerun._execute", side_effect=slow_execute):
            first = threading.Thread(target=rerun, args=("b1",))
            first.start()
            self.assertTrue(started.wait(1))
            second = rerun("b1")
            first.join()
        self.assertEqual(second["status"], "running")
        self.assertEqual(second["batch_id"], "b1")

    def test_lock_released_after_run(self):
        rerun("b1")
        result = rerun("b1")
        self.assertEqual(result["rerun"], 1)


class EdgeCaseTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_unknown_batch_raises(self):
        with self.assertRaises(KeyError):
            rerun("nope")

    def test_register_requires_id(self):
        with self.assertRaises(ValueError):
            register({"tasks": []})

    def test_register_tasks_must_be_list(self):
        with self.assertRaises(ValueError):
            register({"id": "x", "tasks": "nope"})

    def test_all_success_skips_everything(self):
        register({"id": "b2", "tasks": [{"name": "a", "ok": True}]})
        rerun("b2")
        result = rerun("b2")
        self.assertEqual(result["rerun"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_empty_batch(self):
        register({"id": "b3", "tasks": []})
        result = rerun("b3")
        self.assertEqual(result["rerun"], 0)
        self.assertEqual(result["skipped"], 0)

    def test_reregister_resets_partitions(self):
        register({"id": "b4", "tasks": [{"name": "a", "ok": True}]})
        rerun("b4")
        register({"id": "b4", "tasks": [{"name": "a", "ok": True}]})
        result = rerun("b4")
        self.assertEqual(result["rerun"], 1)
        self.assertEqual(result["skipped"], 0)
