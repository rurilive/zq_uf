import unittest

from wmsync import reset, update, watermark


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
