import unittest

from exploitchain import report, results, verify
from exploitchain import steps

CHAIN = {
    "id": "chain-7",
    "source": "zhang",
    "steps": [
        {"name": "auth bypass", "pre": ["login"], "input": "/admin", "expect": 200, "actual": 200},
        {"name": "file read", "pre": ["auth bypass"], "input": "/etc/passwd", "expect": "root:", "actual": "root:x:0:0"},
        {"name": "exfil", "pre": ["file read"], "input": "10.0.0.5:9000", "expect": True, "actual": False},
    ],
}


class StepsTest(unittest.TestCase):
    def setUp(self):
        steps._RUNS.clear()

    def test_all_steps_are_checked(self):
        self.assertEqual(len(verify(CHAIN)["steps"]), 3)

    def test_verdict_is_two_way(self):
        self.assertEqual(verify(CHAIN)["verdict"], "unusable")

    def test_failure_point_is_missing(self):
        self.assertIsNone(verify(CHAIN)["failed_at"])

    def test_preconditions_are_dropped(self):
        self.assertEqual(verify(CHAIN)["preconditions"], [])

    def test_source_is_empty(self):
        verify(CHAIN)
        self.assertEqual(results("chain-7")[0]["source"], "")
        self.assertEqual(report()["partial"], 0)
