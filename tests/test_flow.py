import unittest

from crawlflow import resume, run


class FlowTest(unittest.TestCase):
    def test_run_is_linear(self):
        stages = [row["stage"] for row in run({}, {"seed": "a"})["stages"]]
        self.assertEqual(stages, ["fetch", "parse", "store"])

    def test_run_has_no_rerun(self):
        self.assertEqual(run({}, {})["rerun"], [])

    def test_resume_starts_at_first_stage(self):
        self.assertEqual(resume("t1")["stage"], "fetch")
