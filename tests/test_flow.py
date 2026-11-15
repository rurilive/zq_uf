import unittest

import retryflow.flow as flow
from retryflow import outcome, records, report, retry


class FlowTest(unittest.TestCase):
    def test_retry_three_times(self):
        for expected in (1, 2, 3):
            result = retry("task-a", "shop")
            self.assertEqual(result["action"], "retry")
            self.assertEqual(result["attempts"], expected)

    def test_give_up_after_three(self):
        for _ in range(3):
            retry("task-b", "shop")
        self.assertEqual(retry("task-b", "shop")["action"], "give_up")

    def test_outcome_counts(self):
        retry("task-c", "shop")
        self.assertEqual(outcome("task-c")["attempts"], 1)


class CircuitTest(unittest.TestCase):
    def setUp(self):
        self._clock = [1000.0]
        self._orig_now = flow._now
        flow._now = lambda: self._clock[0]

    def tearDown(self):
        flow._now = self._orig_now

    def _open_circuit(self, site):
        for i in range(3):
            report("seed-%d" % i, site, False, error="timeout")

    def test_wait_while_circuit_open(self):
        self._open_circuit("storm")
        result = retry("w-1", "storm")
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["reason"], "circuit_open")
        self.assertGreater(result["retry_after"], 0)
        self.assertEqual(outcome("w-1")["attempts"], 0)  # 等待不消耗重试次数

    def test_shared_budget_across_tasks(self):
        site = "ledger"
        for task in ("m-1", "m-2"):
            for _ in range(3):
                self.assertEqual(retry(task, site)["action"], "retry")
        self.assertEqual(retry("m-3", site)["action"], "retry")
        self.assertEqual(retry("m-3", site)["action"], "retry")
        denied = retry("m-3", site)
        self.assertEqual(denied["action"], "give_up")
        self.assertEqual(denied["reason"], "site_budget_exhausted")
        total = sum(outcome(t)["attempts"] for t in ("m-1", "m-2", "m-3"))
        self.assertEqual(total, 8)  # 站点预算共享：8 个令牌跨任务瓜分

    def test_ramp_up_after_recovery(self):
        site = "ramp"
        self._open_circuit(site)
        self.assertEqual(retry("r-0", site)["action"], "wait")
        self._clock[0] += 31  # 过恢复期，进入半开
        first = retry("r-1", site)
        self.assertEqual(first["action"], "retry")
        self.assertEqual(first["circuit"], "half_open")
        limited = retry("r-2", site)
        self.assertEqual(limited["action"], "wait")
        self.assertEqual(limited["reason"], "ramp_limited")  # 第 1 阶只放 1 个
        report("r-1", site, True)
        report("r-1", site, True)  # 连续成功，升到第 2 阶
        self.assertEqual(retry("r-2", site)["action"], "retry")
        self.assertEqual(retry("r-3", site)["action"], "retry")
        self.assertEqual(retry("r-4", site)["action"], "wait")  # 第 2 阶只放 2 个
        report("r-2", site, True)
        report("r-3", site, True)  # 升第 3 阶
        for task in ("r-4", "r-5", "r-6", "r-7"):
            self.assertEqual(retry(task, site)["action"], "retry")
        report("r-4", site, True)
        report("r-5", site, True)  # 走满阶梯，闭环回满
        final = retry("r-8", site)
        self.assertEqual(final["action"], "retry")
        self.assertEqual(final["circuit"], "closed")
        self.assertEqual(final["budget_remaining"], 7)

    def test_failure_during_ramp_reopens(self):
        site = "fragile"
        self._open_circuit(site)
        self._clock[0] += 31
        self.assertEqual(retry("f-1", site)["action"], "retry")
        report("f-1", site, False, error="connection refused")
        again = retry("f-2", site)
        self.assertEqual(again["action"], "wait")
        self.assertEqual(again["reason"], "circuit_open")

    def test_give_up_reason_and_consistent_shape(self):
        site = "shape"
        for _ in range(3):
            retry("g-1", site)
        given_up = retry("g-1", site)
        self.assertEqual(given_up["action"], "give_up")
        self.assertEqual(given_up["reason"], "max_attempts")
        self._open_circuit(site)
        waiting = retry("g-2", site)
        self.assertEqual(waiting["action"], "wait")
        self.assertEqual(set(waiting), set(given_up))  # 各分支返回结构一致

    def test_records_and_error_paths(self):
        self._open_circuit("audit")
        retry("a-1", "audit")
        actions = [rec["action"] for rec in records()]
        self.assertIn("circuit_open", actions)
        self.assertIn("wait", actions)
        with self.assertRaises(ValueError):
            retry("", "audit")
        with self.assertRaises(TypeError):
            retry("a-1", None)
        with self.assertRaises(TypeError):
            report("a-1", "audit", "yes")
        with self.assertRaises(ValueError):
            outcome("")


if __name__ == "__main__":
    unittest.main()
