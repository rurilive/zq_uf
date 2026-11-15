import json
import os
import shutil
import tempfile
import unittest

import crawlflow
from crawlflow import resume, run


class FlowTest(unittest.TestCase):
    def test_run_is_linear(self):
        stages = [row["stage"] for row in run({}, {"seed": "a"})["stages"]]
        self.assertEqual(stages, ["fetch", "parse", "store"])

    def test_run_has_no_rerun(self):
        self.assertEqual(run({}, {})["rerun"], [])

    def test_resume_starts_at_first_stage(self):
        self.assertEqual(resume("t1")["stage"], "fetch")


class OrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        crawlflow.set_base_dir(self.tmp)

    def tearDown(self):
        crawlflow.set_base_dir(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dependency_order(self):
        for name in ("root", "left", "right", "join"):
            crawlflow.register_handler(name, lambda c: {})
        flow = {"stages": [
            {"name": "join", "depends_on": ["left", "right"]},
            {"name": "right", "depends_on": ["root"]},
            {"name": "root"},
            {"name": "left", "depends_on": ["root"]},
        ]}
        order = [row["stage"] for row in run(flow, {})["stages"]]
        self.assertLess(order.index("root"), order.index("left"))
        self.assertLess(order.index("root"), order.index("right"))
        self.assertLess(order.index("left"), order.index("join"))
        self.assertLess(order.index("right"), order.index("join"))

    def test_partial_rerun(self):
        state = {"fail": True}
        crawlflow.register_handler("up", lambda c: {"up_out": "u"})

        def mid(context):
            if state["fail"]:
                raise RuntimeError("boom")
            return {"mid_out": "m"}

        crawlflow.register_handler("mid", mid)
        crawlflow.register_handler("down", lambda c: {"down_out": "d"})
        flow = {"stages": [
            {"name": "up", "produces": ["up_out"]},
            {"name": "mid", "depends_on": ["up"], "requires": ["up_out"],
             "produces": ["mid_out"]},
            {"name": "down", "depends_on": ["mid"], "requires": ["mid_out"],
             "produces": ["down_out"]},
        ]}
        first = run(flow, {})
        self.assertEqual(first["status"], "failed")
        rows = {r["stage"]: r["status"] for r in first["stages"]}
        self.assertEqual(rows, {"up": "ok", "mid": "failed", "down": "skipped"})
        up_art = os.path.join(self.tmp, "runs", first["run_id"],
                              "artifacts", "up", "up_out.json")
        with open(up_art, "rb") as fh:
            before = fh.read()

        state["fail"] = False
        second = resume(first["token"])
        self.assertTrue(second["resumed"])
        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["rerun"], ["mid", "down"])
        with open(up_art, "rb") as fh:
            self.assertEqual(fh.read(), before)  # 上游产物未动
        self.assertEqual(
            crawlflow.get_artifact(first["run_id"], "down", "down_out"), "d")
        self.assertEqual(second["stats"]["mid"]["reruns"], 1)
        self.assertEqual(second["stats"]["up"]["reruns"], 0)

    def test_context_missing_field(self):
        crawlflow.register_handler("need", lambda c: {})
        flow = {"stages": [{"name": "need", "requires": ["nope"]}]}
        with self.assertRaises(crawlflow.FlowError) as cm:
            run(flow, {})
        self.assertIn("nope", str(cm.exception))

    def test_context_not_serializable(self):
        with self.assertRaises(crawlflow.FlowError):
            run({}, {"bad": object()})

    def test_resume_expired_token(self):
        crawlflow.register_handler("always_fail", _fail)
        flow = {"stages": [{"name": "always_fail"}]}
        result = run(flow, {}, token_ttl=-1)
        out = resume(result["token"])
        self.assertFalse(out["resumed"])
        self.assertIn("expired", out["error"])

    def test_resume_unknown_token(self):
        out = resume("no-such-token")
        self.assertFalse(out["resumed"])
        self.assertIn("unknown", out["error"])

    def test_cycle_rejected(self):
        flow = {"stages": [
            {"name": "x", "depends_on": ["y"]},
            {"name": "y", "depends_on": ["x"]},
        ]}
        with self.assertRaises(crawlflow.FlowError) as cm:
            run(flow, {})
        self.assertIn("cycle", str(cm.exception))

    def test_unknown_stage_rejected(self):
        with self.assertRaises(crawlflow.FlowError):
            run({"stages": [{"name": "ghost"}]}, {})

    def test_unknown_dependency_rejected(self):
        crawlflow.register_handler("solo", lambda c: {})
        flow = {"stages": [{"name": "solo", "depends_on": ["ghost"]}]}
        with self.assertRaises(crawlflow.FlowError):
            run(flow, {})

    def test_artifacts_and_stats(self):
        result = run({}, {"seed": "s"})
        arts = crawlflow.list_artifacts(result["run_id"])
        self.assertEqual(sorted(arts), ["fetch", "parse", "store"])
        self.assertEqual(
            crawlflow.get_artifact(result["run_id"], "store", "stored"), 1)
        for stage in ("fetch", "parse", "store"):
            self.assertIn("duration", result["stats"][stage])
            self.assertEqual(result["stats"][stage]["reruns"], 0)

    def test_load_flow_from_config(self):
        path = os.path.join(self.tmp, "flow.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"stages": [
                {"name": "fetch"},
                {"name": "parse", "depends_on": ["fetch"]},
            ]}, fh)
        result = run(crawlflow.load_flow(path), {"seed": "x"})
        self.assertEqual([r["stage"] for r in result["stages"]],
                         ["fetch", "parse"])


def _fail(context):
    raise RuntimeError("always fails")


if __name__ == "__main__":
    unittest.main()
