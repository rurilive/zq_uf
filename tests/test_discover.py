import unittest

from formdiscover import FormDiscoverError, discover


PAGE = {
    "action": "/login",
    "method": "POST",
    "inputs": [
        {"name": "user", "type": "text"},
        {"name": "pwd", "type": "password"},
        {"name": "csrf", "type": "hidden", "value": "t1"},
        {"name": "go", "type": "submit"},
    ],
}

STEP2 = {
    "action": "/profile",
    "method": "POST",
    "inputs": [
        {"name": "nickname", "type": "text", "required": True},
        {"name": "age", "type": "number"},
        {"name": "otp_token", "type": "hidden", "value": "n9"},
        {"name": "save", "type": "submit"},
    ],
}

MULTI_PAGE = dict(PAGE, next=STEP2)


class DiscoverTest(unittest.TestCase):
    def test_visible_fields_only(self):
        result = discover(PAGE)
        self.assertEqual([f["name"] for f in result["fields"]], ["user", "pwd"])

    def test_action_and_method(self):
        result = discover(PAGE)
        self.assertEqual(result["action"], "/login")
        self.assertEqual(result["method"], "post")

    def test_single_step(self):
        self.assertNotIn("steps", discover(PAGE))

    def test_hidden_fields_kept(self):
        result = discover(PAGE)
        self.assertEqual([f["name"] for f in result["hidden"]], ["csrf"])
        self.assertEqual(result["hidden"][0]["value"], "t1")
        self.assertEqual(result["hidden"][0]["kind"], "hidden")

    def test_submit_buttons_kept(self):
        result = discover(PAGE)
        self.assertEqual([f["name"] for f in result["submit"]], ["go"])
        self.assertEqual(result["submit"][0]["kind"], "submit")

    def test_csrf_token_detected(self):
        result = discover(PAGE)
        self.assertEqual([f["name"] for f in result["tokens"]], ["csrf"])
        self.assertEqual(result["tokens"][0]["token_kind"], "csrf")
        self.assertTrue(result["hidden"][0]["token"])

    def test_onetime_token_detected(self):
        result = discover(STEP2)
        self.assertEqual([f["name"] for f in result["tokens"]], ["otp_token"])
        self.assertEqual(result["tokens"][0]["token_kind"], "onetime")

    def test_field_types_and_required(self):
        result = discover(STEP2)
        visible = {f["name"]: f for f in result["fields"]}
        self.assertEqual(visible["age"]["type"], "number")
        self.assertEqual(visible["age"]["kind"], "visible")
        self.assertTrue(visible["nickname"]["required"])
        self.assertFalse(visible["age"]["required"])

    def test_default_type_is_text(self):
        page = {"action": "/a", "inputs": [{"name": "q"}]}
        self.assertEqual(discover(page)["fields"][0]["type"], "text")

    def test_multi_step_follows_next(self):
        result = discover(MULTI_PAGE)
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["action"], "/login")
        self.assertEqual(result["steps"][1]["action"], "/profile")
        self.assertEqual(result["steps"][1]["step"], 1)
        self.assertEqual(result["action"], "/login")

    def test_step_limit_raises(self):
        loop = {"action": "/a", "inputs": []}
        loop["next"] = loop
        with self.assertRaises(FormDiscoverError):
            discover(loop, max_steps=3)

    def test_missing_action_raises(self):
        with self.assertRaises(FormDiscoverError):
            discover({"inputs": []})

    def test_bad_input_raises(self):
        with self.assertRaises(FormDiscoverError):
            discover({"action": "/a", "inputs": [{"type": "text"}]})

    def test_bad_method_raises(self):
        with self.assertRaises(FormDiscoverError):
            discover({"action": "/a", "method": "delete", "inputs": []})

    def test_bad_next_raises(self):
        with self.assertRaises(FormDiscoverError):
            discover({"action": "/a", "inputs": [], "next": "/b"})


if __name__ == "__main__":
    unittest.main()
