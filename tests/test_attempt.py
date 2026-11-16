import unittest

from weakpass import attempt, report, reset


class AttemptTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_success_is_listed(self):
        got = attempt({"user": "alice", "dictionary": ["1", "s3cret"], "valid": "s3cret"})
        self.assertEqual(got["success"], ["s3cret"])
        self.assertEqual(got["missed"], ["1"])

    def test_locked_account_tries_nothing(self):
        got = attempt({"user": "svc", "locked": True, "dictionary": ["a", "b"], "valid": "b", "now": 100})
        self.assertEqual(got["tried"], [])
        self.assertEqual(got["remaining"], ["a", "b"])
        self.assertEqual(got["locked"][0]["locked_at"], 100)
        self.assertTrue(all(s["reason"] == "locked" for s in got["skipped"]))

    def test_resume_from_where_it_stopped(self):
        attempt({"user": "svc", "dictionary": ["a", "b", "c", "d"], "valid": "d",
                 "lock_after": 2, "lock_duration": 60, "now": 100})
        got = attempt({"user": "svc", "valid": "d", "now": 200})
        self.assertEqual(got["tried"], ["c", "d"])
        self.assertEqual(got["success"], ["d"])
        self.assertEqual(got["remaining"], [])

    def test_still_locked_before_locked_until(self):
        attempt({"user": "svc", "dictionary": ["a", "b"], "lock_after": 1,
                 "lock_duration": 60, "now": 100})
        got = attempt({"user": "svc", "now": 150})
        self.assertEqual(got["tried"], [])
        self.assertEqual(got["remaining"], ["b"])

    def test_no_duplicate_within_round(self):
        got = attempt({"user": "bob", "dictionary": ["a", "a", "b"], "valid": "x"})
        self.assertEqual(got["tried"], ["a", "b"])
        self.assertIn({"word": "a", "reason": "duplicate"}, got["skipped"])

    def test_success_survives_new_dictionary(self):
        attempt({"user": "alice", "dictionary": ["s3cret"], "valid": "s3cret"})
        got = attempt({"user": "alice", "dictionary": ["new1", "new2"], "valid": "new1"})
        self.assertEqual(got["tried"], [])
        self.assertEqual(got["success"], ["s3cret"])
        self.assertTrue(all(s["reason"] == "already_success" for s in got["skipped"]))

    def test_missed_not_retried_next_round(self):
        attempt({"user": "bob", "dictionary": ["a", "b"], "valid": "x"})
        got = attempt({"user": "bob", "dictionary": ["a", "c"], "valid": "c"})
        self.assertEqual(got["tried"], ["c"])
        self.assertIn({"word": "a", "reason": "already_tried"}, got["skipped"])

    def test_user_required(self):
        with self.assertRaises(ValueError):
            attempt({"dictionary": ["1"]})


class ReportTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_locked_account_not_counted_as_passed(self):
        attempt({"user": "svc", "locked": True, "dictionary": ["a"], "now": 100})
        summary = report()
        self.assertEqual(summary["passed"], 0)
        self.assertEqual(len(summary["locked"]), 1)
        self.assertEqual(summary["remaining"], 1)

    def test_missed_filterable_and_success_listed(self):
        attempt({"user": "alice", "system": "mail", "dictionary": ["1", "s3cret"], "valid": "s3cret"})
        attempt({"user": "bob", "system": "vpn", "dictionary": ["x"], "valid": "y"})
        summary = report()
        self.assertEqual(summary["success"], [{"account": "alice", "system": "mail", "password": "s3cret"}])
        self.assertEqual(summary["missed"],
                         [{"account": "alice", "system": "mail", "password": "1"},
                          {"account": "bob", "system": "vpn", "password": "x"}])
        self.assertEqual(summary["passed"], 1)

    def test_progress_by_system(self):
        attempt({"user": "alice", "system": "mail", "dictionary": ["s3cret"], "valid": "s3cret"})
        attempt({"user": "svc", "system": "vpn", "locked": True, "dictionary": ["a"], "now": 1})
        summary = report(system="vpn")
        self.assertEqual(summary["accounts"], 1)
        self.assertEqual(summary["systems"]["vpn"]["locked"], 1)
        self.assertEqual(summary["systems"]["mail"] if "mail" in summary["systems"] else None, None)
        full = report()
        self.assertEqual(full["systems"]["mail"]["passed"], 1)


if __name__ == "__main__":
    unittest.main()
