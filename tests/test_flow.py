import json
import os
import tempfile
import time
import unittest

from loginflow import Step, run


class FlowTest(unittest.TestCase):
    def test_steps_out_of_order(self):
        steps = [
            Step("second", lambda ctx: {"done": ctx["token"] + "!"},
                 expect="done", needs=("token",), provides=("done",)),
            Step("first", lambda ctx: {"token": "t"},
                 expect="token", provides=("token",)),
        ]
        result = run("acme", steps, session_dir=None)
        self.assertTrue(result.ok)
        self.assertEqual([s.name for s in result.steps], ["first", "second"])
        self.assertEqual(result["done"], "t!")

    def test_unsatisfiable_dependency(self):
        steps = [Step("lonely", lambda ctx: {}, needs=("missing",))]
        self.assertRaises(ValueError, run, "acme", steps, None, None)

    def test_captcha_callback(self):
        seen = {}

        def solve(challenge):
            seen.update(challenge)
            return "654321"

        steps = [Step("sms", lambda ctx: {"logged": ctx["captcha_code"] == "654321"},
                      expect="logged", captcha=True)]
        result = run("acme", steps, callbacks={"captcha": solve}, session_dir=None)
        self.assertTrue(result.ok)
        self.assertEqual(seen, {"account": "acme", "step": "sms"})

    def test_captcha_callback_missing(self):
        steps = [Step("sms", lambda ctx: {}, captcha=True)]
        result = run("acme", steps, session_dir=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.steps[0].reason, "captcha_callback_missing")

    def test_session_isolation_and_success_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = [Step("login", lambda ctx: {"token": "t-" + ctx["account"]},
                         expect="token")]
            run("alice", good, session_dir=tmp)
            run("bob", good, session_dir=tmp)
            with open(os.path.join(tmp, "alice.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["token"], "t-alice")
            with open(os.path.join(tmp, "bob.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["token"], "t-bob")
            bad = [Step("login", lambda ctx: {}, expect="token")]
            self.assertFalse(run("carol", bad, session_dir=tmp).ok)
            self.assertFalse(os.path.exists(os.path.join(tmp, "carol.json")))

    def test_failure_reason_and_retries(self):
        def boom(ctx):
            raise RuntimeError("bad password")

        steps = [Step("login", boom, retries=2)]
        result = run("acme", steps, session_dir=None)
        self.assertFalse(result.ok)
        step = result.steps[0]
        self.assertEqual(step.attempts, 3)
        self.assertEqual(step.retries, 2)
        self.assertEqual(step.reason, "exception:RuntimeError")

    def test_timeout_reason(self):
        steps = [Step("slow", lambda ctx: time.sleep(0.2), timeout=0.05)]
        result = run("acme", steps, session_dir=None)
        self.assertFalse(result.ok)
        self.assertTrue(result.steps[0].reason.startswith("timeout>"))

    def test_manual_recovery(self):
        steps = [Step("sms", lambda ctx: {}, expect="logged", on_failure="manual")]
        result = run("acme", steps,
                     callbacks={"manual": lambda name, reason: {"logged": True}},
                     session_dir=None)
        self.assertTrue(result.ok)
        self.assertEqual(result.steps[0].reason, "manual_resolved")


if __name__ == "__main__":
    unittest.main()
