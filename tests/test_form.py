import json
import unittest

from formstate import dumps, loads, start, step


class FormTest(unittest.TestCase):
    def test_step_increments(self):
        state = start("login")
        step(state, {"user": "a"})
        self.assertEqual(state["step"], 1)

    def test_repeat_submits_again(self):
        state = start("login")
        step(state, {"user": "a"})
        step(state, {"user": "a"})
        self.assertEqual(state["step"], 2)

    def test_no_idempotency_key(self):
        self.assertNotIn("idem_key", start("login"))


def signup_form():
    return {
        "name": "signup",
        "steps": [
            {"name": "account", "page": "<p>account</p>"},
            {"name": "captcha", "type": "captcha", "timeout": 60,
             "page": "<p>captcha</p>"},
            {"name": "profile", "page": "<p>profile</p>", "fail_times": 1},
        ],
    }


class ReentrantFormTest(unittest.TestCase):
    def test_duplicate_submit_returns_cached_result(self):
        state = start(signup_form())
        step(state, {"user": "a"})
        step(state, {"user": "a"})
        self.assertEqual(len(state["results"]), 1)
        self.assertEqual(state["current"], 1)
        hit = state["trail"][-1]
        self.assertEqual(hit["status"], "idempotent")
        self.assertTrue(hit["hit"])
        self.assertEqual(hit["idem_key"], state["trail"][0]["idem_key"])

    def test_step_records_name_fingerprint_and_key(self):
        state = start(signup_form())
        step(state, {"user": "a"})
        entry = state["trail"][0]
        self.assertEqual(entry["step"], "account")
        self.assertEqual(len(entry["fingerprint"]), 16)
        self.assertEqual(len(entry["idem_key"]), 16)

    def test_failure_rolls_back_and_keeps_values(self):
        state = start(signup_form())
        step(state, {"user": "a"})
        step(state, {"captcha": "ok"})
        step(state, {"nick": "x"})
        self.assertEqual(state["current"], 1)
        self.assertEqual(state["values"]["nick"], "x")
        failed = [t for t in state["trail"] if t["status"] == "failed"]
        self.assertEqual(failed[0]["fallback_reason"], "submit_failed")
        self.assertEqual(failed[0]["fallback_to"], 1)
        step(state, {"captcha": "ok"})
        step(state, {"nick": "x"})
        self.assertEqual(state["status"], "done")

    def test_captcha_suspends_and_resumes(self):
        state = start(signup_form())
        step(state, {"user": "a"})
        step(state, {})
        self.assertEqual(state["status"], "suspended")
        self.assertEqual(state["pending"]["step"], "captcha")
        step(state, {})
        self.assertEqual(state["status"], "suspended")
        step(state, {"captcha": "9f3"})
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["current"], 2)

    def test_captcha_timeout_marks_failed(self):
        state = start(signup_form())
        step(state, {"user": "a"})
        step(state, {})
        state["pending"]["suspended_at"] -= 3600
        step(state, {"captcha": "late"})
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["trail"][-1]["fallback_reason"], "captcha_timeout")

    def test_serialization_resume_from_breakpoint(self):
        state = start(signup_form())
        step(state, {"user": "a"})
        step(state, {})
        restored = json.loads(json.dumps(state))
        step(restored, {"captcha": "9f3"})
        step(restored, {"nick": "x"})
        self.assertEqual(restored["current"], 1)
        restored = loads(dumps(restored))
        step(restored, {"captcha": "9f3"})
        step(restored, {"nick": "x"})
        self.assertEqual(restored["status"], "done")


if __name__ == "__main__":
    unittest.main()

