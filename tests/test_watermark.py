import json
import unittest

from wmsync import (
    heartbeat,
    late_events,
    reset,
    restore,
    snapshot,
    status,
    update,
    watermark,
)


class WatermarkTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_watermark_is_max(self):
        update("a", 10)
        update("b", 3)
        self.assertEqual(watermark(), 10)

    def test_no_idle_tracking(self):
        update("a", 10)
        self.assertNotIn("idle", update("b", 3))

    def test_no_late_output(self):
        self.assertEqual(watermark(), 0)

    def test_slowest_source_aligned(self):
        update("b", 3)
        update("a", 10)
        self.assertEqual(watermark(), 3)
        update("a", 20)
        self.assertEqual(watermark(), 3)
        update("b", 6)
        self.assertEqual(watermark(), 6)

    def test_idle_heartbeat_advances(self):
        update("a", 10)
        update("b", 40)
        self.assertEqual(watermark(), 10)
        heartbeat(50, idle_timeout=20)
        self.assertEqual(status()["idle"], ["a"])
        self.assertEqual(watermark(), 30)
        update("a", 35)
        self.assertEqual(status()["idle"], [])
        self.assertEqual(watermark(), 35)

    def test_late_side_output(self):
        update("a", 10)
        update("b", 20)
        update("a", 5)
        late = late_events()
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]["source"], "a")
        self.assertEqual(late[0]["ts"], 5)
        self.assertEqual(status()["late_count"], 1)
        self.assertEqual(watermark(), 10)

    def test_restart_recovery(self):
        update("a", 10)
        update("b", 20)
        update("a", 5)
        heartbeat(60, idle_timeout=30)
        snap = json.loads(json.dumps(snapshot()))
        reset()
        self.assertEqual(watermark(), 0)
        restore(snap)
        self.assertEqual(watermark(), 30)
        self.assertEqual(status()["idle"], ["a", "b"])
        self.assertEqual(status()["late_count"], 1)
