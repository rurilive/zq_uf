import unittest

from exploitchain import report, replay, results, verify
from exploitchain import steps


# 前两步通过、第三步失败：用于验证 partial
PARTIAL_CHAIN = {
    "id": "chain-7",
    "source": "zhang",
    "steps": [
        {"name": "auth bypass", "pre": ["login"], "input": "/admin", "expect": 200, "actual": 200},
        {"name": "file read", "pre": ["auth bypass"], "input": "/etc/passwd", "expect": "root:", "actual": "root:"},
        {"name": "exfil", "pre": ["file read"], "input": "10.0.0.5:9000", "expect": True, "actual": False},
    ],
}

# 全部通过
USABLE_CHAIN = {
    "id": "chain-1",
    "source": "li",
    "steps": [
        {"name": "s1", "pre": ["boot"], "input": "a", "expect": 1, "actual": 1},
        {"name": "s2", "pre": ["s1"], "input": "b", "expect": "x", "actual": "x"},
    ],
}

# 第一步就失败
UNUSABLE_CHAIN = {
    "id": "chain-2",
    "source": "wang",
    "steps": [
        {"name": "pwn", "pre": [], "input": "x", "expect": "shell", "actual": "denied"},
        {"name": "post", "pre": ["pwn"], "input": "y", "expect": True, "actual": True},
    ],
}


class VerifyTest(unittest.TestCase):
    def setUp(self):
        steps._RUNS.clear()

    def test_every_step_is_recorded_with_input_output_and_timing(self):
        entry = verify(PARTIAL_CHAIN)
        self.assertEqual([r["step"] for r in entry["steps"]],
                         ["auth bypass", "file read", "exfil"])
        first = entry["steps"][0]
        self.assertEqual(first["input"], "/admin")
        self.assertEqual(first["expect"], 200)
        self.assertEqual(first["response"], 200)
        self.assertIsInstance(first["elapsed_ms"], float)
        self.assertGreaterEqual(first["elapsed_ms"], 0.0)

    def test_chain_stops_at_first_failure_and_rest_are_skipped(self):
        entry = verify(PARTIAL_CHAIN)
        statuses = [r["status"] for r in entry["steps"]]
        self.assertEqual(statuses, ["passed", "passed", "failed"])
        self.assertEqual(entry["failed_at"], "exfil")
        self.assertEqual(entry["reached_step"], "file read")
        self.assertIsNotNone(entry["steps"][2]["reason"])
        self.assertIsNotNone(entry["steps"][2]["response"])
        self.assertIsNotNone(entry["steps"][2]["elapsed_ms"])

    def test_verdict_is_three_way_partial(self):
        entry = verify(PARTIAL_CHAIN)
        self.assertEqual(entry["verdict"], "partial")
        self.assertEqual(entry["reason"],
                         "actual response does not match expectation")

    def test_fully_passing_chain_is_usable(self):
        entry = verify(USABLE_CHAIN)
        self.assertEqual(entry["verdict"], "usable")
        self.assertIsNone(entry["failed_at"])
        self.assertTrue(all(r["ok"] for r in entry["steps"]))

    def test_first_step_failure_is_unusable(self):
        entry = verify(UNUSABLE_CHAIN)
        self.assertEqual(entry["verdict"], "unusable")
        self.assertEqual(entry["failed_at"], "pwn")
        self.assertIsNone(entry["reached_step"])
        self.assertEqual(entry["steps"][1]["status"], "blocked")
        self.assertIn("pwn", entry["steps"][1]["reason"])

    def test_independent_step_after_break_is_skipped(self):
        chain = {
            "id": "chain-4",
            "source": "sun",
            "steps": [
                {"name": "a", "pre": ["boot"], "expect": 1, "actual": 2},
                # 与 a 无依赖关系，断裂后不执行但不算被前置阻断
                {"name": "b", "pre": ["boot"], "expect": 1, "actual": 1},
            ],
        }
        entry = verify(chain)
        self.assertEqual(entry["verdict"], "unusable")
        self.assertEqual(entry["steps"][0]["status"], "failed")
        self.assertEqual(entry["steps"][1]["status"], "skipped")
        self.assertIn("chain stopped", entry["steps"][1]["reason"])

    def test_preconditions_are_checked_and_recorded(self):
        entry = verify(PARTIAL_CHAIN)
        # 链外条件 login：假定满足并登记到链条级前置
        self.assertEqual(entry["preconditions"], [
            {"step": "auth bypass", "requires": "login",
             "kind": "external", "status": "assumed"},
        ])
        # 链内条件 file read 已通过
        pre = {p["requires"]: p["status"]
               for p in entry["steps"][2]["pre"]}
        self.assertEqual(pre, {"file read": "satisfied"})

    def test_blocked_step_records_unsatisfied_precondition(self):
        chain = {
            "id": "chain-3",
            "source": "zhao",
            "steps": [
                {"name": "a", "input": 1, "expect": 1, "actual": 2},
                {"name": "b", "pre": ["a"], "input": 2, "expect": 2, "actual": 2},
            ],
        }
        entry = verify(chain)
        self.assertEqual(entry["verdict"], "unusable")
        blocked = entry["steps"][1]
        self.assertEqual(blocked["status"], "blocked")
        self.assertIsNone(blocked["response"])
        self.assertIsNone(blocked["elapsed_ms"])
        self.assertEqual(blocked["pre"][0]["status"], "unsatisfied")
        self.assertIn("a", blocked["reason"])

    def test_source_is_recorded_per_chain(self):
        verify(PARTIAL_CHAIN)
        self.assertEqual(results("chain-7")[0]["source"], "zhang")

    def test_missing_source_is_rejected(self):
        bad = {"id": "x", "steps": [{"name": "s", "expect": 1, "actual": 1}]}
        with self.assertRaises(ValueError):
            verify(bad)

    def test_forward_precondition_reference_is_rejected(self):
        bad = {
            "id": "x", "source": "q",
            "steps": [
                {"name": "a", "pre": ["b"], "expect": 1, "actual": 1},
                {"name": "b", "expect": 1, "actual": 1},
            ],
        }
        with self.assertRaises(ValueError):
            verify(bad)

    def test_duplicate_step_names_are_rejected(self):
        bad = {
            "id": "x", "source": "q",
            "steps": [
                {"name": "a", "expect": 1, "actual": 1},
                {"name": "a", "expect": 1, "actual": 1},
            ],
        }
        with self.assertRaises(ValueError):
            verify(bad)

    def test_returned_entry_is_a_snapshot(self):
        entry = verify(PARTIAL_CHAIN)
        entry["steps"][0]["response"] = "tampered"
        self.assertEqual(results("chain-7")[0]["steps"][0]["response"], 200)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        steps._RUNS.clear()

    def test_replay_passed_step_recompares_recorded_response(self):
        verify(PARTIAL_CHAIN)
        r = replay("chain-7", "auth bypass")
        self.assertEqual(r["status"], "passed")
        self.assertTrue(r["replayed"])
        self.assertEqual(r["input"], "/admin")
        self.assertEqual(r["response"], 200)
        self.assertIsNotNone(r["elapsed_ms"])

    def test_replay_failed_step_keeps_its_response_and_timing(self):
        verify(PARTIAL_CHAIN)
        r = replay("chain-7", "exfil")
        self.assertEqual(r["status"], "failed")
        self.assertFalse(r["replayed"])
        self.assertEqual(r["response"], False)
        self.assertIsNotNone(r["elapsed_ms"])

    def test_replay_blocked_step_has_no_response(self):
        verify(UNUSABLE_CHAIN)
        r = replay("chain-2", "post")
        self.assertEqual(r["status"], "blocked")
        self.assertIsNone(r["replayed"])
        self.assertIsNone(r["response"])
        self.assertIsNone(r["elapsed_ms"])

    def test_replay_skipped_step_has_no_response(self):
        chain = {
            "id": "chain-4",
            "source": "sun",
            "steps": [
                {"name": "a", "pre": ["boot"], "expect": 1, "actual": 2},
                {"name": "b", "pre": ["boot"], "expect": 1, "actual": 1},
            ],
        }
        verify(chain)
        r = replay("chain-4", "b")
        self.assertEqual(r["status"], "skipped")
        self.assertIsNone(r["replayed"])
        self.assertIsNone(r["response"])
        self.assertIsNone(r["elapsed_ms"])

    def test_replay_unknown_chain_or_step_raises(self):
        verify(PARTIAL_CHAIN)
        with self.assertRaises(ValueError):
            replay("nope", "exfil")
        with self.assertRaises(ValueError):
            replay("chain-7", "nope")


class ReportTest(unittest.TestCase):
    def setUp(self):
        steps._RUNS.clear()

    def test_report_has_three_buckets_and_failure_detail(self):
        verify(USABLE_CHAIN)
        verify(PARTIAL_CHAIN)
        verify(UNUSABLE_CHAIN)
        summary = report()
        self.assertEqual(summary["chains"], 3)
        self.assertEqual(summary["usable"], 1)
        self.assertEqual(summary["partial"], 1)
        self.assertEqual(summary["unusable"], 1)
        by_chain = {f["chain"]: f for f in summary["failures"]}
        self.assertEqual(by_chain["chain-7"]["failed_at"], "exfil")
        self.assertEqual(by_chain["chain-7"]["source"], "zhang")
        self.assertEqual(by_chain["chain-7"]["reached_step"], "file read")
        self.assertEqual(by_chain["chain-2"]["source"], "wang")
        # 可用链条不出现在 failures 里
        self.assertNotIn("chain-1", by_chain)

    def test_results_supports_full_history(self):
        verify(PARTIAL_CHAIN)
        again = dict(PARTIAL_CHAIN)
        again["steps"] = [
            dict(s, actual=s["expect"]) for s in PARTIAL_CHAIN["steps"]
        ]
        verify(again)
        self.assertEqual(len(results("chain-7")), 1)
        self.assertEqual(results("chain-7")[0]["verdict"], "usable")
        history = results("chain-7", all_runs=True)
        self.assertEqual([e["verdict"] for e in history],
                         ["partial", "usable"])


if __name__ == "__main__":
    unittest.main()
