import unittest

from crawlpause import is_paused, pause, resume


class PauseTest(unittest.TestCase):
    def test_pause_sets_flag(self):
        pause("t1")
        self.assertTrue(is_paused("t1"))

    def test_resume_clears_flag(self):
        pause("t1")
        resume("t1")
        self.assertFalse(is_paused("t1"))

    def test_pause_returns_task(self):
        self.assertEqual(pause("t1")["task"], "t1")
