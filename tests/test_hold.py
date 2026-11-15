import json
import os
import tempfile
import unittest

from crawlhold import Request, Task, hold, release


class HoldTest(unittest.TestCase):
    def test_hold_exits_immediately(self):
        result = hold("task-a")
        self.assertEqual(result["phase"], "exited")
        self.assertFalse(result["released"])

    def test_release_does_not_resume(self):
        self.assertFalse(release("task-a")["resumed"])

    def test_task_name_echoed(self):
        self.assertEqual(hold("task-b")["task"], "task-b")


class ResumableHoldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make_task(self, name):
        return Task(name, workdir=self.tmp.name)

    def test_hold_waits_for_inflight_and_writes_checkpoint(self):
        task = self.make_task("t-drain")
        task.in_flight.append(Request("http://a/1"))
        task.progress = 3
        task.pending = ["http://a/2"]
        result = hold(task, timeout=1.0)
        self.assertEqual(result["phase"], "exited")
        self.assertIn("draining", result["phases"])
        self.assertIn("checkpointing", result["phases"])
        self.assertEqual(task.in_flight, [])
        self.assertFalse(result["timed_out"])
        with open(result["checkpoint"], encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["progress"], 3)
        self.assertEqual(data["pending"], ["http://a/2"])

    def test_hold_releases_proxy_quota_and_handles(self):
        task = self.make_task("t-res")
        task.proxies = ["p1", "p2"]
        task.quota = 7
        handle = open(os.path.join(self.tmp.name, "out.txt"), "w")
        task.handles.append(handle)
        result = hold(task)
        self.assertIn("proxy:p1", result["released"])
        self.assertIn("proxy:p2", result["released"])
        self.assertIn("quota:7", result["released"])
        self.assertEqual(task.proxies, [])
        self.assertEqual(task.quota, 0)
        self.assertTrue(handle.closed)
        self.assertEqual(task.handles, [])

    def test_hold_timeout_marks_half_written(self):
        task = self.make_task("t-stuck")
        output = os.path.join(self.tmp.name, "part.bin")
        with open(output, "w") as fh:
            fh.write("half")
        task.in_flight.append(Request("http://a/slow", output=output, stuck=True))
        result = hold(task, timeout=0.05)
        self.assertTrue(result["timed_out"])
        self.assertIn(output, result["partial_files"])
        with open(result["checkpoint"], encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn(output, data["partial_files"])
        self.assertIn("http://a/slow", data["pending"])

    def test_release_resumes_and_cleans_half_written(self):
        task = self.make_task("t-resume")
        output = os.path.join(self.tmp.name, "part.bin")
        with open(output, "w") as fh:
            fh.write("half")
        task.in_flight.append(Request("http://a/slow", output=output, stuck=True))
        task.progress = 5
        hold(task, timeout=0.05)
        result = release(task)
        self.assertTrue(result["resumed"])
        self.assertEqual(result["progress"], 5)
        self.assertIn(output, result["cleaned"])
        self.assertFalse(os.path.exists(output))
        self.assertEqual(task.progress, 5)
        self.assertIn("http://a/slow", task.pending)
        self.assertTrue(task.accepting)

    def test_release_rejects_corrupt_checkpoint(self):
        task = self.make_task("t-corrupt")
        with open(task.checkpoint_path(), "w") as fh:
            fh.write("not json")
        result = release(task)
        self.assertFalse(result["resumed"])
        self.assertIn("error", result)

    def test_release_rejects_foreign_checkpoint(self):
        task = self.make_task("t-foreign")
        with open(task.checkpoint_path(), "w") as fh:
            json.dump({"version": 1, "task": "someone-else", "progress": 0,
                       "pending": [], "partial_files": []}, fh)
        result = release(task)
        self.assertFalse(result["resumed"])
        self.assertIn("error", result)

    def test_hold_is_idempotent(self):
        task = self.make_task("t-idem-hold")
        first = hold(task)
        second = hold(task)
        self.assertEqual(first, second)
        self.assertEqual(first["checkpoint"], second["checkpoint"])

    def test_release_is_idempotent(self):
        task = self.make_task("t-idem-rel")
        task.progress = 2
        hold(task)
        first = release(task)
        second = release(task)
        self.assertTrue(first["resumed"])
        self.assertEqual(first, second)

    def test_progress_matches_across_hold_and_release(self):
        task = self.make_task("t-progress")
        task.progress = 9
        task.pending = ["u1", "u2"]
        hold(task)
        result = release(task)
        self.assertEqual(result["progress"], 9)
        self.assertEqual(task.pending, ["u1", "u2"])
