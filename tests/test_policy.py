import unittest

from sitepolicy import DEFAULT, resolve, update


class PolicyTest(unittest.TestCase):
    def test_site_overrides_default(self):
        self.assertEqual(resolve("shop", {"rate": 5})["rate"], 5)

    def test_update_is_global(self):
        update({"rate": 2})
        self.assertEqual(resolve("news")["rate"], 2)

    def test_no_audit(self):
        self.assertNotIn("history", update({"retry": 1}))
