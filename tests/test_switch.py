import unittest

from versionflow import current, reset, switch


class SwitchTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_pointer_changes(self):
        switch("orders", "v1")
        self.assertEqual(current("orders"), "v1")

    def test_single_step(self):
        switch("orders", "v1")
        result = switch("orders", "v2")
        self.assertEqual(result["steps"], ["pointer"])

    def test_no_audit(self):
        result = switch("orders", "v1")
        self.assertNotIn("operator", result)
