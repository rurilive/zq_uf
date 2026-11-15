import unittest

from snapdiff import diff, snapshot


class SnapTest(unittest.TestCase):
    def test_hash_changes_with_script(self):
        self.assertNotEqual(snapshot({"html": "<p>a</p>"})["hash"],
                            snapshot({"html": "<p>a</p><script>x()</script>"})["hash"])

    def test_diff_is_boolean(self):
        a = snapshot({"html": "<p>a</p>"})
        self.assertTrue(diff(a, snapshot({"html": "<p>b</p>"}))["changed"])

    def test_no_blocks(self):
        self.assertEqual(snapshot({"html": ""})["blocks"], [])
