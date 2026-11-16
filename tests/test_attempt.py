import unittest

from weakpass import attempt, reset


class AttemptTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_success_is_listed(self):
        got = attempt({"user": "alice", "dictionary": ["1", "s3cret"], "valid": "s3cret"})
        self.assertEqual(got["success"], ["s3cret"])

    def test_locked_account_still_tries_all(self):
        got = attempt({"user": "svc", "locked": True, "dictionary": ["a", "b"], "valid": "b"})
        self.assertEqual(got["tried"], ["a", "b"])

    def test_locked_account_has_no_lock_record(self):
        got = attempt({"user": "svc2", "locked": True, "dictionary": ["a"]})
        self.assertEqual(got["locked"], [])

    def test_no_resume_point(self):
        got = attempt({"user": "bob", "dictionary": ["1", "2"]})
        self.assertEqual(got["remaining"], [])

    def test_user_required(self):
        with self.assertRaises(ValueError):
            attempt({"dictionary": ["1"]})
