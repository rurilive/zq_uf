import unittest

from exploitchain import replay, report, results, verify
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

ALL_OK = {
    "id": "chain-8",
    "source": "li",
    "steps": [
        {"name": "scan", "pre": [], "input": "10.0.0.0/24", "expect": "up", "actual": "up"},
        {"name": "login", "pre": ["scan"], "input": "admin", "expect": True, "actual": True},
    ],
}

FIRST_FAIL = {
    "id": "chain-9",
    "source": "wang",
    "steps": [
        {"name": "auth bypass", "pre": [], "input": "/admin", "expect": 200, "actual": 403},
        {"name": "file read", "pre": ["auth bypass"], "input": "/etc/passwd", "expect": "root:", "actual": "root:"},
    ],
}


class VerifyTest(unittest.TestCase):
    def setUp(self):
        steps._RUNS.clear()
        steps._DEFS.clear()

    def test_stops_at_first_failure(self):
        entry = verify(CHAIN)
        self.assertEqual(entry["failed_at"], "exfil")
        self.assertEqual([s["step"] for s in entry["steps"]],
                         ["auth bypass", "file read", "exfil"])

    def test_partial_verdict_records_reached_step(self):
        entry = verify(CHAIN)
        self.assertEqual(entry["verdict"], "partial")
        self.assertEqual(entry["reached"], 2)

    def test_all_pass_is_usable(self):
        entry = verify(ALL_OK)
        self.assertEqual(entry["verdict"], "usable")
        self.assertIsNone(entry["failed_at"])
        self.assertEqual(entry["reached"], 2)

    def test_first_step_fail_is_unusable(self):
        entry = verify(FIRST_FAIL)
        self.assertEqual(entry["verdict"], "unusable")
        self.assertEqual(entry["reached"], 0)
        self.assertEqual(len(entry["steps"]), 1)  # 失败即停，后面的步骤不执行

    def test_step_io_and_elapsed_are_recorded(self):
        entry = verify(CHAIN)
        step = entry["steps"][2]
        self.assertEqual(step["input"], "10.0.0.5:9000")
        self.assertEqual(step["expect"], True)
        self.assertEqual(step["actual"], False)  # 失败步骤保留当时的响应
        self.assertGreaterEqual(step["elapsed_ms"], 0.0)

    def test_preconditions_are_kept(self):
        entry = verify(CHAIN)
        self.assertEqual(entry["preconditions"], ["login", "auth bypass", "file read"])
        self.assertEqual(entry["steps"][1]["pre"], ["auth bypass"])

    def test_unmet_precondition_fails_the_step(self):
        chain = {
            "id": "chain-10",
            "source": "zhao",
            "steps": [
                {"name": "a", "pre": [], "input": 1, "expect": 1, "actual": 1},
                {"name": "b", "pre": ["c"], "input": 2, "expect": 2, "actual": 2},
                {"name": "c", "pre": [], "input": 3, "expect": 3, "actual": 3},
            ],
        }
        entry = verify(chain)
        self.assertEqual(entry["failed_at"], "b")
        self.assertIn("precondition not met", entry["steps"][1]["error"])

    def test_source_is_recorded(self):
        verify(CHAIN)
        self.assertEqual(results("chain-7")[0]["source"], "zhang")

    def test_replay_single_step(self):
        verify(CHAIN)
        out = replay("chain-7", "exfil")
        self.assertEqual(out["input"], "10.0.0.5:9000")
        self.assertFalse(out["ok"])
        out = replay("chain-7", "auth bypass")
        self.assertTrue(out["ok"])

    def test_replay_unknown_step_raises(self):
        verify(CHAIN)
        with self.assertRaises(KeyError):
            replay("chain-7", "nope")

    def test_run_callable_is_executed(self):
        chain = {
            "id": "chain-11",
            "source": "qian",
            "steps": [
                {"name": "calc", "pre": [], "input": 3, "expect": 6,
                 "run": lambda x: x * 2},
            ],
        }
        self.assertEqual(verify(chain)["verdict"], "usable")

    def test_run_exception_is_a_failed_step(self):
        def boom(_):
            raise RuntimeError("connection reset")

        chain = {
            "id": "chain-12",
            "source": "qian",
            "steps": [{"name": "exploit", "pre": [], "input": "x", "expect": 1, "run": boom}],
        }
        entry = verify(chain)
        self.assertEqual(entry["verdict"], "unusable")
        self.assertIn("connection reset", entry["steps"][0]["error"])


class ReportTest(unittest.TestCase):
    def setUp(self):
        steps._RUNS.clear()
        steps._DEFS.clear()

    def test_three_way_counts(self):
        verify(CHAIN)      # partial
        verify(ALL_OK)     # usable
        verify(FIRST_FAIL)  # unusable
        rep = report()
        self.assertEqual(rep["chains"], 3)
        self.assertEqual(rep["usable"], 1)
        self.assertEqual(rep["partial"], 1)
        self.assertEqual(rep["unusable"], 1)

    def test_failures_carry_source_and_breakpoint(self):
        verify(CHAIN)
        verify(FIRST_FAIL)
        failures = report()["failures"]
        self.assertEqual(len(failures), 2)
        self.assertEqual(failures[0]["source"], "zhang")
        self.assertEqual(failures[0]["failed_at"], "exfil")
        self.assertEqual(failures[0]["reached"], 2)
        self.assertEqual(failures[1]["source"], "wang")

    def test_results_list_every_chain_with_source(self):
        verify(CHAIN)
        verify(ALL_OK)
        sources = {r["chain"]: r["source"] for r in report()["results"]}
        self.assertEqual(sources, {"chain-7": "zhang", "chain-8": "li"})


if __name__ == "__main__":
    unittest.main()
