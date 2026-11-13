"""聚合、影响统计、恢复汇总、静默、升级与状态序列化测试。"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from alertmerge import (
    configure,
    dump_state,
    emit,
    load_state,
    reset,
    sent,
    silence,
)

BASE = 1_700_000_000.0


def firing(job, ts, reason="upstream failed", group="nightly"):
    return {"job": job, "state": "firing", "reason": reason,
            "group": group, "time": ts}


def recovered(job, ts, reason="upstream failed", group="nightly"):
    return {"job": job, "state": "recovered", "reason": reason,
            "group": group, "time": ts}


class AggregateTest(unittest.TestCase):
    def setUp(self):
        reset()
        configure(window_seconds=600)

    def test_window_aggregates_to_one(self):
        for index in range(100):
            emit(firing(f"j{index}", BASE + index))
        self.assertEqual(len(sent()), 1)
        row = sent()[0]
        self.assertEqual(row["state"], "firing")
        self.assertEqual(row["reason"], "upstream failed")
        self.assertEqual(row["group"], "nightly")

    def test_distinct_reason_not_merged(self):
        emit(firing("j1", BASE, reason="a"))
        emit(firing("j2", BASE, reason="b"))
        self.assertEqual(len(sent()), 2)

    def test_distinct_group_not_merged(self):
        emit(firing("j1", BASE, group="g1"))
        emit(firing("j2", BASE, group="g2"))
        self.assertEqual(len(sent()), 2)

    def test_new_window_sends_again(self):
        emit(firing("j1", BASE))
        emit(firing("j2", BASE + 599))
        emit(firing("j3", BASE + 600))
        self.assertEqual(len(sent()), 2)

    def test_impact_stats_in_recovery_summary(self):
        for index in range(3):
            emit(firing(f"j{index}", BASE + index * 10))
        emit(recovered("j0", BASE + 100))
        summary = sent()[-1]
        self.assertEqual(summary["state"], "recovered")
        self.assertEqual(summary["jobs"], 3)
        self.assertEqual(summary["first"], BASE)
        self.assertEqual(summary["last"], BASE + 100)

    def test_recovery_summary_dedupes_window(self):
        emit(firing("j1", BASE))
        for index in range(50):
            emit(recovered(f"j{index}", BASE + 700 + index))
        self.assertEqual([row["state"] for row in sent()],
                         ["firing", "recovered"])

    def test_recovery_without_incident_raises(self):
        with self.assertRaises(ValueError):
            emit(recovered("j1", BASE))

    def test_recovery_after_tombstone_expires_raises(self):
        emit(firing("j1", BASE))
        emit(recovered("j1", BASE + 100))
        with self.assertRaises(ValueError):
            emit(recovered("j2", BASE + 100 + 601))

    def test_silence_suppresses_firing_but_not_summary(self):
        silence("upstream failed", "nightly", BASE + 1000)
        for index in range(10):
            emit(firing(f"j{index}", BASE + index))
        self.assertEqual(sent(), [])
        emit(recovered("j0", BASE + 500))
        self.assertEqual(len(sent()), 1)
        self.assertEqual(sent()[0]["jobs"], 10)

    def test_silence_expires(self):
        silence("upstream failed", "nightly", BASE + 10)
        emit(firing("j1", BASE))
        emit(firing("j2", BASE + 700))
        self.assertEqual(len(sent()), 1)

    def test_escalation_once_per_window(self):
        configure(window_seconds=600, escalate_after=3)
        for index in range(10):
            emit(firing(f"j{index}", BASE + index))
        states = [row["state"] for row in sent()]
        self.assertEqual(states, ["firing", "escalated"])
        self.assertEqual(sent()[1]["jobs"], 3)

    def test_out_of_order_timestamps_tracked(self):
        emit(firing("j1", BASE + 50))
        emit(firing("j2", BASE + 10))
        emit(recovered("j1", BASE + 60))
        summary = sent()[-1]
        self.assertEqual(summary["first"], BASE + 10)
        self.assertEqual(summary["last"], BASE + 60)


class StateTest(unittest.TestCase):
    def setUp(self):
        reset()
        configure(window_seconds=600)

    def test_dump_is_json_serializable(self):
        emit(firing("j1", BASE))
        emit(firing("j2", BASE + 1))
        json.dumps(dump_state())

    def test_roundtrip_restores_incident(self):
        emit(firing("j1", BASE))
        emit(firing("j2", BASE + 1))
        snapshot = json.loads(json.dumps(dump_state()))
        reset()
        load_state(snapshot)
        emit(recovered("j1", BASE + 100))
        summary = sent()[-1]
        self.assertEqual(summary["state"], "recovered")
        self.assertEqual(summary["jobs"], 2)
        self.assertEqual(summary["first"], BASE)

    def test_roundtrip_preserves_config_and_silence(self):
        silence("upstream failed", "nightly", BASE + 1000)
        snapshot = json.loads(json.dumps(dump_state()))
        reset()
        load_state(snapshot)
        emit(firing("j1", BASE))
        self.assertEqual(sent(), [])

    def test_load_state_rejects_garbage(self):
        for bad in (None, [], {}, {"config": {}},
                    {"config": {}, "incidents": [{}],
                     "tombstones": [], "silences": []}):
            with self.assertRaises(ValueError):
                load_state(bad)


class ValidationTest(unittest.TestCase):
    def setUp(self):
        reset()
        configure(window_seconds=600)

    def test_missing_job_raises(self):
        with self.assertRaises(ValueError):
            emit({"state": "firing", "time": BASE})

    def test_unknown_state_raises(self):
        with self.assertRaises(ValueError):
            emit({"job": "j1", "state": "flapping", "time": BASE})

    def test_non_numeric_time_raises(self):
        with self.assertRaises(ValueError):
            emit({"job": "j1", "time": "soon"})

    def test_non_dict_alert_raises(self):
        with self.assertRaises(ValueError):
            emit("not a dict")

    def test_bad_configure_raises(self):
        with self.assertRaises(ValueError):
            configure(window_seconds=-1)
        with self.assertRaises(ValueError):
            configure(escalate_after=1)

    def test_bad_silence_raises(self):
        with self.assertRaises(ValueError):
            silence("r", "g", "later")


class DemoTest(unittest.TestCase):
    def test_demo_output_matches_aggregation(self):
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "demo.py"], cwd=root,
            capture_output=True, text=True, check=True)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines[0], "4 条告警")
        self.assertIn("实际发送 3 条", lines[1])
        self.assertTrue(lines[2].startswith("firing 影响任务 1"))
        self.assertTrue(lines[3].startswith("escalated 影响任务 50"))
        self.assertTrue(lines[4].startswith("recovered 影响任务 300"))


if __name__ == "__main__":
    unittest.main()
