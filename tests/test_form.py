import unittest

from formstep import begin, step, back, finish, export, import_flow, copy_flow


class FormTest(unittest.TestCase):
    def test_begin_token(self):
        self.assertEqual(begin("t1")["token"], "t1-0")

    def test_step_saves_conditions_per_batch(self):
        begin("t2")
        r = step("t2", {"industry": "it"})
        self.assertEqual(r["step"], 1)
        self.assertEqual(r["state"], [{"industry": "it"}])
        r = step("t2", {"region": "bj"})
        self.assertEqual(r["state"], [{"industry": "it"}, {"region": "bj"}])

    def test_duplicate_submit_replayed_single_result(self):
        r = begin("t3")
        step("t3", {"industry": "it"}, token=r["token"])
        dup = step("t3", {"industry": "it"}, token=r["token"])
        self.assertTrue(dup["replayed"])
        self.assertEqual(dup["state"], [{"industry": "it"}])
        self.assertEqual(dup["step"], 1)

    def test_back_invalidates_later_keeps_earlier(self):
        begin("t4")
        step("t4", {"industry": "it"})
        step("t4", {"region": "bj"})
        step("t4", {"time": "2026"})
        r = back("t4", 1)
        self.assertTrue(r["rolled_back"])
        self.assertEqual(r["step"], 1)
        self.assertEqual(r["state"], [{"industry": "it"}])
        self.assertNotEqual(r["token"], "t4-3")  # 令牌重取
        step("t4", {"region": "sh"}, token=r["token"])
        self.assertEqual(begin("t4")["state"], [{"industry": "it"}, {"region": "sh"}])

    def test_failure_keeps_draft_and_resumes(self):
        r = begin("t5")
        bad = step("t5", {"industry": ""}, token=r["token"])
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["failed"], {"step": 0, "field": "industry", "reason": "empty value"})
        self.assertEqual(bad["draft"], {"industry": ""})
        self.assertEqual(bad["step"], 0)
        ok = step("t5", {"industry": "it"}, token=r["token"])  # 原令牌继续
        self.assertTrue(ok["ok"])
        self.assertIsNone(ok["failed"])
        self.assertEqual(ok["step"], 1)

    def test_batches_isolated(self):
        begin("t6a")
        begin("t6b")
        step("t6a", {"industry": "it"})
        self.assertEqual(begin("t6b")["state"], [])
        self.assertEqual(begin("t6a")["step"], 1)

    def test_export_import_resumes_and_no_overwrite(self):
        begin("t7")
        step("t7", {"industry": "it"})
        step("t7", {"region": "bj"})
        finish("t7")
        data = export("t7")
        self.assertTrue(data["done"])
        r = import_flow("t7b", data)
        self.assertEqual(r["step"], 2)  # 从原步骤继续
        self.assertEqual(r["state"], [{"industry": "it"}, {"region": "bj"}])
        with self.assertRaises(ValueError):
            import_flow("t7", data)  # 不覆盖已有批次

    def test_copy_to_new_batch(self):
        begin("t8")
        step("t8", {"industry": "it"})
        r = copy_flow("t8", "t8c")
        self.assertEqual(r["state"], [{"industry": "it"}])
        step("t8c", {"region": "bj"})
        self.assertEqual(begin("t8")["step"], 1)  # 互不影响

    def test_finished_batch_rejects_step(self):
        begin("t9")
        finish("t9")
        r = step("t9", {"a": 1})
        self.assertFalse(r["ok"])
        self.assertEqual(r["failed"]["reason"], "batch finished")


if __name__ == "__main__":
    unittest.main()
