import unittest

from dagprop import rerun, run


class DagTest(unittest.TestCase):
    def test_run_is_linear(self):
        self.assertEqual(run({"nodes": ["a", "b"]})["status"], {"a": "ok", "b": "ok"})

    def test_no_skip(self):
        self.assertEqual(run({"nodes": ["a"]})["skipped"], [])

    def test_rerun_all(self):
        run({"nodes": ["a", "b"]})
        self.assertEqual(rerun(["a"])["rerun"], ["a", "b"])
