import unittest

from jsonshape import observe, report, reset


class ShapeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_first_observes_without_alert(self):
        self.assertFalse(observe("shop", {"a": 1})["alert"])

    def test_baseline_shared_across_sites(self):
        observe("shop", {"a": 1})
        self.assertTrue(observe("news", {"b": 1})["alert"])

    def test_type_change_ignored(self):
        observe("shop", {"a": 1})
        self.assertFalse(observe("shop", {"a": "x"})["alert"])
