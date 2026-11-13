import unittest

from captchahandoff import CaptchaRequired, challenge, submit


class HandoffTest(unittest.TestCase):
    def test_challenge_raises(self):
        with self.assertRaises(CaptchaRequired) as ctx:
            challenge("acc-1")
        self.assertEqual(ctx.exception.account, "acc-1")

    def test_submit_without_ticket_raises(self):
        with self.assertRaises(CaptchaRequired):
            submit("acc-1", "1234")

    def test_no_ticket_queue(self):
        with self.assertRaises(CaptchaRequired):
            challenge("acc-2")
        with self.assertRaises(CaptchaRequired):
            submit("acc-2", "0000")
