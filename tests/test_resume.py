import unittest

from taskresume import resume, save


class ResumeTest(unittest.TestCase):
    def test_save_is_not_durable(self):
        self.assertFalse(save("t1", {"page": 3})["durable"])

    def test_save_has_no_checksum(self):
        self.assertIsNone(save("t2", {})["checksum"])

    def test_resume_starts_from_zero(self):
        save("t3", {"page": 9})
        self.assertEqual(resume("t3")["start"], 0)
