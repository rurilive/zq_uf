# -*- coding: utf-8 -*-
import json
import threading
import unittest

from formstep import (DEFAULT_STEPS, back, begin, copy_flow,
                      export_conditions, import_conditions, reset, step)

SCHEMA = list(DEFAULT_STEPS)


class LegacyTest(unittest.TestCase):
    """无 schema 的通用模式：保留旧接口的行为。"""

    def setUp(self):
        reset()

    def test_begin_token(self):
        self.assertEqual(begin("f1")["token"], "f1-0")

    def test_token_changes_each_step(self):
        begin("f2")
        self.assertNotEqual(step("f2", {})["token"], step("f2", {})["token"])

    def test_state_echoed(self):
        begin("f3")
        self.assertEqual(step("f3", {"a": 1})["state"], {"a": 1})

    def test_never_replayed(self):
        begin("f4")
        self.assertFalse(step("f4", {})["replayed"])


class CollectFlowTest(unittest.TestCase):
    def setUp(self):
        reset()

    def _finish(self, flow="A", values=("制造", "华东", "2026", 3)):
        r = begin(flow, SCHEMA)
        for name, value in zip(SCHEMA, values):
            r = step(flow, {name: value}, token=r["token"])
        return r

    def test_step_position_tracking(self):
        r = begin("A", SCHEMA)
        self.assertEqual(r["step"], 1)
        self.assertFalse(r["rolled_back"])
        self.assertFalse(r["replayed"])
        r = step("A", {"industry": "制造"}, token=r["token"])
        self.assertEqual(r["step"], 2)
        self.assertEqual(r["state"]["industry"], "制造")
        r = step("A", {"region": "华东"}, token=r["token"])
        self.assertEqual(r["step"], 3)

    def test_conditions_accumulate_per_flow(self):
        r = self._finish()
        self.assertTrue(r["finished"])
        self.assertEqual(r["state"],
                         {"industry": "制造", "region": "华东",
                          "time_range": "2026", "crawl": 3})
        self.assertEqual(len(r["conditions"]), 4)

    def test_back_invalidates_later_conditions_and_refetches_page(self):
        r = self._finish()
        gen_before = r["page"]["generation"]
        r = back("A", "region")
        self.assertTrue(r["rolled_back"])
        self.assertEqual(r["step"], 2)
        # 第2步（地区）及其后条件作废，只保留第1步行业
        self.assertEqual(r["state"], {"industry": "制造"})
        self.assertEqual(list(r["conditions"].keys()), [1])
        self.assertNotIn("region", r["state"])
        self.assertGreater(r["page"]["generation"], gen_before)
        self.assertTrue(r["token"].endswith("-r1"))

    def test_back_by_step_number(self):
        self._finish()
        r = back("A", 1)
        self.assertEqual(r["step"], 1)
        self.assertEqual(r["conditions"], {})

    def test_resubmit_after_back_keeps_earlier_conditions(self):
        self._finish()
        r = back("A", 2)
        r = step("A", {"region": "华南"}, token=r["token"])
        self.assertEqual(r["state"]["industry"], "制造")
        self.assertEqual(r["state"]["region"], "华南")

    def test_replay_same_token_returns_same_response_and_result(self):
        r = self._finish()
        token = "A-3"  # 末步成功时消耗的第三步令牌
        again = step("A", {"crawl": 999}, token=token)
        self.assertTrue(again["replayed"])
        self.assertIs(again["result"], r["result"])
        third = step("A", {"crawl": 999}, token=token)
        self.assertTrue(third["replayed"])
        self.assertIs(third["result"], r["result"])

    def test_old_token_dead_after_back(self):
        self._finish()
        r = back("A", 2)
        bad = step("A", {"region": "x"}, token="A-1")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"], "stale_token")
        self.assertEqual(bad["step"], 2)
        self.assertEqual(r["state"], bad["state"])

    def test_failure_keeps_conditions_and_token(self):
        r = begin("A", SCHEMA)
        token = r["token"]
        bad = step("A", {}, token=token)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["step"], 1)
        self.assertEqual(bad["field"], "industry")
        self.assertEqual(bad["error"], "missing_condition")
        self.assertEqual(bad["token"], token)  # 令牌未消耗

        bad2 = step("A", {"industry": ""}, token=token)
        self.assertEqual(bad2["error"], "empty_condition")
        self.assertEqual(bad2["field"], "industry")

        ok = step("A", {"industry": "制造"}, token=token)
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["step"], 2)
        self.assertEqual(ok["state"]["industry"], "制造")

    def test_failure_mid_flow_reports_step_and_field(self):
        r = self._finish(values=("制造", "华东", "2026", 1))
        r = back("A", 3)
        bad = step("A", {"time_range": ""}, token=r["token"])
        self.assertEqual(bad["step"], 3)
        self.assertEqual(bad["field"], "time_range")
        self.assertEqual(bad["state"]["region"], "华东")  # 已选条件仍在
        fixed = step("A", {"time_range": "2025"}, token=r["token"])
        self.assertTrue(fixed["ok"])

    def test_batch_isolation(self):
        r = self._finish("A")
        b = begin("B", SCHEMA)
        b = step("B", {"industry": "零售"}, token=b["token"])
        self.assertIn("region", r["state"])
        self.assertNotIn("region", b["state"])
        self.assertNotIn("A", b["state"]["industry"])
        # 两批次令牌互不干扰
        self.assertEqual(step("A", {}, token="B-0")["error"], "stale_token")

    def test_finished_batch_rejects_new_submission(self):
        r = self._finish()
        bad = step("A", {"crawl": 1}, token=r["token"])
        self.assertEqual(bad["error"], "already_finished")

    def test_export_is_json_serializable_and_import_resumes(self):
        self._finish()
        payload = export_conditions("A")
        blob = json.dumps(payload)
        r = import_conditions(json.loads(blob), "C")
        self.assertTrue(r["ok"])
        self.assertEqual(r["step"], 4)  # 从原步骤（末步）开始
        # 末步抓取已作废，前三步条件保留
        self.assertEqual(r["state"]["industry"], "制造")
        self.assertEqual(r["state"]["region"], "华东")
        self.assertEqual(r["state"]["time_range"], "2026")
        self.assertNotIn("crawl", r["state"])
        self.assertFalse(r["finished"])
        done = step("C", {"crawl": 7}, token=r["token"])
        self.assertTrue(done["finished"])
        self.assertEqual(done["result"]["pages"], 7)

    def test_import_does_not_overwrite_existing_flow(self):
        self._finish("A")
        payload = export_conditions("A")
        again = import_conditions(payload, "A")
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"], "flow_exists")

    def test_mid_flow_import_resumes_at_open_step(self):
        r = begin("A", SCHEMA)
        r = step("A", {"industry": "制造"}, token=r["token"])
        payload = export_conditions("A")
        r2 = import_conditions(payload, "C")
        self.assertEqual(r2["step"], 2)
        r2 = step("C", {"region": "华北"}, token=r2["token"])
        self.assertEqual(r2["state"]["industry"], "制造")
        self.assertEqual(r2["state"]["region"], "华北")

    def test_copy_flow_creates_isolated_batch(self):
        self._finish("A")
        r = copy_flow("A", "D")
        self.assertTrue(r["ok"])
        self.assertEqual(r["step"], 4)
        r = step("D", {"crawl": 1}, token=r["token"])
        self.assertTrue(r["finished"])
        # 源批次不受影响
        self.assertTrue(export_conditions("A")["finished"])

    def test_unknown_flow(self):
        self.assertEqual(step("nope", {})["error"], "unknown_flow")
        self.assertEqual(back("nope", 1)["error"], "unknown_flow")
        self.assertEqual(export_conditions("nope")["error"], "unknown_flow")

    def test_invalid_back_target(self):
        r = begin("A", SCHEMA)
        self.assertEqual(back("A", 2)["error"], "invalid_step")
        self.assertEqual(back("A", "nope")["error"], "invalid_step")
        self.assertEqual(r["token"], "A-0")

    def test_concurrent_duplicate_submit_yields_one_result(self):
        r = begin("A", SCHEMA)
        r = step("A", {"industry": "制造"}, token=r["token"])
        r = step("A", {"region": "华东"}, token=r["token"])
        r = step("A", {"time_range": "2026"}, token=r["token"])
        token = r["token"]  # 末步令牌，多线程同时提交

        responses = []

        def submit():
            responses.append(step("A", {"crawl": 4}, token=token))

        threads = [threading.Thread(target=submit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(responses), 8)
        self.assertEqual(sum(1 for x in responses if not x["replayed"]), 1)
        self.assertEqual(sum(1 for x in responses if x["replayed"]), 7)
        results = {id(x["result"]) for x in responses}
        self.assertEqual(len(results), 1)  # 全部指向同一套结果集


if __name__ == "__main__":
    unittest.main()
