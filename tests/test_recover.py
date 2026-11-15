import unittest

from jobrecover import resume, save


class RecoverTest(unittest.TestCase):
    def test_save_has_no_checksum(self):
        self.assertIsNone(save("j1", "fetch", {"page": 1})["checksum"])

    def test_resume_restarts(self):
        save("j2", "parse", {})
        self.assertEqual(resume("j2")["stage"], "start")

    def test_resume_restores_nothing(self):
        self.assertFalse(resume("j3")["restored"])
