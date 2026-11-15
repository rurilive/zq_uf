import unittest

from formstep import (back, begin, copy_to, export, finish,
                      import_conditions, step)


class FormTest(unittest.TestCase):
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


class FlowTest(unittest.TestCase):
    def test_conditions_saved_per_step(self):
        begin("b1")
        r1 = step("b1", {"industry": "it"})
        r2 = step("b1", {"region": "bj"}, token=r1["token"])
        self.assertEqual(r2["step"], 2)
        self.assertEqual(r2["state"], {"industry": "it", "region": "bj"})

    def test_back_invalidates_later_steps(self):
        begin("b2")
        step("b2", {"industry": "it"})
        step("b2", {"region": "bj"})
        r = back("b2", 1)
        self.assertTrue(r["back"])
        self.assertEqual(r["step"], 1)
        self.assertEqual(r["state"], {"industry": "it"})
        r2 = step("b2", {"region": "sh"})
        self.assertEqual(r2["state"], {"industry": "it", "region": "sh"})

    def test_back_refreshes_token(self):
        begin("b3")
        r1 = step("b3", {"a": 1})
        r2 = back("b3", 0)
        self.assertNotEqual(r1["token"], r2["token"])

    def test_duplicate_submit_single_result(self):
        begin("b4")
        r1 = step("b4", {"industry": "it"})
        first = step("b4", {"region": "bj"}, token=r1["token"])
        dup = step("b4", {"region": "bj"}, token=r1["token"])
        self.assertFalse(first["replayed"])
        self.assertTrue(dup["replayed"])
        self.assertEqual(dup["step"], first["step"])
        self.assertEqual(dup["token"], first["token"])
        again = step("b4", {"range": "30d"}, token=first["token"])
        self.assertEqual(again["step"], 3)

    def test_failure_keeps_draft_and_step(self):
        begin("b5")
        r1 = step("b5", {"industry": "it"})
        bad = step("b5", {"region": "bj"}, token=r1["token"], fail=True)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["failed_step"], 1)
        self.assertEqual(bad["failed_condition"], {"region": "bj"})
        self.assertEqual(bad["step"], 1)
        ok = step("b5", {"region": "bj"}, token=r1["token"])
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["step"], 2)

    def test_batches_isolated(self):
        begin("b6")
        begin("b7")
        step("b6", {"industry": "it"})
        self.assertEqual(step("b7", {"region": "bj"})["state"],
                         {"region": "bj"})

    def test_export_import_resumes_step(self):
        begin("b8")
        step("b8", {"industry": "it"})
        step("b8", {"region": "bj"})
        finish("b8")
        saved = export("b8")
        r = import_conditions("b9", saved)
        self.assertTrue(r["imported"])
        self.assertEqual(r["step"], 2)
        self.assertEqual(r["state"], {"industry": "it", "region": "bj"})
        r2 = step("b9", {"range": "30d"})
        self.assertEqual(r2["step"], 3)

    def test_import_does_not_overwrite(self):
        begin("b10")
        step("b10", {"industry": "it"})
        saved = export("b10")
        r = import_conditions("b10", saved)
        self.assertFalse(r["ok"])
        self.assertEqual(step("b10", {"region": "bj"})["step"], 2)

    def test_copy_to_new_batch(self):
        begin("b11")
        step("b11", {"industry": "it"})
        r = copy_to("b11", "b12")
        self.assertEqual(r["state"], {"industry": "it"})
        self.assertEqual(r["step"], 1)
        self.assertFalse(copy_to("b11", "b12")["ok"])


if __name__ == "__main__":
    unittest.main()
