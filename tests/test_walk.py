import unittest

from privpath import reset, walk


OPS = [
    {"op": "read-config", "needs": "user", "effect": "admin"},
    {"op": "dump-db", "needs": "admin", "effect": "root"},
    {"op": "open-shell", "needs": "root", "effect": "host"},
]


class WalkTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_only_direct_operation(self):
        got = walk({"account": "dev", "role": "user", "ops": OPS})
        self.assertEqual(got["reachable"], ["admin"])

    def test_second_hop_not_followed(self):
        got = walk({"account": "dev2", "role": "user", "ops": OPS})
        self.assertNotIn("root", got["reachable"])

    def test_no_preconditions(self):
        got = walk({"account": "dev3", "role": "user", "ops": OPS})
        self.assertEqual(got["preconditions"], [])

    def test_blocked_branch_dropped(self):
        got = walk({"account": "dev4", "role": "user", "ops": OPS})
        self.assertNotIn("dump-db", got["steps"])

    def test_account_required(self):
        with self.assertRaises(ValueError):
            walk({"role": "user"})
