import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from crawlchain import (
    cleanup,
    export,
    export_detail,
    format_replay,
    query,
    replay,
    start,
)
from crawlchain import store

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK = "task-a"


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_start_returns_id(self):
        self.assertTrue(start(TASK, data_dir=self.dir)["trace"].startswith("t-"))

    def test_stage_recorded(self):
        tr = start(TASK, data_dir=self.dir)
        with tr.stage("fetch"):
            time.sleep(0.01)
        tr.finish()
        report = query(tr["trace"], data_dir=self.dir)
        stage = report["attempts"][0]["stages"][0]
        self.assertEqual(stage["stage"], "fetch")
        self.assertGreater(stage["elapsed_ms"], 0)
        self.assertTrue(stage["ok"])

    def test_retry_reuses_trace_and_merges(self):
        tr = start(TASK, data_dir=self.dir)
        with tr.stage("fetch"):
            pass
        tr.finish(ok=False, error="boom")
        retry = start(TASK, trace_id=tr["trace"], data_dir=self.dir)
        self.assertEqual(retry["attempt"], 2)
        with retry.stage("fetch"):
            pass
        retry.finish()
        report = query(tr["trace"], data_dir=self.dir)
        self.assertEqual([a["attempt"] for a in report["attempts"]], [1, 2])
        self.assertEqual(report["merged"]["fetch"]["attempts"], 2)
        self.assertTrue(report["merged"]["fetch"]["ok"])

    def test_cross_process_same_trace(self):
        tr = start(TASK, data_dir=self.dir)
        subprocess.run(
            [sys.executable, os.path.join(REPO, "tests", "worker.py"),
             TASK, tr["trace"], "1", self.dir],
            check=True, capture_output=True,
        )
        events = store.load(TASK, self.dir)
        pids = {e["pid"] for e in events if e["type"] == "stage_start"}
        self.assertEqual(len(pids), 1)
        self.assertNotEqual(pids.pop(), os.getpid())
        report = query(tr["trace"], data_dir=self.dir)
        self.assertEqual(report["attempts"][0]["stages"][0]["stage"], "fetch")

    def test_failure_report(self):
        tr = start(TASK, data_dir=self.dir)
        with tr.stage("fetch"):
            time.sleep(0.01)
        try:
            with tr.stage("parse"):
                raise ValueError("bad html")
        except ValueError:
            pass
        tr.finish(ok=False, error="bad html")
        report = query(tr["trace"], data_dir=self.dir)
        self.assertEqual(report["failure_point"]["stage"], "parse")
        self.assertIn("bad html", report["failure_point"]["error"])
        self.assertTrue(any(e["stage"] == "parse" for e in report["error_chain"]))
        self.assertEqual(report["slowest"]["stage"], "fetch")

    def test_slow_threshold_configurable(self):
        tr = start(TASK, data_dir=self.dir)
        with tr.stage("store"):
            time.sleep(0.01)
        tr.finish()
        self.assertTrue(query(tr["trace"], data_dir=self.dir, slow_ms=0)["merged"]["store"]["slow"])
        self.assertFalse(query(tr["trace"], data_dir=self.dir, slow_ms=10 ** 9)["merged"]["store"]["slow"])

    def test_cleanup_by_retention(self):
        tr = start(TASK, data_dir=self.dir)
        tr.finish()
        old = {"type": "begin", "trace": "t-old", "task": TASK, "attempt": 1,
               "ts": time.time() - 2 * 86400, "pid": 1}
        store.append(TASK, old, self.dir)
        removed = cleanup(task=TASK, retention_hours=24, data_dir=self.dir)
        self.assertEqual(removed[TASK], 1)
        traces = {e["trace"] for e in store.load(TASK, self.dir)}
        self.assertIn(tr["trace"], traces)
        self.assertNotIn("t-old", traces)

    def test_export(self):
        tr = start(TASK, data_dir=self.dir)
        with tr.stage("fetch"):
            pass
        tr.finish()
        summary = export(tr["trace"], data_dir=self.dir)
        detail = export_detail(tr["trace"], data_dir=self.dir)
        with open(summary, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["trace"], tr["trace"])
        with open(detail, encoding="utf-8") as f:
            self.assertTrue(any(e["type"] == "stage_end" for e in json.load(f)))

    def test_replay_ordered(self):
        tr = start(TASK, data_dir=self.dir)
        with tr.stage("fetch"):
            pass
        tr.finish()
        events = replay(tr["trace"], data_dir=self.dir)
        self.assertEqual([e["type"] for e in events],
                         ["begin", "stage_start", "stage_end", "end"])
        text = format_replay(tr["trace"], data_dir=self.dir)
        self.assertIn("fetch", text)
        self.assertIn("begin", text)


if __name__ == "__main__":
    unittest.main()
